from __future__ import annotations

import asyncio

from backend.permission import (
    AlwaysAllowStore,
    Decision,
    PermissionEngine,
    PermissionRule,
    parse_rules,
    project_id_for,
)
from backend.permission.engine import _match


def test_decision_parse():
    assert Decision.parse("allow") is Decision.ALLOW
    assert Decision.parse("DENY") is Decision.DENY
    assert Decision.parse("ask") is Decision.ASK
    assert Decision.parse("maybe") is Decision.ASK
    assert Decision.parse(True) is Decision.ALLOW


def test_match_wildcards():
    assert _match("src/**", "src/foo/bar.py")
    assert _match("src/**", "src")
    assert _match("*.secret", "api.secret")
    assert not _match("*.secret", "api.py")
    assert _match("*", "anything/here")
    assert _match("a?c", "abc")
    assert not _match("a?c", "abbc")


def test_parse_rules_string_and_dict():
    raw = {
        "build": {"read": "allow", "edit": {"src/**": "allow", "*": "ask"}, "bash": "ask"},
        "plan": {"*": "ask", "read": "allow", "edit": "deny"},
    }
    rules = parse_rules(raw)
    assert len(rules["build"]) == 4
    edit_rules = [r for r in rules["build"] if r.key == "edit"]
    assert any(r.pattern == "src/**" and r.decision is Decision.ALLOW for r in edit_rules)
    assert any(r.pattern == "*" and r.decision is Decision.ASK for r in edit_rules)
    plan_edit = [r for r in rules["plan"] if r.key == "edit"][0]
    assert plan_edit.decision is Decision.DENY


