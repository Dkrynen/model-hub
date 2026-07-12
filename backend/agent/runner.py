from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from ..permission import Decision, PermissionEngine
from ..provider.base import ChatDelta, LLMProvider, ProviderError
from ..resilience import FallbackChain, build_default_chain
from .base import Agent
from .permissions import Permissions

ToolHandler = Callable[[dict, dict], str]
RememberAction = Callable[[], Any]
AskCommit = Callable[..., "Decision"]

MAX_TOOL_ARGUMENT_CHARS = 64 * 1024
MAX_TOOL_RESULT_CHARS = 64 * 1024
MAX_TOOL_CALLS_PER_TURN = 64
MAX_TOOL_NAME_CHARS = 128
MAX_TOOL_ID_CHARS = 256
_TOOL_ARGUMENT_ERROR = (
    f"[tool error: arguments exceed {MAX_TOOL_ARGUMENT_CHARS} characters]"
)
_TOOL_ARGUMENT_REDACTION = {
    "_redacted": f"arguments exceeded {MAX_TOOL_ARGUMENT_CHARS} characters"
}
_TOOL_RESULT_TRUNCATION_SUFFIX = "\n...[tool result truncated]"

_COMPLETION_DURATION_KEYS = (
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)
_COMPLETION_COUNT_KEYS = ("prompt_eval_count", "eval_count")
_MAX_COMPLETION_DURATION_NS = 7 * 24 * 60 * 60 * 1_000_000_000
_MAX_COMPLETION_COUNT = 1_000_000_000


def _completion_stats(raw: object) -> dict[str, int | float]:
    """Copy only bounded numeric provider timing fields into public events."""

    if not isinstance(raw, dict):
        return {}
    out: dict[str, int | float] = {}
    for key in _COMPLETION_DURATION_KEYS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if 0 <= value <= _MAX_COMPLETION_DURATION_NS:
            out[key] = value
    for key in _COMPLETION_COUNT_KEYS:
        value = raw.get(key)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_COMPLETION_COUNT
        ):
            out[key] = value
    return out


def _has_thinking_progress(raw: object) -> bool:
    """Detect reasoning progress without exposing hidden reasoning content."""

    if not isinstance(raw, dict):
        return False
    message = raw.get("message")
    if not isinstance(message, dict):
        return False
    thinking = message.get("thinking")
    return isinstance(thinking, str) and bool(thinking)

PreparedExecution = Callable[
    [], tuple[bool, str] | Awaitable[tuple[bool, str]]
]


@dataclass(frozen=True)
class PreparedToolCall:
    """One immutable authorization/execution unit prepared before approval.

    A custom tool preparer should parse and validate mutable model arguments
    once, then close over that parsed value in ``execute``.  The runner copies
    both targets so later mutation of the preparer's source objects cannot
    change what policy evaluated or what the user approved.
    """

    permission_target: Any
    approval_target: Any
    execute: PreparedExecution


ToolPreparer = Callable[[dict, dict], PreparedToolCall]


class _ToolPreparationError(Exception):
    """A fail-closed result produced before permission prompting."""


def _bounded_json_character_count(value: Any, limit: int) -> int:
    """Count JSON characters without first materializing a hostile payload."""

    seen: set[int] = set()

    def string_size(text: str) -> int:
        size = 2
        for character in text:
            code = ord(character)
            if character in ('"', "\\"):
                size += 2
            elif code < 32:
                size += 6
            elif code <= 0x7F:
                size += 1
            elif code <= 0xFFFF:
                size += 6
            else:
                size += 12
            if size > limit:
                return limit + 1
        return size

    def visit(item: Any, depth: int) -> int:
        if depth > 64:
            return limit + 1
        if item is None:
            return 4
        if item is True:
            return 4
        if item is False:
            return 5
        if isinstance(item, str):
            return string_size(item)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            try:
                encoded = json.dumps(item, allow_nan=False)
            except (TypeError, ValueError, OverflowError):
                return limit + 1
            return min(len(encoded), limit + 1)
        if isinstance(item, (list, tuple, dict)):
            identity = id(item)
            if identity in seen:
                return limit + 1
            seen.add(identity)
            try:
                if isinstance(item, dict):
                    size = 2
                    for index, (key, child) in enumerate(item.items()):
                        if not isinstance(key, str):
                            return limit + 1
                        if index:
                            size += 1
                        size += string_size(key) + 1
                        if size > limit:
                            return limit + 1
                        size += visit(child, depth + 1)
                        if size > limit:
                            return limit + 1
                    return size
                size = 2
                for index, child in enumerate(item):
                    if index:
                        size += 1
                    size += visit(child, depth + 1)
                    if size > limit:
                        return limit + 1
                return size
            finally:
                seen.discard(identity)
        return limit + 1

    return visit(value, 0)


