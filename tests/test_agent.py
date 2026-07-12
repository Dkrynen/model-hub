from __future__ import annotations

import asyncio
import json

import pytest

from backend.agent import AgentRunner, get_agent, list_agents
from backend.agent.runner import (
    MAX_TOOL_ARGUMENT_CHARS,
    MAX_TOOL_RESULT_CHARS,
    PreparedToolCall,
    _completion_stats,
)
from backend.permission import AlwaysAllowStore, Decision, PermissionEngine, parse_rules
from backend.agent.permissions import FULL_PERMISSIONS, READONLY_PERMISSIONS, Permissions
from backend.plugin.builtins.tools import TOOL_HANDLERS, WRITE_TOOLS
from backend.provider.base import ChatDelta


def test_permissions_from_dict_roundtrip():
    d = FULL_PERMISSIONS.to_dict()
    p = Permissions.from_dict(d)
    assert p.can_write() and p.can_delete() and p.can_run_bash()
    assert p.can_fetch() and p.can_post() and p.can_mcp()


def test_readonly_denies_write():
    p = READONLY_PERMISSIONS
    assert not p.can_write()
    assert not p.can_run_bash()
    assert p.can_read()
    assert not p.allows_tool("write_file", WRITE_TOOLS, set(), set())


def test_full_allows_write():
    assert FULL_PERMISSIONS.allows_tool("write_file", WRITE_TOOLS, set(), set())


def test_list_agents_has_three():
    agents = list_agents()
    names = {a.name for a in agents}
    assert {"build", "plan", "explore"}.issubset(names)


def test_agent_permission_tiers():
    build = get_agent("build")
    plan = get_agent("plan")
    assert build.permissions.can_write()
    assert not plan.permissions.can_write()
    assert build.permissions.can_run_bash()
    assert not plan.permissions.can_run_bash()


def test_runner_no_tool_call(mock_provider, tool_registry):
    agent = get_agent("build")
    agent.model = "mock:1b"
    mock_provider.set_script([ChatDelta(content="hello there", done=True)])
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    async def go():
        return await runner.run("hi")

    import asyncio

    result = asyncio.run(go())
    assert result.error is None
    assert "hello there" in result.content
    assert result.messages[-1]["role"] == "assistant"


def test_runner_done_event_exposes_only_valid_completion_stats(mock_provider):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    mock_provider.set_script([
        ChatDelta(
            content="hello",
            done=True,
            raw={
                "total_duration": 10_000_000,
                "load_duration": 2_000_000,
                "prompt_eval_count": 12,
                "prompt_eval_duration": 3_000_000,
                "eval_count": 7,
                "eval_duration": 5_000_000,
                "message": {"content": "must not leak"},
                "unknown": "must not leak",
                "invalid_count": True,
            },
        )
    ])
    runner = AgentRunner(mock_provider, agent, {}, [])

    result = asyncio.run(runner.run("hi"))
    done = next(event for event in result.events if event["type"] == "done")

    assert {key: done[key] for key in (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    )} == {
        "total_duration": 10_000_000,
        "load_duration": 2_000_000,
        "prompt_eval_count": 12,
        "prompt_eval_duration": 3_000_000,
        "eval_count": 7,
        "eval_duration": 5_000_000,
    }
    assert "message" not in done
    assert "unknown" not in done


def test_runner_ignores_provider_tool_calls_when_agent_has_no_tool_schemas(
    mock_provider,
):
    agent = get_agent("plan")
    agent.name = "ask"
    agent.type = "ask"
    agent.model = "mock:1b"
    agent.tools = []
    call = {
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": "escape.txt", "content": "no"}),
        }
    }
    mock_provider.set_script([
        ChatDelta(content="plain answer", tool_calls=[call], done=True)
    ])
    executed = []
    runner = AgentRunner(
        mock_provider,
        agent,
        {"write_file": lambda args, ctx: executed.append(args) or "unexpected"},
        [],
        max_iterations=1,
    )

    result = asyncio.run(runner.run("answer without tools"))

    assert result.error is None
    assert result.content == "plain answer"
    assert executed == []
    assert not [
        event
        for event in result.events
        if event["type"] in {"tool_calls", "tool_call", "tool_result"}
    ]
    assert mock_provider._calls[0]["tools"] is None


@pytest.mark.parametrize(
    "raw",
    [
        {"total_duration": 10**400},
        {"load_duration": float("nan")},
        {"prompt_eval_duration": float("inf")},
        {"eval_duration": -1},
        {"prompt_eval_count": True},
        {"eval_count": 10**400},
    ],
)
def test_completion_stats_reject_invalid_or_unbounded_numbers(raw):
    assert _completion_stats(raw) == {}


