from __future__ import annotations

import asyncio
import json

import pytest

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


def test_always_allow_target_is_exact_not_a_glob(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"bash": "allow"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "bash", "cat *")
    assert engine.evaluate("build", "bash", "cat *") is Decision.ALLOW
    assert engine.evaluate("build", "bash", "cat ~/.ssh/id_rsa") is Decision.ASK
    engine.remember("build", "bash", "/bin/echo hi")
    assert engine.store.is_allowed("t", "build", "bash", "/bin/echo hi")
    assert not engine.store.is_allowed("t", "build", "bash", "bin/echo hi")
    engine.remember("build", "bash", r"cat \*")
    assert engine.store.is_allowed("t", "build", "bash", r"cat \*")
    assert not engine.store.is_allowed("t", "build", "bash", "cat /*")


def test_always_allow_refuses_broad_or_dangerous_scope(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"bash": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "bash", None)
    engine.remember("build", "bash", "rm -rf /")
    assert not engine.store.is_allowed("t", "build", "bash", "echo safe")
    assert not engine.store.is_allowed("t", "build", "bash", "rm -rf /")
    assert engine.evaluate("build", "bash", "rm -rf /") is Decision.ASK


def test_remembered_approval_is_scoped_to_the_exact_tool(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"task": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "task", "repo", tool_name="mcp_one_read")
    assert engine.evaluate(
        "build", "task", "repo", tool_name="mcp_one_read"
    ) is Decision.ALLOW
    assert engine.evaluate(
        "build", "task", "repo", tool_name="mcp_two_delete"
    ) is Decision.ASK


def test_hard_deny_beats_a_previously_remembered_target(isolated_home):
    store = AlwaysAllowStore()
    first = PermissionEngine(
        rules=parse_rules({"build": {"edit": "ask"}}),
        project_id="t",
        store=store,
    )
    first.remember("build", "edit", "prod.secret")
    hardened = PermissionEngine(
        rules=parse_rules({"build": {"edit": {"*.secret": "deny", "*": "ask"}}}),
        project_id="t",
        store=store,
    )
    assert hardened.evaluate("build", "edit", "prod.secret") is Decision.DENY