@dataclass
class AskResult:
    decision: "Decision"
    remember: bool = False
    _commit: AskCommit | None = field(default=None, repr=False, compare=False)

    def consume(
        self,
        remember_action: RememberAction | None = None,
        rollback_action: RememberAction | None = None,
        grant_id: str | None = None,
        *,
        remember_allowed: bool = True,
    ) -> "Decision":
        """Commit an answer before its decision can authorize tool execution.

        Ordinary callers use the default path, which applies the optional
        remembered permission. The web bridge supplies a commit callback that
        couples permission persistence, audit persistence, and HTTP
        acknowledgement under its run-state lock.
        """

        if not remember_allowed:
            remember_action = None
            rollback_action = None
            grant_id = None
        if self._commit is not None:
            if remember_allowed:
                return self._commit(remember_action, rollback_action, grant_id)
            # New bridges may accept this fourth policy bit so a backend-level
            # never-remember rule remains authoritative even if a stale client
            # requested persistence. Existing bridges retain their 3-argument
            # path whenever the opt-in restriction is not active.
            return self._commit(
                remember_action,
                rollback_action,
                grant_id,
                False,
            )
        if self.decision is Decision.ALLOW and remember_action is not None:
            try:
                remember_action()
            except Exception:
                # SQLite/networked stores can fail after an ambiguous partial
                # write. Best-effort exact rollback keeps a failed approval
                # from silently becoming permanent.
                if rollback_action is not None:
                    rollback_action()
                raise
        return self.decision