def test_evaluate_specificity_wins(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({
            "build": {"edit": {"src/**": "allow", "*.secret": "deny", "*": "ask"}},
        }),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    assert engine.evaluate("build", "edit", "src/foo.py") is Decision.ALLOW
    assert engine.evaluate("build", "edit", "src/a/b/c.py") is Decision.ALLOW
    assert engine.evaluate("build", "edit", "creds.secret") is Decision.DENY
    assert engine.evaluate("build", "edit", "notes.txt") is Decision.ASK


def test_evaluate_catchall_star_key(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"plan": {"*": "ask", "read": "allow", "edit": "deny"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    assert engine.evaluate("plan", "read", "x") is Decision.ALLOW
    assert engine.evaluate("plan", "edit", "x") is Decision.DENY
    assert engine.evaluate("plan", "bash", "ls") is Decision.ASK
    assert engine.evaluate("plan", "task", None) is Decision.ASK


def test_evaluate_unknown_agent_defaults_ask(isolated_home):
    engine = PermissionEngine(rules=parse_rules({}), project_id="t", store=AlwaysAllowStore())
    assert engine.evaluate("nobody", "read", "x") is Decision.ASK


def test_always_allow_overrides(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"edit": {"*": "ask"}}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    assert engine.evaluate("build", "edit", "a.txt") is Decision.ASK
    engine.remember("build", "edit", "a.txt")
    assert engine.evaluate("build", "edit", "a.txt") is Decision.ALLOW
    assert engine.evaluate("build", "edit", "other.txt") is Decision.ASK


def test_always_allow_persists_across_engines(isolated_home):
    store = AlwaysAllowStore()
    e1 = PermissionEngine(rules=parse_rules({"build": {"edit": {"*": "ask"}}}), project_id="t", store=store)
    e1.remember("build", "edit", "src/x.py")
    e2 = PermissionEngine(rules=parse_rules({"build": {"edit": {"*": "ask"}}}), project_id="t", store=store)
    assert e2.evaluate("build", "edit", "src/x.py") is Decision.ALLOW


def test_always_allow_scoped_per_project(isolated_home):
    store = AlwaysAllowStore()
    e1 = PermissionEngine(rules=parse_rules({"build": {"edit": {"*": "ask"}}}), project_id="projA", store=store)
    e1.remember("build", "edit", "a.py")
    e2 = PermissionEngine(rules=parse_rules({"build": {"edit": {"*": "ask"}}}), project_id="projB", store=store)
    assert e2.evaluate("build", "edit", "a.py") is Decision.ASK


def test_forget_clears_always_allow(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"edit": {"*": "ask"}}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "edit", "a.py")
    assert engine.evaluate("build", "edit", "a.py") is Decision.ALLOW
    engine.forget("build")
    assert engine.evaluate("build", "edit", "a.py") is Decision.ASK


def test_doom_loop_detection(isolated_home):
    engine = PermissionEngine(rules=parse_rules({}), project_id="t", store=AlwaysAllowStore())
    args = {"path": "src/x.py"}
    assert engine.record_tool_call("build", "read_file", args) is False
    assert engine.record_tool_call("build", "read_file", args) is False
    assert engine.record_tool_call("build", "read_file", args) is True
    assert engine.record_tool_call("build", "read_file", {"path": "other.py"}) is False


def test_from_config_loads_apt_rules():
    engine = PermissionEngine.from_config()
    assert engine.evaluate("build", "read", "x") is Decision.ALLOW
    assert engine.evaluate("plan", "edit", "x") is Decision.DENY
    assert engine.evaluate("explore", "webfetch", "http://x") is Decision.ALLOW


def test_project_id_stable():
    a = project_id_for("C:/repo")
    b = project_id_for("C:/repo")
    c = project_id_for("C:/other")
    assert a == b
    assert a != c


def test_runner_uses_engine_deny(mock_provider, tool_registry):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "write_file", "arguments": '{"path":"x","content":"y"}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="ok", done=True)])
    engine = PermissionEngine(rules=parse_rules({"plan": {"edit": "deny"}}), project_id="t", store=AlwaysAllowStore())
    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine)

    result = asyncio.run(runner.run("write file"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is False
    assert "denied" in tool_results[0]["result"]


def test_runner_ask_callback_allows(mock_provider, tool_registry):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent, tool, target, key):
        return AskResult(decision=Decision.ALLOW)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask)
    result = asyncio.run(runner.run("list"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is True


def test_ask_remember_false_does_not_persist(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())
    seen_keys = []

    async def ask(agent_name, tool, target, key):
        seen_keys.append(key)
        return AskResult(decision=Decision.ALLOW, remember=False)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    asyncio.run(runner.run("list"))
    assert seen_keys and seen_keys[0] == "list"
    assert engine.evaluate("build", "list", ".") is Decision.ASK


def test_ask_remember_true_persists(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    asyncio.run(runner.run("list"))
    assert engine.evaluate("build", "list", ".") is Decision.ALLOW


def test_doom_loop_allow_with_remember_never_persists(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    store = AlwaysAllowStore()
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "allow"}}), project_id="t", store=store)
    doom_asks = []

    async def ask(agent_name, tool, target, key):
        doom_asks.append(key)
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=4)
    asyncio.run(runner.run("list"))
    assert "doom_loop" in doom_asks
    assert not store.is_allowed("t", "build", "doom_loop", ".")
    assert engine.evaluate("build", "doom_loop", ".") is Decision.ASK


def test_is_dangerous_covers_ssh_paths():
    from backend.permission.engine import is_dangerous

    assert is_dangerous("write_file", "C:\\Users\\u\\.ssh\\config")
    assert is_dangerous("write_file", "/home/u/.ssh/authorized_keys")
    assert is_dangerous("write_file", "keys/id_ed25519")
    assert is_dangerous("write_file", "backup/id_ecdsa.pub")
    assert is_dangerous("write_file", "id_rsa")
    assert is_dangerous("write_file", "prod/.env")
    assert not is_dangerous("write_file", "src/main.py")
    assert not is_dangerous("write_file", "docs/ssh-guide.md")


def test_runner_uses_bash_permission_key(mock_provider, tool_registry):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "run_bash", "arguments": '{"command":"echo hi"}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"edit": "allow", "bash": "deny"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine)

    result = asyncio.run(runner.run("run echo"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is False
    assert "(bash) denied" in tool_results[0]["result"]


def test_runner_falls_back_to_boolean_permissions_without_engine(mock_provider, tool_registry):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "write_file", "arguments": '{"path":"x","content":"y"}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="ok", done=True)])
    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS)
    result = asyncio.run(runner.run("write"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is False