def test_legacy_implicit_allow_rows_are_cleared_once_on_upgrade(tmp_path):
    import sqlite3

    db_path = tmp_path / "permissions.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE always_allow (
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                pattern TEXT NOT NULL,
                decided_at REAL NOT NULL,
                PRIMARY KEY (project_id, agent, key, pattern)
            )"""
        )
        conn.execute(
            "INSERT INTO always_allow VALUES (?,?,?,?,?)",
            ("legacy", "build", "bash", "pytest -q", 1.0),
        )

    store = AlwaysAllowStore(db_path)
    assert not store.is_allowed("legacy", "build", "bash", "pytest -q")

    store.remember("current", "build", "bash", "pytest -q")
    reopened = AlwaysAllowStore(db_path)
    assert reopened.is_allowed("current", "build", "bash", "pytest -q")


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


def test_doom_loop_remember_is_ignored(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"doom_loop": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "doom_loop", ".")
    assert engine.evaluate("build", "doom_loop", ".") is Decision.ASK


def test_from_config_loads_apt_rules(isolated_home):
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


def test_from_config_scopes_unconfigured_explicit_start_directories(
    isolated_home, tmp_path
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_engine = PermissionEngine.from_config(start_dir=first)
    second_engine = PermissionEngine.from_config(start_dir=second)

    assert first_engine.project_id == project_id_for(first.resolve())
    assert second_engine.project_id == project_id_for(second.resolve())
    assert first_engine.project_id != second_engine.project_id


def test_from_config_can_scope_grants_to_sibling_cwds_under_one_project(
    isolated_home, tmp_path
):
    configured = tmp_path / "configured"
    apt_dir = configured / ".apt"
    first = configured / "first"
    second = configured / "second"
    apt_dir.mkdir(parents=True)
    first.mkdir()
    second.mkdir()
    (apt_dir / "apt.jsonc").write_text(
        json.dumps({"permission": {"build": {"edit": "ask"}}}),
        encoding="utf-8",
    )
    store = AlwaysAllowStore()

    project_scoped_first = PermissionEngine.from_config(start_dir=first, store=store)
    project_scoped_second = PermissionEngine.from_config(start_dir=second, store=store)
    ancestor_project_id = project_id_for(configured.resolve())
    assert project_scoped_first.project_id == ancestor_project_id
    assert project_scoped_second.project_id == ancestor_project_id

    first_engine = PermissionEngine.from_config(
        start_dir=first, store=store, permission_scope_root=first
    )
    second_engine = PermissionEngine.from_config(
        start_dir=second, store=store, permission_scope_root=second
    )

    assert first_engine.project_id == project_id_for(first.resolve())
    assert second_engine.project_id == project_id_for(second.resolve())
    assert first_engine.evaluate(
        "build", "edit", "same.txt", tool_name="write_file"
    ) is Decision.ASK
    assert second_engine.evaluate(
        "build", "edit", "same.txt", tool_name="write_file"
    ) is Decision.ASK

    first_engine.remember(
        "build", "edit", "same.txt", tool_name="write_file"
    )

    assert first_engine.evaluate(
        "build", "edit", "same.txt", tool_name="write_file"
    ) is Decision.ALLOW
    assert second_engine.evaluate(
        "build", "edit", "same.txt", tool_name="write_file"
    ) is Decision.ASK


def test_runner_uses_engine_deny(mock_provider, tool_registry, isolated_home):
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


def test_runner_ask_callback_allows(mock_provider, tool_registry, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    called = []

    async def ask(agent, tool, target, key):
        called.append((agent, tool, target, key))
        return AskResult(decision=Decision.ALLOW)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert called == [("build", "list_files", ".", "list")]
    assert tool_results and tool_results[0]["ok"] is True


def test_runner_legacy_decision_callback_is_allow_once(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent_name, tool, target, key):
        return Decision.ALLOW

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    result = asyncio.run(runner.run("list"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is True
    assert engine.evaluate("build", "list", ".") is Decision.ASK


def test_runner_malformed_ask_result_fails_closed(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent_name, tool, target, key):
        return None

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    result = asyncio.run(runner.run("list"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["ok"] is False
    assert "permission ask failed" in tool_results[0]["result"]


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


def test_ask_non_boolean_remember_does_not_persist(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember="false")

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=1)
    asyncio.run(runner.run("list"))
    assert engine.evaluate("build", "list", ".") is Decision.ASK


def test_ask_remember_true_persists(mock_provider, isolated_home):
    import sqlite3

    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    store = AlwaysAllowStore()
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=store,
    )

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    asyncio.run(runner.run("list"))
    assert engine.evaluate("build", "list", ".", tool_name="list_files") is Decision.ALLOW
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM remembered_grant_audits"
        ).fetchone() == (1,)


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


def test_doom_loop_forces_ask_even_when_config_allows(
    mock_provider, isolated_home
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "allow", "doom_loop": "allow"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    asked = []

    async def ask(agent_name, tool, target, key):
        asked.append(key)
        return AskResult(decision=Decision.DENY)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=3,
    )
    asyncio.run(runner.run("list"))
    assert asked == ["doom_loop"]


def test_loop_detection_uses_full_tool_arguments(
    mock_provider, isolated_home, monkeypatch
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    contents = iter(("one", "two", "three"))

    def chat(*args, **kwargs):
        content = next(contents)
        call = {
            "function": {
                "name": "write_file",
                "arguments": json.dumps({"path": "same.txt", "content": content}),
            }
        }
        yield ChatDelta(content="", tool_calls=[call], done=True)

    monkeypatch.setattr(mock_provider, "chat", chat)
    engine = PermissionEngine(
        rules=parse_rules({"build": {"edit": "ask", "doom_loop": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    asked = []

    async def ask(agent_name, tool, target, key):
        asked.append(key)
        return AskResult(decision=Decision.ALLOW)

    runner = AgentRunner(
        mock_provider,
        agent,
        {**TOOL_HANDLERS, "write_file": lambda args, ctx: "ok"},
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=3,
        resilient=False,
    )
    asyncio.run(runner.run("write"))
    assert asked == ["edit", "edit", "edit"]


@pytest.mark.parametrize("doom_decision", ["ask", "allow"])
def test_doom_loop_never_weakens_base_deny(
    mock_provider, isolated_home, doom_decision
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "write_file",
            "arguments": '{"path":"prod.secret","content":"x"}',
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules(
            {
                "build": {
                    "edit": {"*.secret": "deny", "*": "allow"},
                    "doom_loop": doom_decision,
                }
            }
        ),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    asked = []

    async def ask(agent_name, tool, target, key):
        asked.append(key)
        return AskResult(decision=Decision.ALLOW)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=4,
    )
    result = asyncio.run(runner.run("write"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results and all(e["ok"] is False for e in tool_results)
    assert asked == []


def test_is_dangerous_covers_ssh_paths():
    from backend.permission.engine import is_dangerous

    assert is_dangerous("write_file", "C:\\Users\\u\\.ssh\\config")
    assert is_dangerous("write_file", "/home/u/.ssh/authorized_keys")
    assert is_dangerous("write_file", "keys/id_ed25519")
    assert is_dangerous("write_file", "backup/id_ecdsa.pub")
    assert is_dangerous("write_file", "id_rsa")
    assert is_dangerous("write_file", "prod/.env")
    assert is_dangerous("run_bash", "echo key >> ~/.ssh/authorized_keys")
    assert is_dangerous("run_bash", "Set-Content $HOME\\.ssh\\config value")
    assert is_dangerous("run_bash", "cp private-key keys/id_ed25519")
    assert is_dangerous("run_bash", "cat ~/.ssh")
    assert is_dangerous("run_bash", "cat ~/.ssh&&echo ok")
    assert is_dangerous("run_bash", "cp id_ed25519-old backup/")
    assert is_dangerous("run_bash", "type credentials_backup")
    assert is_dangerous("run_bash", "type .env-production")
    assert is_dangerous("run_bash", "cat ~/.git-credentials")
    assert is_dangerous("run_bash", "cat ~/.bash_profile")
    assert is_dangerous("run_bash", "cat ~/.netrc")
    assert is_dangerous("run_bash", "rm -rf $HOME")
    assert is_dangerous("run_bash", "chmod -R 000 secrets")
    assert is_dangerous("run_bash", 123)
    assert is_dangerous("write_file", ["not", "a", "path"])
    assert not is_dangerous("write_file", "src/main.py")
    assert not is_dangerous("write_file", "docs/ssh-guide.md")
    assert not is_dangerous("run_bash", "echo docs/ssh-guide.md")


def test_runner_uses_bash_permission_key(mock_provider, tool_registry, isolated_home):
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


def test_runner_always_ask_tool_overrides_allow(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "run_bash", "arguments": '{"command":"echo hi"}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"bash": "allow"}}),
        project_id="always-ask",
        store=AlwaysAllowStore(),
    )
    asked = []
    executed = []

    async def ask(agent_name, tool, target, key):
        asked.append((agent_name, tool, target, key))
        return AskResult(decision=Decision.ALLOW)

    handlers = {
        **TOOL_HANDLERS,
        "run_bash": lambda args, ctx: executed.append(args["command"]) or "ok",
    }
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        always_ask_tools={"run_bash"},
        max_iterations=1,
    )

    result = asyncio.run(runner.run("run"))

    assert asked == [("build", "run_bash", "echo hi", "bash")]
    assert executed == ["echo hi"]
    assert [e for e in result.events if e["type"] == "tool_result"][0]["ok"] is True


def test_runner_always_ask_never_weakens_hard_deny(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "run_bash", "arguments": '{"command":"echo hi"}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"bash": "deny"}}),
        project_id="always-ask-deny",
        store=AlwaysAllowStore(),
    )
    asked = []
    executed = []

    async def ask(agent_name, tool, target, key):
        asked.append(target)
        return AskResult(decision=Decision.ALLOW)

    handlers = {
        **TOOL_HANDLERS,
        "run_bash": lambda args, ctx: executed.append(args["command"]) or "ok",
    }
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        always_ask_tools={"run_bash"},
        max_iterations=1,
    )

    result = asyncio.run(runner.run("run"))

    assert asked == []
    assert executed == []
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "(bash) denied" in tool_result["result"]


def test_runner_never_remember_tool_ignores_remember_request(
    mock_provider, isolated_home
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    command = "echo safe"
    call = {
        "function": {
            "name": "run_bash",
            "arguments": json.dumps({"command": command}),
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    store = AlwaysAllowStore()
    engine = PermissionEngine(
        rules=parse_rules({"build": {"bash": "ask"}}),
        project_id="never-remember",
        store=store,
    )
    executed = []

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    handlers = {
        **TOOL_HANDLERS,
        "run_bash": lambda args, ctx: executed.append(args["command"]) or "ok",
    }
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        never_remember_tools={"run_bash"},
        max_iterations=1,
    )

    result = asyncio.run(runner.run("run"))

    assert executed == [command]
    assert [e for e in result.events if e["type"] == "tool_result"][0]["ok"] is True
    assert not store.is_allowed(
        "never-remember", "build", "bash@run_bash", command
    )


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


def test_runner_rejects_non_object_tool_arguments(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '["not", "an", "object"]'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results == [
        {
            "type": "tool_result",
            "name": "list_files",
            "ok": False,
            "result": "[tool error: arguments must be a JSON object]",
        }
    ]


def test_forget_remembered_removes_only_exact_tool_approval(isolated_home):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    engine.remember("build", "list", ".", tool_name="list_files")
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ALLOW
    engine.forget_remembered("build", "list", ".", tool_name="list_files")
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ASK


def test_runner_remember_failure_fails_closed(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    class FailingStore(AlwaysAllowStore):
        def remember(self, *args, **kwargs):
            raise OSError("permission database unavailable")

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=FailingStore(),
    )

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "permission database unavailable" in tool_result["result"]


def test_runner_rolls_back_an_ambiguous_remember_failure(
    mock_provider, isolated_home
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    class PartialWriteStore(AlwaysAllowStore):
        def remember(self, *args, **kwargs):
            super().remember(*args, **kwargs)
            raise OSError("failed after write")

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    store = PartialWriteStore()
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=store,
    )

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    asyncio.run(runner.run("list"))
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ASK


@pytest.mark.parametrize("arguments", ["{}", '{"path":""}'])
def test_list_files_default_target_is_canonicalized_for_approval(
    mock_provider, isolated_home, arguments
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": arguments}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    seen = []

    async def ask(agent_name, tool, target, key):
        seen.append(target)
        return AskResult(decision=Decision.DENY)

    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    asyncio.run(runner.run("list"))
    assert seen == ["."]


def test_runner_canonicalizes_filesystem_target_before_rule_ask_and_remember(
    mock_provider, isolated_home, tmp_path
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    project = tmp_path / "project"
    project.mkdir()
    agent = get_agent("build")
    agent.model = "mock:1b"
    raw_target = "src/../outside.txt"
    call = {
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": raw_target, "content": "x"}),
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    store = AlwaysAllowStore()
    engine = PermissionEngine(
        rules=parse_rules(
            {"build": {"edit": {"src/**": "allow", "*": "ask"}}}
        ),
        project_id="t",
        store=store,
    )
    seen = []
    executed = []

    async def ask(agent_name, tool, target, key):
        seen.append((agent_name, tool, target, key))
        return AskResult(decision=Decision.ALLOW, remember=True)

    def write(args, ctx):
        executed.append((dict(args), dict(ctx)))
        return "ok"

    runner = AgentRunner(
        mock_provider,
        agent,
        {**TOOL_HANDLERS, "write_file": write},
        TOOL_SCHEMAS,
        ctx={"cwd": str(project)},
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    asyncio.run(runner.run("write"))

    assert seen == [("build", "write_file", "outside.txt", "edit")]
    assert executed == [
        ({"path": raw_target, "content": "x"}, {"cwd": str(project)})
    ]
    assert store.is_allowed("t", "build", "edit@write_file", "outside.txt")
    assert not store.is_allowed("t", "build", "edit@write_file", raw_target)


def test_runner_denies_filesystem_target_outside_cwd_before_ask(
    mock_provider, isolated_home, tmp_path
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    project = tmp_path / "project"
    project.mkdir()
    agent = get_agent("build")
    agent.model = "mock:1b"
    raw_target = "../escape.txt"
    call = {
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": raw_target, "content": "x"}),
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    engine = PermissionEngine(
        rules=parse_rules({"build": {"edit": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    asked = []
    executed = []

    async def ask(agent_name, tool, target, key):
        asked.append(target)
        return AskResult(decision=Decision.ALLOW, remember=True)

    def write(args, ctx):
        executed.append(args)
        return "ok"

    runner = AgentRunner(
        mock_provider,
        agent,
        {**TOOL_HANDLERS, "write_file": write},
        TOOL_SCHEMAS,
        ctx={"cwd": str(project)},
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("write"))

    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "outside workspace" in tool_result["result"]
    assert asked == []
    assert executed == []


def test_runner_rejects_invalid_json_without_executing_default_tool(
    mock_provider, isolated_home
):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": "{"}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    executed = []
    handlers = {**TOOL_HANDLERS, "list_files": lambda args, ctx: executed.append(args)}
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "valid JSON" in tool_result["result"]
    assert executed == []


@pytest.mark.parametrize(
    "call,error_fragment",
    [
        (None, "tool call must be a JSON object"),
        ({"function": []}, "function must be a JSON object"),
        ({"function": {"name": 7, "arguments": "{}"}}, "tool name must be"),
    ],
)
def test_runner_rejects_malformed_tool_call_envelopes(
    mock_provider, isolated_home, call, error_fragment
):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    runner = AgentRunner(
        mock_provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("malformed"))
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert error_fragment in tool_result["result"]


def test_failed_concurrent_approval_rollback_preserves_existing_owner(
    isolated_home,
):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    first_owner = engine.new_remember_token()
    second_owner = engine.new_remember_token()
    engine.remember(
        "build", "list", ".", tool_name="list_files", owner_token=first_owner
    )
    engine.remember(
        "build", "list", ".", tool_name="list_files", owner_token=second_owner
    )

    engine.forget_remembered(
        "build", "list", ".", tool_name="list_files", owner_token=second_owner
    )
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ALLOW

    engine.forget_remembered(
        "build", "list", ".", tool_name="list_files", owner_token=first_owner
    )
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ASK


def test_successful_concurrent_approval_survives_first_owner_rollback(
    isolated_home,
):
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )
    failing_owner = engine.new_remember_token()
    successful_owner = engine.new_remember_token()
    engine.remember(
        "build", "list", ".", tool_name="list_files", owner_token=failing_owner
    )
    engine.remember(
        "build", "list", ".", tool_name="list_files", owner_token=successful_owner
    )

    engine.forget_remembered(
        "build", "list", ".", tool_name="list_files", owner_token=failing_owner
    )
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ALLOW
    engine.forget_remembered(
        "build",
        "list",
        ".",
        tool_name="list_files",
        owner_token=successful_owner,
    )
    assert engine.evaluate(
        "build", "list", ".", tool_name="list_files"
    ) is Decision.ASK


def test_claim_without_authoritative_grant_audit_is_denied(isolated_home):
    import sqlite3
    import time

    store = AlwaysAllowStore()
    pattern = "exact:."
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO always_allow VALUES (?,?,?,?,?,?)",
            ("t", "build", "list@list_files", pattern, time.time(), "orphan"),
        )
        conn.execute(
            "INSERT INTO always_allow_claims VALUES (?,?,?,?,?,?)",
            ("t", "build", "list@list_files", pattern, "orphan", time.time()),
        )
    assert not store.is_allowed("t", "build", "list@list_files", ".")


def test_authoritative_grant_audit_binds_scope_and_survives_revocation(
    isolated_home,
):
    import sqlite3

    store = AlwaysAllowStore()
    grant_id = store.remember("t", "build", "list@list_files", ".")
    assert grant_id
    assert store.is_allowed("t", "build", "list@list_files", ".")

    with sqlite3.connect(store.db_path) as conn:
        audit = conn.execute(
            """SELECT grant_id, project_id, agent, key, pattern
               FROM remembered_grant_audits WHERE grant_id=?""",
            (grant_id,),
        ).fetchone()
    assert audit == (
        grant_id,
        "t",
        "build",
        "list@list_files",
        "exact:.",
    )

    store.forget_exact(
        "t", "build", "list@list_files", ".", owner_token=grant_id
    )
    assert not store.is_allowed("t", "build", "list@list_files", ".")
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM remembered_grant_audits WHERE grant_id=?",
            (grant_id,),
        ).fetchone() == (1,)


def test_grant_audit_cannot_authorize_a_mismatched_scope(isolated_home):
    import sqlite3
    import time

    store = AlwaysAllowStore()
    grant_id = store.remember("t", "build", "list@list_files", ".")
    assert grant_id
    mismatched_pattern = "exact:other"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO always_allow VALUES (?,?,?,?,?,?)",
            (
                "t",
                "build",
                "list@list_files",
                mismatched_pattern,
                time.time(),
                grant_id,
            ),
        )
        conn.execute(
            "INSERT INTO always_allow_claims VALUES (?,?,?,?,?,?)",
            (
                "t",
                "build",
                "list@list_files",
                mismatched_pattern,
                grant_id,
                time.time(),
            ),
        )
    assert not store.is_allowed("t", "build", "list@list_files", "other")


def test_grant_audit_requires_matching_tool_and_schema(isolated_home):
    import sqlite3

    store = AlwaysAllowStore()
    grant_id = store.remember("t", "build", "list@list_files", ".")
    assert grant_id
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """UPDATE remembered_grant_audits
               SET tool_name='run_bash', schema_version=999
               WHERE grant_id=?""",
            (grant_id,),
        )
    assert not store.is_allowed("t", "build", "list@list_files", ".")


def test_authoritative_grant_audit_survives_session_deletion(isolated_home):
    import sqlite3

    from backend.cookbook import persistence

    store = AlwaysAllowStore()
    grant_id = store.remember("t", "build", "list@list_files", ".")
    session_id = persistence.create_session(model="mock:1b")
    persistence.add_session_event(
        session_id,
        "ask_resolved",
        {"ask_id": "a", "grant_id": grant_id, "decision": "allow"},
    )
    persistence.delete_session(session_id)

    assert store.is_allowed("t", "build", "list@list_files", ".")
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM remembered_grant_audits WHERE grant_id=?",
            (grant_id,),
        ).fetchone() == (1,)


def test_unknown_newer_permission_contract_is_not_destructively_downgraded(
    isolated_home,
):
    import sqlite3

    store = AlwaysAllowStore()
    grant_id = store.remember("t", "build", "list@list_files", ".")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE permission_meta SET value=? WHERE key='always_allow_contract'",
            ("explicit_raw_exact_claim_audit_v5",),
        )

    with pytest.raises(RuntimeError, match="Unsupported permission contract"):
        AlwaysAllowStore(store.db_path)

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT value FROM permission_meta WHERE key='always_allow_contract'"
        ).fetchone() == ("explicit_raw_exact_claim_audit_v5",)
        assert conn.execute(
            "SELECT 1 FROM remembered_grant_audits WHERE grant_id=?",
            (grant_id,),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT 1 FROM always_allow_claims WHERE owner_token=?",
            (grant_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("decoded_arguments", [[], False, 0, None])
def test_runner_rejects_falsy_decoded_non_object_arguments(
    mock_provider, isolated_home, decoded_arguments
):
    from backend.agent import AgentRunner, get_agent
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "list_files",
            "arguments": decoded_arguments,
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    executed = []
    handlers = {**TOOL_HANDLERS, "list_files": lambda args, ctx: executed.append(args)}
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "JSON object" in tool_result["result"]
    assert executed == []


@pytest.mark.parametrize("path_value", [[], False, 0, None, 1, {}])
def test_list_files_rejects_non_string_path_before_permission_or_execution(
    mock_provider, isolated_home, path_value
):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {
        "function": {
            "name": "list_files",
            "arguments": json.dumps({"path": path_value}),
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    asked = []
    executed = []
    engine = PermissionEngine(
        rules=parse_rules({"build": {"list": "ask"}}),
        project_id="t",
        store=AlwaysAllowStore(),
    )

    async def ask(agent_name, tool, target, key):
        asked.append(target)
        return AskResult(decision=Decision.ALLOW, remember=True)

    handlers = {**TOOL_HANDLERS, "list_files": lambda args, ctx: executed.append(args)}
    runner = AgentRunner(
        mock_provider,
        agent,
        handlers,
        TOOL_SCHEMAS,
        permission_engine=engine,
        on_ask=ask,
        max_iterations=1,
    )
    result = asyncio.run(runner.run("list"))
    tool_result = [e for e in result.events if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False
    assert "path must be a string" in tool_result["result"]
    assert asked == []
    assert executed == []
