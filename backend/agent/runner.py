from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from ..permission import Decision, PermissionEngine
from ..provider.base import ChatDelta, LLMProvider, ProviderError
from ..resilience import FallbackChain, build_default_chain
from .base import Agent
from .permissions import Permissions

ToolHandler = Callable[[dict, dict], str]


@dataclass
class AskResult:
    decision: "Decision"
    remember: bool = False


AskCallback = Callable[[str, str, str | None, str], Awaitable["AskResult"]]

Event = dict


@dataclass
class RunResult:
    content: str
    messages: list[dict] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider | FallbackChain,
        agent: Agent,
        tool_handlers: dict[str, ToolHandler] | None = None,
        tool_schemas: list[dict] | None = None,
        mcp: Any | None = None,
        ctx: dict | None = None,
        max_iterations: int = 12,
        permission_engine: PermissionEngine | None = None,
        on_ask: AskCallback | None = None,
        chat_options: dict[str, Any] | None = None,
        resilient: bool = True,
    ):
        self.agent = agent
        self.tool_handlers = tool_handlers or {}
        self.tool_schemas = list(tool_schemas or [])
        self.mcp = mcp
        self.ctx = ctx or {}
        self.max_iterations = max_iterations
        self.permission_engine = permission_engine
        self.on_ask = on_ask
        self.chat_options = dict(chat_options or {})
        self.provider = provider if (isinstance(provider, FallbackChain) or not resilient) else build_default_chain(provider)

        from ..plugin.builtins.tools import WRITE_TOOLS, DELETE_TOOLS, NETWORK_TOOLS

        self._write_tools = WRITE_TOOLS
        self._delete_tools = DELETE_TOOLS
        self._network_tools = NETWORK_TOOLS
        self._chat_chain: FallbackChain = build_default_chain(self.provider, "primary")

    def _perm_key_for(self, tool_name: str) -> str:
        if tool_name in {"run_bash"}:
            return "bash"
        if tool_name in self._write_tools:
            return "edit"
        if tool_name in self._network_tools:
            return "webfetch"
        if tool_name in {"read_file"}:
            return "read"
        if tool_name in {"list_files"}:
            return "list"
        return "task"

    def _enabled_schemas(self) -> list[dict]:
        allowed = set(self.agent.tools)
        out = [s for s in self.tool_schemas if s["function"]["name"] in allowed]
        if self.mcp is not None and self.agent.permissions.can_mcp():
            out.extend(self.mcp.tool_schemas_for_agent())
        return out

    async def _check_permission(self, tool_name: str, target: str | None) -> tuple[bool, str]:
        if not tool_name.startswith("mcp_") and tool_name not in set(self.agent.tools):
            return False, f"[permission denied: {tool_name} not enabled for agent '{self.agent.name}']"

        if self.permission_engine is None:
            allowed = self.agent.permissions.allows_tool(
                tool_name, self._write_tools, self._delete_tools, self._network_tools
            )
            if not allowed:
                return False, f"[permission denied: {tool_name} not permitted for agent '{self.agent.name}']"
            return True, ""

        if self.permission_engine.record_tool_call(self.agent.name, tool_name, {"target": target or ""}):
            key = "doom_loop"
        else:
            key = self._perm_key_for(tool_name)
        decision = self.permission_engine.evaluate(self.agent.name, key, target)
        if decision == Decision.ALLOW:
            return True, ""
        if decision == Decision.DENY:
            return False, f"[permission denied: {tool_name} ({key}) denied for agent '{self.agent.name}']"
        if self.on_ask is not None:
            try:
                result = await self.on_ask(self.agent.name, tool_name, target, key)
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            if result.decision == Decision.ALLOW:
                if result.remember and key != "doom_loop":
                    self.permission_engine.remember(self.agent.name, key, target)
                return True, ""
            if result.decision == Decision.DENY:
                return False, f"[permission denied by user: {tool_name}]"
            return False, f"[permission denied: {tool_name} (ask returned no decision)]"
        return False, f"[permission denied: {tool_name} ({key}) requires approval (no ask handler)]"

    async def _execute_tool(self, name: str, args: dict) -> tuple[bool, str]:
        target = args.get("path") or args.get("command") or args.get("query") or args.get("url")
        ok, reason = await self._check_permission(name, target)
        if not ok:
            return False, reason

        if name.startswith("mcp_") and self.mcp is not None:
            parts = name.split("_", 2)
            if len(parts) == 3:
                srv, tool = parts[1], parts[2]
                try:
                    result = await self.mcp.call_tool(srv, tool, args)
                    text_parts = []
                    for c in getattr(result, "content", []) or []:
                        text = getattr(c, "text", None)
                        if text:
                            text_parts.append(text)
                    return True, "\n".join(text_parts) or "(no content)"
                except Exception as e:
                    return False, f"[mcp error: {e}]"
            return False, "[malformed mcp tool name]"

        handler = self.tool_handlers.get(name)
        if handler is None:
            return False, f"[unknown tool: {name}]"
        try:
            return True, handler(args, self.ctx)
        except Exception as e:
            return False, f"[tool error: {e}]"

    async def run_stream(self, user_text: str, history: list[dict] | None = None) -> AsyncIterator[Event]:
        messages = list(history or [])
        messages.append({"role": "user", "content": user_text})
        schemas = self._enabled_schemas()
        tools_param = schemas if schemas else None

        for i in range(self.max_iterations):
            content = ""
            tool_calls: list[dict] = []
            try:
                for delta in self._chat_chain.chat(
                    self.agent.model or "",
                    messages,
                    stream=True,
                    tools=tools_param,
                    system=self.agent.system_prompt or None,
                    **self.chat_options,
                ):
                    if delta.content:
                        content += delta.content
                        yield {"type": "delta", "content": delta.content}
                    if delta.tool_calls:
                        tool_calls.extend(delta.tool_calls)
                    if delta.done:
                        break
            except ProviderError as e:
                yield {"type": "error", "message": str(e)}
                return

            if tool_calls:
                asst = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst)
                yield {"type": "tool_calls", "calls": tool_calls}

                for call in tool_calls:
                    fn = call.get("function", call)
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except json.JSONDecodeError:
                        args = {}
                    yield {"type": "tool_call", "name": name, "args": args}
                    ok, result = await self._execute_tool(name, args)
                    yield {"type": "tool_result", "name": name, "ok": ok, "result": result[:4000]}
                    messages.append({"role": "tool", "name": name, "content": result})
                continue

            messages.append({"role": "assistant", "content": content})
            yield {"type": "done", "content": content, "messages": messages, "iterations": i + 1}
            return

        yield {"type": "error", "message": f"agent exceeded {self.max_iterations} tool iterations"}

    async def run(self, user_text: str, history: list[dict] | None = None) -> RunResult:
        content = ""
        events: list[Event] = []
        messages: list[dict] = []
        async for ev in self.run_stream(user_text, history):
            events.append(ev)
            if ev["type"] == "delta":
                content += ev["content"]
            elif ev["type"] == "done":
                content = ev.get("content", content)
                messages = ev.get("messages", [])
                return RunResult(content=content, messages=messages, events=events, iterations=ev.get("iterations", 0))
            elif ev["type"] == "error":
                return RunResult(content=content, messages=messages, events=events, error=ev["message"])
        return RunResult(content=content, messages=messages, events=events)