def test_runner_emits_thinking_progress_without_exposing_reasoning_text(
    mock_provider,
):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    mock_provider.set_script([
        ChatDelta(
            content="",
            done=False,
            raw={"message": {"thinking": "private chain of thought"}},
        ),
        ChatDelta(content="final answer", done=True),
    ])
    runner = AgentRunner(mock_provider, agent, {}, [])

    result = asyncio.run(runner.run("think"))

    assert [event["type"] for event in result.events] == [
        "thinking",
        "delta",
        "done",
    ]
    assert "private chain of thought" not in json.dumps(result.events)


def test_runner_executes_tool_call(mock_provider, tool_registry):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": json.dumps({"path": "."})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done listing", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("list files")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is True
    assert ".py" in tool_results[0]["result"] or "backend" in tool_results[0]["result"]


def test_runner_denies_tool_for_readonly_agent(mock_provider, tool_registry):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "write_file", "arguments": json.dumps({"path": "x", "content": "y"})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="ok", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("write a file")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is False
    assert "permission denied" in tool_results[0]["result"]


def test_runner_denies_tool_not_enabled_for_agent(mock_provider, tool_registry):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "web_search", "arguments": json.dumps({"query": "lac"})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="ok", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("search the web")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is False
    assert "not enabled" in tool_results[0]["result"]


def test_runner_prepares_and_freezes_tool_before_approval(
    mock_provider, tool_registry, isolated_home
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "run_bash",
            "arguments": json.dumps({"command": "alpha"}),
        }
    }
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done", done=True),
        ]
    )
    source_target = {"argv": ["alpha"]}
    executed = []
    approvals = []

    def prepare(args, ctx):
        command = str(args["command"])

        def execute():
            executed.append(command)
            return True, f"ran {command}"

        return PreparedToolCall(
            permission_target=f"policy:{command}",
            approval_target=source_target,
            execute=execute,
        )

    async def ask(agent_name, tool_name, target, key):
        from backend.agent.runner import AskResult

        approvals.append((agent_name, tool_name, target, key))
        # Mutating the preparer's source object after preparation must not alter
        # the target already frozen into this approval.
        source_target["argv"][0] = "mutated"
        return AskResult(decision=Decision.ALLOW)

    engine = PermissionEngine(
        rules=parse_rules(
            {"build": {"bash": {"policy:alpha": "allow", "*": "deny"}}}
        ),
        project_id="prepared",
        store=AlwaysAllowStore(),
    )
    runner = AgentRunner(
        mock_provider,
        agent,
        tool_registry["handlers"],
        tool_registry["schemas"],
        permission_engine=engine,
        on_ask=ask,
        tool_preparers={"run_bash": prepare},
        always_ask_tools={"run_bash"},
        max_iterations=1,
    )

    result = asyncio.run(runner.run("run it"))

    assert approvals == [
        ("build", "run_bash", {"argv": ["alpha"]}, "bash")
    ]
    assert executed == ["alpha"]
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result == {
        "type": "tool_result",
        "name": "run_bash",
        "ok": True,
        "result": "ran alpha",
    }


def test_always_ask_without_permission_engine_cannot_persist_remembered_allow(
    mock_provider, tool_registry
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "list_files",
            "arguments": json.dumps({"path": "."}),
        }
    }
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done", done=True),
        ]
    )

    async def ask(*_args):
        from backend.agent.runner import AskResult

        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(
        mock_provider,
        agent,
        tool_registry["handlers"],
        tool_registry["schemas"],
        on_ask=ask,
        always_ask_tools={"list_files"},
        max_iterations=1,
    )

    result = asyncio.run(runner.run("list"))
    tool_result = [event for event in result.events if event["type"] == "tool_result"][0]
    assert tool_result["ok"] is True


def test_runner_bounds_tool_result_before_event_and_model_history(
    mock_provider, tool_registry
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "list_files",
            "arguments": json.dumps({"path": "."}),
        }
    }
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done", done=True),
        ]
    )
    huge = "x" * (MAX_TOOL_RESULT_CHARS + 1024)
    handlers = {**tool_registry["handlers"], "list_files": lambda args, ctx: huge}
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        tool_registry["schemas"],
    )

    result = asyncio.run(runner.run("list"))

    event_result = [e for e in result.events if e["type"] == "tool_result"][0][
        "result"
    ]
    history_result = [
        m
        for m in mock_provider._calls[1]["messages"]
        if m.get("role") == "tool"
    ][0]["content"]
    assert event_result == history_result
    assert len(event_result) <= MAX_TOOL_RESULT_CHARS
    assert event_result.endswith("...[tool result truncated]")