AskCallback = Callable[[str, str, Any, str], Awaitable["AskResult | Decision"]]

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
        tool_preparers: dict[str, ToolPreparer] | None = None,
        always_ask_tools: set[str] | frozenset[str] | None = None,
        never_remember_tools: set[str] | frozenset[str] | None = None,
        tool_result_max_chars: int = MAX_TOOL_RESULT_CHARS,
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
        self.tool_preparers = dict(tool_preparers or {})
        self.always_ask_tools = frozenset(always_ask_tools or ())
        self.never_remember_tools = frozenset(never_remember_tools or ())
        try:
            requested_result_limit = int(tool_result_max_chars)
        except (TypeError, ValueError):
            requested_result_limit = MAX_TOOL_RESULT_CHARS
        self.tool_result_max_chars = max(
            1, min(requested_result_limit, MAX_TOOL_RESULT_CHARS)
        )
        self.provider = provider if (isinstance(provider, FallbackChain) or not resilient) else build_default_chain(provider)

        from ..plugin.builtins.tools import WRITE_TOOLS, DELETE_TOOLS, NETWORK_TOOLS

        self._write_tools = WRITE_TOOLS
        self._delete_tools = DELETE_TOOLS
        self._network_tools = NETWORK_TOOLS
        self._filesystem_tools = {"read_file", "write_file", "list_files"} | self._delete_tools
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

    def _canonical_permission_target(self, tool_name: str, target: Any) -> Any:
        """Canonicalize local path policy input without changing handler arguments."""

        if tool_name not in self._filesystem_tools or not isinstance(target, str):
            return target
        base = Path(self.ctx.get("cwd", ".")).resolve()
        path = Path(target)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        try:
            relative = resolved.relative_to(base)
        except ValueError:
            # Handlers retain their own workspace jail, but an outside target
            # must fail before policy evaluation, prompting, or remembering.
            raise ValueError(
                f"filesystem target resolves outside workspace: {target}"
            )
        return relative.as_posix()

    async def _check_permission(
        self,
        tool_name: str,
        permission_target: Any,
        approval_target: Any,
        tool_args: dict,
    ) -> tuple[bool, str]:
        if not tool_name.startswith("mcp_") and tool_name not in set(self.agent.tools):
            return False, f"[permission denied: {tool_name} not enabled for agent '{self.agent.name}']"

        if self.permission_engine is None:
            allowed = self.agent.permissions.allows_tool(
                tool_name, self._write_tools, self._delete_tools, self._network_tools
            )
            if not allowed:
                return False, f"[permission denied: {tool_name} not permitted for agent '{self.agent.name}']"
            if tool_name not in self.always_ask_tools:
                return True, ""
            key = self._perm_key_for(tool_name)
            decision = Decision.ASK
        else:
            base_key = self._perm_key_for(tool_name)
            loop_detected = self.permission_engine.record_tool_call(
                self.agent.name, tool_name, tool_args
            )
            base_decision = self.permission_engine.evaluate(
                self.agent.name,
                base_key,
                permission_target,
                tool_name=tool_name,
            )
            key = base_key
            decision = base_decision
            if loop_detected and base_decision is not Decision.DENY:
                loop_decision = self.permission_engine.evaluate(
                    self.agent.name,
                    "doom_loop",
                    permission_target,
                    tool_name=tool_name,
                )
                if loop_decision is Decision.DENY:
                    key = "doom_loop"
                    decision = Decision.DENY
                else:
                    # The third identical call always asks. A configured ALLOW
                    # may never disable loop protection, and the doom key
                    # prevents the answer from becoming permanent.
                    key = "doom_loop"
                    decision = Decision.ASK
            if tool_name in self.always_ask_tools and decision is not Decision.DENY:
                decision = Decision.ASK
        if decision == Decision.ALLOW:
            return True, ""
        if decision == Decision.DENY:
            return False, f"[permission denied: {tool_name} ({key}) denied for agent '{self.agent.name}']"
        if self.on_ask is not None:
            try:
                result = await self.on_ask(
                    self.agent.name,
                    tool_name,
                    approval_target,
                    key,
                )
                if isinstance(result, Decision):
                    # Backward-compatible callers are treated as allow-once;
                    # only the explicit AskResult contract can persist.
                    result = AskResult(decision=result)
                if not isinstance(result, AskResult):
                    raise TypeError("ask callback must return AskResult")
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            remember_allowed = (
                self.permission_engine is not None
                and tool_name not in self.never_remember_tools
                and key != "doom_loop"
            )
            remember_requested = (
                result.decision is Decision.ALLOW
                and result.remember is True
                and remember_allowed
            )
            remember_action = None
            rollback_action = None
            owner_token = None
            if remember_requested:
                owner_token = self.permission_engine.new_remember_token()
                remember_action = lambda: self.permission_engine.remember(
                    self.agent.name,
                    key,
                    permission_target,
                    tool_name=tool_name,
                    owner_token=owner_token,
                )
                rollback_action = lambda: self.permission_engine.forget_remembered(
                    self.agent.name,
                    key,
                    permission_target,
                    tool_name=tool_name,
                    owner_token=owner_token,
                )
            try:
                final_decision = result.consume(
                    remember_action,
                    rollback_action,
                    owner_token,
                    remember_allowed=remember_allowed,
                )
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            if final_decision == Decision.ALLOW:
                return True, ""
            if final_decision == Decision.DENY:
                return False, f"[permission denied by user: {tool_name}]"
            return False, f"[permission denied: {tool_name} (ask returned no decision)]"
        return False, f"[permission denied: {tool_name} ({key}) requires approval (no ask handler)]"

    def _prepare_tool_call(self, name: str, args: dict) -> PreparedToolCall:
        frozen_args = copy.deepcopy(args)
        preparer = self.tool_preparers.get(name)
        if preparer is not None:
            prepared = preparer(copy.deepcopy(frozen_args), self.ctx)
            if not isinstance(prepared, PreparedToolCall):
                raise TypeError("tool preparer must return PreparedToolCall")
            if not callable(prepared.execute):
                raise TypeError("prepared tool execute must be callable")
            return PreparedToolCall(
                permission_target=copy.deepcopy(prepared.permission_target),
                approval_target=copy.deepcopy(prepared.approval_target),
                execute=prepared.execute,
            )

        if name == "list_files":
            list_path = frozen_args.get("path", ".")
            if not isinstance(list_path, str):
                raise _ToolPreparationError(
                    "[tool error: list_files path must be a string]"
                )
            target = list_path or "."
        else:
            target = (
                frozen_args.get("path")
                or frozen_args.get("command")
                or frozen_args.get("query")
                or frozen_args.get("url")
            )
        if self.permission_engine is not None:
            try:
                target = self._canonical_permission_target(name, target)
            except (OSError, RuntimeError, ValueError) as e:
                raise _ToolPreparationError(
                    f"[permission denied: {name} invalid filesystem target: {e}]"
                ) from e

        if name.startswith("mcp_") and self.mcp is not None:
            parts = name.split("_", 2)

            async def execute_mcp() -> tuple[bool, str]:
                if len(parts) != 3:
                    return False, "[malformed mcp tool name]"
                srv, tool = parts[1], parts[2]
                try:
                    result = await self.mcp.call_tool(srv, tool, frozen_args)
                    text_parts = []
                    for content in getattr(result, "content", []) or []:
                        value = getattr(content, "text", None)
                        if value:
                            text_parts.append(value)
                    return True, "\n".join(text_parts) or "(no content)"
                except Exception as e:
                    return False, f"[mcp error: {e}]"

            execute: PreparedExecution = execute_mcp
        else:
            handler = self.tool_handlers.get(name)

            def execute_local() -> tuple[bool, str]:
                if handler is None:
                    return False, f"[unknown tool: {name}]"
                try:
                    return True, handler(frozen_args, self.ctx)
                except Exception as e:
                    return False, f"[tool error: {e}]"

            execute = execute_local

        return PreparedToolCall(
            permission_target=copy.deepcopy(target),
            approval_target=copy.deepcopy(target),
            execute=execute,
        )

    async def _invoke_prepared(self, prepared: PreparedToolCall) -> tuple[bool, str]:
        try:
            outcome = prepared.execute()
            if inspect.isawaitable(outcome):
                outcome = await outcome
            if not isinstance(outcome, tuple) or len(outcome) != 2:
                raise TypeError("prepared tool execute must return (ok, result)")
            ok, result = outcome
            return bool(ok), str(result)
        except Exception as e:
            return False, f"[tool error: {e}]"

    async def _execute_tool(self, name: str, args: dict) -> tuple[bool, str]:
        if not name.startswith("mcp_") and name not in set(self.agent.tools):
            return False, f"[permission denied: {name} not enabled for agent '{self.agent.name}']"
        try:
            prepared = self._prepare_tool_call(name, args)
            frozen_tool_args = copy.deepcopy(args)
        except _ToolPreparationError as e:
            return False, str(e)
        except Exception as e:
            return False, f"[tool error: failed to prepare {name}: {e}]"

        ok, reason = await self._check_permission(
            name,
            prepared.permission_target,
            prepared.approval_target,
            frozen_tool_args,
        )
        if not ok:
            return False, reason
        return await self._invoke_prepared(prepared)

    def _bound_tool_result(self, result: str) -> str:
        text = str(result)
        if len(text) <= self.tool_result_max_chars:
            return text
        suffix = _TOOL_RESULT_TRUNCATION_SUFFIX
        if self.tool_result_max_chars <= len(suffix):
            return suffix[-self.tool_result_max_chars:]
        keep = self.tool_result_max_chars - len(suffix)
        return text[:keep] + suffix

    @staticmethod
    def _sanitize_tool_call_arguments(call: Any) -> tuple[Any, str | None]:
        """Canonicalize a model tool call before any event or history write."""
        if not isinstance(call, dict):
            return {
                "function": {"name": "<invalid>", "arguments": "{}"}
            }, "[tool error: tool call must be a JSON object]"
        fn = call.get("function", call)
        if not isinstance(fn, dict):
            return {
                "function": {"name": "<invalid>", "arguments": "{}"}
            }, "[tool error: function must be a JSON object]"

        error: str | None = None
        raw_name = fn.get("name", "")
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or len(raw_name) > MAX_TOOL_NAME_CHARS
            or any(ord(character) < 32 for character in raw_name)
        ):
            safe_name = "<invalid>"
            error = "[tool error: tool name must be a bounded non-empty string]"
        else:
            safe_name = raw_name

        raw_args = fn.get("arguments", "{}")
        argument_chars = _bounded_json_character_count(
            raw_args, MAX_TOOL_ARGUMENT_CHARS
        )

        if argument_chars <= MAX_TOOL_ARGUMENT_CHARS:
            safe_args = copy.deepcopy(raw_args)
        else:
            redaction = dict(_TOOL_ARGUMENT_REDACTION)
            safe_args = (
                json.dumps(redaction, separators=(",", ":"))
                if isinstance(raw_args, str)
                else redaction
            )
            error = error or _TOOL_ARGUMENT_ERROR

        safe_fn = {"name": safe_name, "arguments": safe_args}
        if fn is call:
            return safe_fn, error

        safe_call: dict[str, Any] = {"function": safe_fn}
        raw_id = call.get("id")
        if raw_id is not None:
            if (
                isinstance(raw_id, str)
                and len(raw_id) <= MAX_TOOL_ID_CHARS
                and not any(ord(character) < 32 for character in raw_id)
            ):
                safe_call["id"] = raw_id
            else:
                error = error or "[tool error: tool call id is invalid or too long]"
        raw_type = call.get("type")
        if raw_type is not None:
            if raw_type == "function":
                safe_call["type"] = "function"
            else:
                error = error or "[tool error: tool call type must be function]"
        return safe_call, error

    async def run_stream(self, user_text: str, history: list[dict] | None = None) -> AsyncIterator[Event]:
        messages = list(history or [])
        messages.append({"role": "user", "content": user_text})
        schemas = self._enabled_schemas()
        tools_param = schemas if schemas else None

        for i in range(self.max_iterations):
            content = ""
            tool_calls: list[dict] = []
            tool_call_overflow = False
            completion_stats: dict[str, int | float] = {}
            try:
                for delta in self._chat_chain.chat(
                    self.agent.model or "",
                    messages,
                    stream=True,
                    tools=tools_param,
                    system=self.agent.system_prompt or None,
                    **self.chat_options,
                ):
                    if not content and _has_thinking_progress(delta.raw):
                        yield {"type": "thinking"}
                    if delta.content:
                        content += delta.content
                        yield {"type": "delta", "content": delta.content}
                    if delta.tool_calls and tools_param is not None:
                        try:
                            for call in delta.tool_calls:
                                if len(tool_calls) >= MAX_TOOL_CALLS_PER_TURN:
                                    tool_call_overflow = True
                                    break
                                tool_calls.append(call)
                        except TypeError:
                            tool_call_overflow = True
                        if tool_call_overflow:
                            break
                    if delta.done:
                        completion_stats = _completion_stats(delta.raw)
                        break
            except ProviderError as e:
                yield {"type": "error", "message": str(e)}
                return

            if tool_call_overflow:
                yield {
                    "type": "error",
                    "message": (
                        "agent returned too many tool calls "
                        f"(maximum {MAX_TOOL_CALLS_PER_TURN})"
                    ),
                }
                return

            if tool_calls:
                sanitized_calls = [
                    self._sanitize_tool_call_arguments(call) for call in tool_calls
                ]
                safe_tool_calls = [call for call, _error in sanitized_calls]
                asst = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": safe_tool_calls,
                }
                messages.append(asst)
                yield {"type": "tool_calls", "calls": safe_tool_calls}

                for call, argument_error in sanitized_calls:
                    if not isinstance(call, dict):
                        name = "<invalid>"
                        args = {}
                        result = "[tool error: tool call must be a JSON object]"
                        yield {"type": "tool_call", "name": name, "args": args}
                        yield {"type": "tool_result", "name": name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    fn = call.get("function", call)
                    if not isinstance(fn, dict):
                        name = "<invalid>"
                        args = {}
                        result = "[tool error: function must be a JSON object]"
                        yield {"type": "tool_call", "name": name, "args": args}
                        yield {"type": "tool_result", "name": name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    name = fn.get("name", "")
                    if not isinstance(name, str) or not name:
                        safe_name = "<invalid>"
                        args = {}
                        result = "[tool error: tool name must be a non-empty string]"
                        yield {"type": "tool_call", "name": safe_name, "args": args}
                        yield {"type": "tool_result", "name": safe_name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": safe_name, "content": result})
                        continue
                    raw_args = fn.get("arguments", "{}")
                    parse_error = argument_error
                    if argument_error is not None:
                        args = dict(_TOOL_ARGUMENT_REDACTION)
                    else:
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {}
                            parse_error = "[tool error: arguments must be valid JSON]"
                    yield {"type": "tool_call", "name": name, "args": args}
                    if parse_error is not None or not isinstance(args, dict):
                        result = parse_error or "[tool error: arguments must be a JSON object]"
                        yield {
                            "type": "tool_result",
                            "name": name,
                            "ok": False,
                            "result": result,
                        }
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    ok, result = await self._execute_tool(name, args)
                    result = self._bound_tool_result(result)
                    yield {"type": "tool_result", "name": name, "ok": ok, "result": result}
                    messages.append({"role": "tool", "name": name, "content": result})
                continue

            messages.append({"role": "assistant", "content": content})
            yield {
                "type": "done",
                "content": content,
                "messages": messages,
                "iterations": i + 1,
                **completion_stats,
            }
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