def test_runner_preserves_normal_tool_arguments_in_events_and_history(
    mock_provider, tool_registry
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    raw_args = json.dumps({"path": "."})
    call = {
        "id": "call-normal",
        "type": "function",
        "function": {"name": "list_files", "arguments": raw_args},
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    runner = AgentRunner(
        mock_provider,
        agent,
        tool_registry["handlers"],
        tool_registry["schemas"],
        max_iterations=2,
    )

    result = asyncio.run(runner.run("list"))

    aggregate = [e for e in result.events if e["type"] == "tool_calls"][0]
    parsed = [e for e in result.events if e["type"] == "tool_call"][0]
    history_call = [
        m
        for m in mock_provider._calls[1]["messages"]
        if m.get("role") == "assistant" and m.get("tool_calls")
    ][0]["tool_calls"][0]
    assert aggregate["calls"][0] == call
    assert parsed == {"type": "tool_call", "name": "list_files", "args": {"path": "."}}
    assert history_call == call


@pytest.mark.parametrize("arguments_as_json", [True, False], ids=["json", "object"])
def test_runner_redacts_oversized_tool_arguments_before_events_and_history(
    mock_provider, tool_registry, arguments_as_json
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    secret = "sensitive-tool-payload-" + ("x" * (MAX_TOOL_ARGUMENT_CHARS + 1024))
    arguments = {"path": secret}
    call = {
        "id": "call-oversized",
        "type": "function",
        "function": {
            "name": "list_files",
            "arguments": json.dumps(arguments) if arguments_as_json else arguments,
        },
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    executed: list[dict] = []

    def list_files(args, ctx):
        executed.append(args)
        return "must not execute"

    handlers = {**tool_registry["handlers"], "list_files": list_files}
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        tool_registry["schemas"],
        max_iterations=2,
    )

    result = asyncio.run(runner.run("list"))

    aggregate = [e for e in result.events if e["type"] == "tool_calls"][0]
    parsed = [e for e in result.events if e["type"] == "tool_call"][0]
    rejected = [e for e in result.events if e["type"] == "tool_result"][0]
    history_call = [
        m
        for m in mock_provider._calls[1]["messages"]
        if m.get("role") == "assistant" and m.get("tool_calls")
    ][0]["tool_calls"][0]

    assert executed == []
    assert rejected["ok"] is False
    assert "arguments exceed" in rejected["result"]
    assert secret not in json.dumps(aggregate)
    assert secret not in json.dumps(parsed)
    assert secret not in json.dumps(history_call)
    assert len(json.dumps(aggregate)) < MAX_TOOL_ARGUMENT_CHARS


def test_runner_rejects_too_many_tool_calls_before_events_or_execution(
    mock_provider, tool_registry
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "list_files",
            "arguments": json.dumps({"path": "."}),
        }
    }
    mock_provider.set_script(
        [ChatDelta(content="", tool_calls=[call] * 65, done=True)]
    )
    executed: list[dict] = []
    handlers = {
        **tool_registry["handlers"],
        "list_files": lambda args, ctx: executed.append(args) or "unexpected",
    }
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        tool_registry["schemas"],
    )

    result = asyncio.run(runner.run("list"))

    assert executed == []
    assert "too many tool calls" in (result.error or "")
    assert not [event for event in result.events if event["type"] == "tool_calls"]


def test_runner_drops_unbounded_tool_envelope_fields_before_events_and_history(
    mock_provider, tool_registry
):
    agent = get_agent("build")
    agent.model = "mock:1b"
    padding = "sensitive-envelope-" + ("x" * (MAX_TOOL_ARGUMENT_CHARS + 1024))
    call = {
        "id": "call-bounded-envelope",
        "type": "function",
        "padding": padding,
        "function": {
            "name": "list_files",
            "arguments": json.dumps({"path": "."}),
            "hidden": padding,
        },
    }
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider,
        agent,
        tool_registry["handlers"],
        tool_registry["schemas"],
    )

    result = asyncio.run(runner.run("list"))

    aggregate = [event for event in result.events if event["type"] == "tool_calls"][0]
    history_call = [
        message
        for message in mock_provider._calls[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ][0]["tool_calls"][0]
    assert padding not in json.dumps(aggregate)
    assert padding not in json.dumps(history_call)
    assert set(history_call) == {"id", "type", "function"}
    assert set(history_call["function"]) == {"name", "arguments"}
