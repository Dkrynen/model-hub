from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def _events(response) -> list[dict]:
    out: list[dict] = []
    for line in response.get_data(as_text=True).splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            continue
        out.append(json.loads(data))
    return out


def test_agent_chat_streams_and_persists_tool_events(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.cookbook import persistence

    captured: dict = {}

    class FakeProvider:
        name = "ollama"

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, ctx=None, max_iterations=None, **kwargs):
            captured["provider"] = provider
            captured["agent"] = agent
            captured["ctx"] = ctx
            captured["max_iterations"] = max_iterations
            captured["chat_options"] = kwargs.get("chat_options")

        async def run_stream(self, user_text, history=None):
            captured["user_text"] = user_text
            captured["history"] = history
            yield {"type": "delta", "content": "hello"}
            yield {"type": "tool_call", "name": "list_files", "args": {"path": "."}}
            yield {"type": "tool_result", "name": "list_files", "ok": True, "result": "f api.py"}
            yield {
                "type": "done",
                "content": "hello",
                "messages": [
                    {"role": "user", "content": user_text},
                    {"role": "tool", "name": "list_files", "content": "f api.py"},
                    {"role": "assistant", "content": "hello"},
                ],
                "iterations": 1,
            }

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: FakeProvider())

    project = tmp_path / "project"
    project.mkdir()
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "List this project",
            "cwd": str(project),
            "messages": [{"role": "system", "content": "stay brief"}],
        },
    )

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    events = _events(response)
    assert [e["type"] for e in events] == ["session", "run", "status", "delta", "tool_call", "tool_result", "done"]
    assert captured["agent"].model == "mock:1b"
    assert captured["agent"].tools == ["read_file", "list_files"]
    assert not captured["agent"].permissions.can_write()
    assert not captured["agent"].permissions.can_run_bash()
    assert Path(captured["ctx"]["cwd"]) == project.resolve()
    assert captured["chat_options"]["keep_alive"] == "30m"
    assert captured["chat_options"]["options"]["num_ctx"] > 0
    assert captured["history"] == [{"role": "system", "content": "stay brief"}]

    session = persistence.get_session(events[0]["session_id"])
    assert session is not None
    assert [m["role"] for m in session["messages"]] == ["system", "user", "assistant"]
    assert session["messages"][-1]["content"] == "hello"
    assert [e["type"] for e in session["events"]] == ["tool_call", "tool_result"]


def test_agent_chat_ignores_project_agent_override(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod

    captured: dict = {}

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, ctx=None, max_iterations=None, **kwargs):
            captured["agent"] = agent
            captured["schemas"] = schemas

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "safe", "messages": [], "iterations": 1}

    project = tmp_path / "project"
    agent_dir = project / ".apt" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "plan.json").write_text(
        json.dumps(
            {
                "name": "plan",
                "permissions": {
                    "filesystem": {"read": True, "write": True, "delete": True},
                    "bash": {"run": True},
                    "network": {"fetch": True, "post": True},
                    "mcp": {"connect": True},
                },
                "tools": ["read_file", "list_files", "write_file", "run_bash"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "inspect", "cwd": str(project)},
    )

    assert response.status_code == 200
    _events(response)
    assert captured["agent"].tools == ["read_file", "list_files"]
    assert not captured["agent"].permissions.can_write()
    assert not captured["agent"].permissions.can_run_bash()


def test_agent_chat_preserves_saved_workspace_when_client_omits_it(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.cookbook import persistence

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    sid = persistence.create_session(name="t", model="mock:1b", workspace="saved-workspace")
    project = tmp_path / "project"
    project.mkdir()

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "continue",
            "session_id": sid,
            "messages": [{"role": "user", "content": "earlier"}],
            "cwd": str(project),
        },
    )

    assert response.status_code == 200
    _events(response)
    saved = persistence.get_session(sid)
    assert saved["workspace"] == "saved-workspace"
    assert saved["name"] == "t"


def test_agent_chat_accepts_build_and_rejects_unknown_modes(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    captured: dict = {}

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured["agent"] = agent

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ready", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()
    (project / ".apt").mkdir()
    (project / ".apt" / "apt.jsonc").write_text("{}", encoding="utf-8")

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "build", "model": "mock:1b", "message": "edit files", "cwd": str(project)},
    )
    assert response.status_code == 200
    assert [e["type"] for e in _events(response)][-1] == "done"
    assert captured["agent"].name == "build"
    assert "write_file" in captured["agent"].tools
    assert "run_bash" not in captured["agent"].tools

    unknown = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "shell", "model": "mock:1b", "message": "edit files", "cwd": str(project)},
    )
    assert unknown.status_code == 403
    assert unknown.get_json()["error"] == "Unknown web agent mode: shell"
    assert unknown.get_json()["allowed_agents"] == ["ask", "build", "explore", "plan"]


def test_project_bound_ask_is_explicit_local_zero_tool_mode(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.cookbook.config import create_workspace

    captured: dict = {}

    class FakeProvider:
        name = "ollama"

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured.update({
                "provider": provider,
                "agent": agent,
                "handlers": handlers,
                "schemas": schemas,
                "kwargs": kwargs,
            })

        async def run_stream(self, user_text, history=None):
            yield {"type": "delta", "content": "Local answer"}
            yield {
                "type": "done",
                "content": "Local answer",
                "messages": [],
                "iterations": 1,
                "eval_count": 4,
                "eval_duration": 2_000_000_000,
            }

    workspace = create_workspace("Ask Client")
    root = tmp_path / "ask-project"
    root.mkdir()
    project = persistence.create_project(
        workspace=workspace.id,
        name="Ask Project",
        root=str(root),
    )
    provider_calls = []
    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(
        api_mod,
        "OllamaProvider",
        lambda **kwargs: provider_calls.append(kwargs) or FakeProvider(),
        raising=False,
    )
    monkeypatch.setattr(
        api_mod,
        "default_provider",
        lambda *_args, **_kwargs: pytest.fail("Ask must not resolve the project provider"),
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "ask",
            "model": "qwen-local:7b",
            "message": "Keep this thread local",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    session = persistence.get_session(events[0]["session_id"])
    assert provider_calls == [{"base_url": api_mod.OLLAMA_HOST}]
    assert session["project_id"] == project["id"]
    assert session["workspace"] == workspace.id
    assert [message["content"] for message in session["messages"]] == [
        "Keep this thread local",
        "Local answer",
    ]
    assert captured["agent"].tools == []
    assert captured["agent"].system_prompt == ""
    assert not captured["agent"].permissions.can_read()
    assert not captured["agent"].permissions.can_fetch()
    assert not captured["agent"].permissions.can_mcp()
    assert captured["handlers"] == {}
    assert captured["schemas"] == []
    assert captured["kwargs"]["permission_engine"] is None
    assert captured["kwargs"]["on_ask"] is None
    assert captured["kwargs"]["max_iterations"] == 1
    assert not any(event["type"] in {"tool_call", "tool_calls", "tool_result", "ask"} for event in events)


def test_ask_requires_registered_project_and_explicit_model_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.cookbook.config import create_workspace

    provider_calls = []
    monkeypatch.setattr(
        api_mod,
        "OllamaProvider",
        lambda **kwargs: provider_calls.append(kwargs) or object(),
        raising=False,
    )

    without_project = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "ask", "model": "qwen-local:7b", "message": "Hello"},
    )
    assert without_project.status_code == 400
    assert "registered project" in without_project.get_json()["error"].lower()

    workspace = create_workspace("Ask Client")
    root = tmp_path / "ask-project"
    root.mkdir()
    project = persistence.create_project(
        workspace=workspace.id,
        name="Ask Project",
        root=str(root),
    )
    without_model = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "ask", "message": "Hello", "project_id": project["id"]},
    )
    assert without_model.status_code == 400
    assert without_model.get_json()["error"] == "Ask mode requires an explicit local model"
    assert provider_calls == []


@pytest.mark.parametrize(
    "ollama_host",
    [
        "https://models.example.com:11434",
        "http://192.168.1.25:11434",
        "http://[2001:db8::25]:11434",
    ],
)
def test_ask_rejects_non_loopback_ollama_before_session_or_provider(
    flask_app, isolated_home, monkeypatch, tmp_path, ollama_host
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.cookbook.config import create_workspace

    workspace = create_workspace("Ask Client")
    root = tmp_path / "ask-project"
    root.mkdir()
    project = persistence.create_project(
        workspace=workspace.id,
        name="Ask Project",
        root=str(root),
    )
    provider_calls = []
    monkeypatch.setattr(api_mod, "OLLAMA_HOST", ollama_host)
    monkeypatch.setattr(
        api_mod,
        "OllamaProvider",
        lambda **kwargs: provider_calls.append(kwargs) or object(),
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "ask",
            "model": "qwen-local:7b",
            "message": "Do not send this away",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "Ask mode requires a loopback Ollama host"
    assert provider_calls == []
    assert persistence.list_sessions(
        workspace=workspace.id,
        project_id=project["id"],
    ) == []


def test_project_bound_ask_does_not_resurrect_a_deleted_thread(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.cookbook.config import create_workspace

    workspace = create_workspace("Ask Client")
    root = tmp_path / "ask-project"
    root.mkdir()
    project = persistence.create_project(
        workspace=workspace.id,
        name="Ask Project",
        root=str(root),
    )

    class FakeProvider:
        name = "ollama"

    class DeletingRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            sessions = persistence.list_sessions(
                workspace=workspace.id,
                project_id=project["id"],
            )
            assert len(sessions) == 1
            persistence.delete_session(sessions[0]["id"])
            yield {
                "type": "done",
                "content": "late answer",
                "messages": [],
                "iterations": 1,
            }

    monkeypatch.setattr(api_mod, "AgentRunner", DeletingRunner)
    monkeypatch.setattr(api_mod, "OllamaProvider", lambda **_kwargs: FakeProvider())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "ask",
            "model": "qwen-local:7b",
            "message": "Delete while running",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert persistence.get_session(events[0]["session_id"]) is None


def test_agent_chat_requires_explicit_project_cwd_for_build(
    flask_app, isolated_home
):
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "build", "model": "mock:1b", "message": "edit files"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Build mode requires an explicit project cwd"


def test_build_requires_configured_project_root_and_evaluates_rules_from_it(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.permission import Decision

    captured = []

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured.append(kwargs["permission_engine"])

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ready", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    configured = tmp_path / "configured"
    selected = configured / "backend"
    selected.mkdir(parents=True)
    (configured / ".apt").mkdir()
    (configured / ".apt" / "apt.jsonc").write_text(
        json.dumps(
            {
                "permission": {
                    "build": {
                        "edit": {"backend/**": "deny", "*": "allow"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    nested = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "build",
            "model": "mock:1b",
            "message": "edit files",
            "cwd": str(selected),
        },
    )
    assert nested.status_code == 400
    payload = nested.get_json()
    assert payload["configured_root"] == str(configured.resolve())
    assert str(configured.resolve()) in payload["error"]
    assert captured == []

    root = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "build",
            "model": "mock:1b",
            "message": "edit files",
            "cwd": str(configured),
        },
    )
    assert root.status_code == 200
    _events(root)
    assert len(captured) == 1
    engine = captured[0]
    assert engine.evaluate(
        "build", "edit", "backend/secret.py", tool_name="write_file"
    ) is Decision.DENY
    assert engine.evaluate(
        "build", "edit", "frontend/app.py", tool_name="write_file"
    ) is Decision.ALLOW


def test_build_runner_is_project_scoped_and_staged_while_read_modes_stay_read_only(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.permission import PermissionEngine
    from backend.permission.engine import project_id_for
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    captured: list[dict] = []

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured.append(
                {
                    "agent": agent,
                    "handlers": handlers,
                    "permission_engine": kwargs.get("permission_engine"),
                }
            )

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ready", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "configured"
    project.mkdir()
    (project / ".apt").mkdir()
    (project / ".apt" / "apt.jsonc").write_text("{}", encoding="utf-8")

    for agent in ("build", "plan", "explore"):
        response = flask_app.test_client().post(
            "/api/agent/chat",
            json={"agent": agent, "model": "mock:1b", "message": "inspect", "cwd": str(project)},
        )
        assert response.status_code == 200
        _events(response)

    build, plan, explore = captured
    assert isinstance(build["permission_engine"], PermissionEngine)
    assert build["permission_engine"].project_id == project_id_for(project.resolve())
    assert build["handlers"]["write_file"] is not TOOL_HANDLERS["write_file"]
    assert build["handlers"]["read_file"] is not TOOL_HANDLERS["read_file"]
    assert build["handlers"]["list_files"] is not TOOL_HANDLERS["list_files"]
    assert "run_bash" not in build["handlers"]
    assert build["agent"].permissions.can_write()
    assert not build["agent"].permissions.can_run_bash()
    assert build["agent"].tools == ["read_file", "write_file", "list_files"]
    assert "staged" in build["agent"].description.lower()
    assert "docker tasks" in build["agent"].description.lower()
    assert "cannot run shell commands on the host" in build["agent"].system_prompt.lower()
    assert "run_task" not in build["agent"].tools

    for read_mode in (plan, explore):
        assert read_mode["permission_engine"] is None
        assert read_mode["handlers"] is TOOL_HANDLERS
        assert not read_mode["agent"].permissions.can_write()
        assert not read_mode["agent"].permissions.can_run_bash()
    assert plan["agent"].tools == ["read_file", "list_files"]
    assert explore["agent"].tools == ["read_file", "list_files", "web_search"]


def test_build_exposes_only_named_sandbox_task_when_exact_capability_is_ready(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    captured: dict = {}

    class Capability:
        available = True
        tasks = ("lint", "test")
        image = "example/lac@sha256:" + ("a" * 64)

    class Broker:
        def __init__(self, root, session_id, run_id, cancel_event, *, capability):
            captured["broker"] = {
                "root": root,
                "session_id": session_id,
                "run_id": run_id,
                "cancel_event": cancel_event,
                "capability": capability,
            }

        def prepare_task(self, name):  # pragma: no cover - runner is captured
            raise AssertionError(name)

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured["agent"] = agent
            captured["handlers"] = handlers
            captured["schemas"] = schemas
            captured["kwargs"] = kwargs

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ready", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "probe_project_sandbox", lambda root: Capability())
    monkeypatch.setattr(api_mod, "DockerTaskBroker", Broker)
    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())

    project = tmp_path / "configured"
    project.mkdir()
    (project / ".apt").mkdir()
    (project / ".apt" / "apt.jsonc").write_text("{}", encoding="utf-8")
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "build", "model": "mock:1b", "message": "verify", "cwd": str(project)},
    )
    assert response.status_code == 200
    _events(response)

    assert captured["agent"].tools == [
        "read_file",
        "write_file",
        "list_files",
        "run_task",
    ]
    assert "run_bash" not in captured["handlers"]
    assert captured["agent"].permissions.can_run_bash() is False
    schema = [
        item for item in captured["schemas"]
        if item["function"]["name"] == "run_task"
    ][0]
    assert schema["function"]["parameters"]["properties"]["name"]["enum"] == [
        "lint",
        "test",
    ]
    assert set(captured["kwargs"]["tool_preparers"]) == {"run_task"}
    assert captured["kwargs"]["always_ask_tools"] == {"run_task"}
    assert captured["kwargs"]["never_remember_tools"] == {"run_task"}
    assert captured["broker"]["root"] == project.resolve()
    assert captured["broker"]["cancel_event"] is captured["kwargs"]["ctx"]["cancel_event"]


def test_run_task_approval_is_never_rememberable_even_for_bounded_details():
    import backend.api as api_mod

    assert api_mod._ask_is_rememberable(
        "run_task",
        {
            "kind": "sandbox_task",
            "name": "test",
            "argv": ["python", "-m", "pytest"],
        },
        "task",
    ) is False


def test_real_build_run_task_freezes_then_executes_only_after_allow_once(
    flask_app, isolated_home, monkeypatch, tmp_path, mock_provider
):
    import backend.api as api_mod
    from backend.provider.base import ChatDelta

    executed: list[str] = []
    approval_target = {
        "kind": "sandbox_task",
        "name": "test",
        "argv": ["python", "-m", "pytest", "-q"],
        "root": "will-be-replaced",
        "image": "example/lac@sha256:" + ("a" * 64),
        "image_id": "sha256:" + ("b" * 64),
        "timeout_seconds": 120,
        "network": "none",
        "staged_overlay_digest": "c" * 64,
        "config_digest": "d" * 64,
        "staged_changes": [],
    }

    class Capability:
        available = True
        tasks = ("test",)
        image = approval_target["image"]

    class Frozen:
        permission_target = "test"

        def __init__(self, root):
            self.approval_target = {**approval_target, "root": str(root)}

        def execute_outcome(self):
            executed.append("test")
            return True, "[exit 0]\n2 passed"

    class Broker:
        def __init__(self, root, session_id, run_id, cancel_event, *, capability):
            self.root = root

        def prepare_task(self, name):
            assert name == "test"
            return Frozen(self.root)

    project = tmp_path / "project"
    project.mkdir()
    (project / ".apt").mkdir()
    (project / ".apt" / "apt.jsonc").write_text(
        json.dumps({"permission": {"build": {"task": "allow"}}}),
        encoding="utf-8",
    )
    mock_provider.set_script(
        [
            ChatDelta(
                content="",
                tool_calls=[
                    {
                        "function": {
                            "name": "run_task",
                            "arguments": json.dumps({"name": "test"}),
                        }
                    }
                ],
                done=True,
            )
        ]
    )
    monkeypatch.setattr(api_mod, "probe_project_sandbox", lambda root: Capability())
    monkeypatch.setattr(api_mod, "DockerTaskBroker", Broker)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: mock_provider)

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "build",
            "model": "mock:1b",
            "message": "run verification",
            "cwd": str(project),
            "max_iterations": 1,
        },
    )
    events = _iter_events(response)
    run_id = approval_token = None
    ask = None
    for event in events:
        if event["type"] == "run":
            run_id = event["run_id"]
            approval_token = event["approval_token"]
        elif event["type"] == "ask":
            ask = event
            break

    assert ask is not None
    assert ask["tool"] == "run_task"
    assert ask["key"] == "task"
    assert ask["rememberable"] is False
    assert ask["target"] == {**approval_target, "root": str(project.resolve())}
    assert executed == []

    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer",
        json={
            "ask_id": ask["ask_id"],
            "approval_token": approval_token,
            "decision": "allow",
            "remember": True,
        },
    )
    assert answer.status_code == 200
    remaining = list(events)
    assert executed == ["test"]
    resolved = [event for event in remaining if event["type"] == "ask_resolved"][0]
    assert resolved["decision"] == "allow"
    assert resolved["remember"] is False
    tool_result = [event for event in remaining if event["type"] == "tool_result"][0]
    assert tool_result == {
        "type": "tool_result",
        "name": "run_task",
        "ok": True,
        "result": "[exit 0]\n2 passed",
    }


class _BuildWriteRunner:
    """Exercise the web ask callback and the injected write handler together."""

    def __init__(self, provider, agent, handlers, schemas, ctx=None, **kwargs):
        self.agent = agent
        self.handlers = handlers
        self.ctx = ctx or {}
        self.on_ask = kwargs["on_ask"]

    async def run_stream(self, user_text, history=None):
        from backend.permission import Decision

        answer = await self.on_ask("build", "write_file", "src/app.py", "edit")
        decision = answer.consume()
        if decision is Decision.ALLOW:
            result = self.handlers["write_file"](
                {"path": "src/app.py", "content": "print('staged')\n"}, self.ctx
            )
            ok = not result.startswith("error:")
        else:
            result = "[permission denied by user: write_file]"
            ok = False
        yield {"type": "tool_result", "name": "write_file", "ok": ok, "result": result}
        yield {"type": "done", "content": "finished", "messages": [], "iterations": 1}


@pytest.mark.parametrize("decision, expected_pending", [("deny", 0), ("allow", 1)])
def test_build_write_decision_only_stages_after_allow_once(
    flask_app, isolated_home, monkeypatch, tmp_path, decision, expected_pending
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    monkeypatch.setattr(api_mod, "AgentRunner", _BuildWriteRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "build", "model": "mock:1b", "message": "write it", "cwd": str(project)},
    )
    events = _iter_events(response)
    session_id = run_id = approval_token = None
    ask = None
    for event in events:
        if event["type"] == "session":
            session_id = event["session_id"]
        elif event["type"] == "run":
            run_id = event["run_id"]
            approval_token = event["approval_token"]
        elif event["type"] == "ask":
            ask = event
            break

    assert ask is not None
    assert ask["run_id"] == run_id
    assert ask["tool"] == "write_file"
    assert ask["target"] == "src/app.py"
    assert ask["rememberable"] is True
    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer",
        json={
            "ask_id": ask["ask_id"],
            "approval_token": approval_token,
            "decision": decision,
            "remember": False,
        },
    )
    assert answer.status_code == 200
    remaining = list(events)

    pending = persistence.list_staged_changes(session_id, status="pending")
    assert len(pending) == expected_pending
    assert not (project / "src" / "app.py").exists()
    staged_events = [event for event in remaining if event["type"] == "staged_change"]
    assert len(staged_events) == expected_pending
    if expected_pending:
        assert pending[0]["path"] == "src/app.py"
        assert pending[0]["new_content"] == "print('staged')\n"
        assert staged_events[0]["change_id"] == pending[0]["id"]


def test_real_build_runner_remembered_allow_stages_and_replays_exact_scope(
    flask_app, isolated_home, monkeypatch, tmp_path, mock_provider
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision, PermissionEngine
    from backend.provider.base import ChatDelta

    project = tmp_path / "project"
    project.mkdir()
    (project / ".apt").mkdir()
    (project / ".apt" / "apt.jsonc").write_text(
        json.dumps({"permission": {"build": {"edit": "ask"}}}),
        encoding="utf-8",
    )
    call = {
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": "draft.txt", "content": "staged only\n"}),
        }
    }
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: mock_provider)

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "build",
            "model": "mock:1b",
            "message": "write a draft",
            "cwd": str(project),
            "max_iterations": 1,
        },
    )
    events = _iter_events(response)
    session_id = run_id = approval_token = None
    ask = None
    for event in events:
        if event["type"] == "session":
            session_id = event["session_id"]
        elif event["type"] == "run":
            run_id = event["run_id"]
            approval_token = event["approval_token"]
        elif event["type"] == "ask":
            ask = event
            break

    assert ask is not None and ask["rememberable"] is True
    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer",
        json={
            "ask_id": ask["ask_id"],
            "approval_token": approval_token,
            "decision": "allow",
            "remember": True,
        },
    )
    assert answer.status_code == 200
    remaining = list(events)
    assert any(event["type"] == "staged_change" for event in remaining)
    assert not (project / "draft.txt").exists()
    pending = persistence.list_staged_changes(session_id, status="pending")
    assert len(pending) == 1 and pending[0]["new_content"] == "staged only\n"

    replay = PermissionEngine.from_config(start_dir=project)
    assert replay.evaluate(
        "build", "edit", "draft.txt", tool_name="write_file"
    ) is Decision.ALLOW


def test_agent_chat_requires_message(flask_app, isolated_home):
    response = flask_app.test_client().post("/api/agent/chat", json={"model": "mock:1b"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Message required"


def _iter_events(resp):
    """Incrementally parse SSE data frames from a streaming test-client response.

    resp.response is Werkzeug's LAZY app iterator; .get_data() would drain the
    whole stream (and hang on a paused run) - never use it in bridge tests.
    """
    for chunk in resp.response:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line.removeprefix("data:").strip()
                if data != "[DONE]":
                    yield json.loads(data)


def test_agent_chat_emits_run_event_and_registers_run(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = list(_iter_events(resp))
    run_events = [e for e in events if e["type"] == "run"]
    assert len(run_events) == 1 and run_events[0]["run_id"]
    # run completed -> registry swept clean by the finally block
    assert api_mod._AGENT_RUNS == {}


def test_agent_run_registry_enforces_capacity(monkeypatch):
    import queue as queue_mod
    import threading

    import pytest

    import backend.api as api_mod

    monkeypatch.setattr(api_mod, "MAX_AGENT_RUNS", 1)

    def make_run(session_id):
        return api_mod._AgentRun(
            ask_event=threading.Event(),
            queue=queue_mod.Queue(),
            session_id=session_id,
            created_at=time.time(),
        )

    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS.clear()
    try:
        api_mod._register_agent_run("one", make_run("s1"))
        with pytest.raises(RuntimeError, match="Too many active agent runs"):
            api_mod._register_agent_run("two", make_run("s2"))
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.clear()


def test_agent_run_registry_rejects_overlapping_runs_for_one_session():
    import queue as queue_mod
    import threading

    import backend.api as api_mod

    def make_run():
        return api_mod._AgentRun(
            ask_event=threading.Event(),
            queue=queue_mod.Queue(),
            session_id="same-session",
            created_at=time.time(),
        )

    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS.clear()
    try:
        first = make_run()
        api_mod._register_agent_run("one", first)
        first.worker_done.set()
        api_mod._release_agent_run_if_finished("one", first)
        with pytest.raises(RuntimeError, match="already has an active run"):
            api_mod._register_agent_run("two", make_run())
        first.persistence_done.set()
        api_mod._release_agent_run_if_finished("one", first)
        api_mod._register_agent_run("two", make_run())
        assert set(api_mod._AGENT_RUNS) == {"two"}
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.clear()


def test_agent_chat_heartbeats_while_runner_is_slow(flask_app, isolated_home, monkeypatch, tmp_path):
    import asyncio as aio

    import backend.api as api_mod

    monkeypatch.setattr(api_mod, "HEARTBEAT_INTERVAL", 0.05)

    class SlowRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            await aio.sleep(0.4)
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", SlowRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    raw = b"".join(c if isinstance(c, bytes) else c.encode() for c in resp.response)
    assert b": ping" in raw  # SSE comment heartbeat reached the wire
    assert b'"type": "done"' in raw or b'"type":"done"' in raw


def test_agent_chat_disconnect_cancels_worker(flask_app, isolated_home, monkeypatch, tmp_path):
    import asyncio as aio

    import backend.api as api_mod

    started = {"flag": False}

    class DripRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            started["flag"] = True
            for i in range(1000):
                yield {"type": "delta", "content": f"chunk{i}"}
                await aio.sleep(0.01)
            yield {"type": "done", "content": "never", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", DripRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(resp)
    for ev in events:
        if ev["type"] == "delta":
            break  # stream is live
    resp.response.close()  # simulate client disconnect -> GeneratorExit in generate()

    assert started["flag"]
    deadline = time.time() + 5
    while time.time() < deadline and api_mod._AGENT_RUNS:
        time.sleep(0.05)
    assert api_mod._AGENT_RUNS == {}  # registry entry dropped by the finally block


def test_agent_chat_disconnect_preserves_existing_cancel_reason(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "unused", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_event = next(ev for ev in events if ev["type"] == "run")
    run = api_mod._AGENT_RUNS[run_event["run_id"]]

    try:
        cancelled = flask_app.test_client().post(
            f"/api/agent/runs/{run_event['run_id']}/cancel",
            json={"approval_token": run_event["approval_token"]},
        )
        assert cancelled.status_code == 200
        assert run.cancel_reason == "user_cancelled"
    finally:
        resp.response.close()

    assert run.cancel_reason == "user_cancelled"
    assert run_event["run_id"] not in api_mod._AGENT_RUNS


def test_agent_chat_disconnect_after_first_event_cleans_registry(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(resp)
    assert next(events)["type"] == "session"
    assert api_mod._AGENT_RUNS
    try:
        resp.response.close()
        assert api_mod._AGENT_RUNS == {}
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.clear()


def test_agent_chat_runner_constructor_failure_does_not_register_run(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import pytest

    import backend.api as api_mod
    from backend.cookbook import persistence

    class FailingRunner:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("runner init failed")

    monkeypatch.setattr(api_mod, "AgentRunner", FailingRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    before = [s["id"] for s in persistence.list_sessions()]
    with pytest.raises(RuntimeError, match="runner init failed"):
        flask_app.test_client().post(
            "/api/agent/chat",
            json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
        )
    try:
        assert api_mod._AGENT_RUNS == {}
        assert [s["id"] for s in persistence.list_sessions()] == before
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.clear()


def test_blocked_worker_stays_tracked_until_it_exits(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import asyncio as aio
    import threading

    import backend.api as api_mod

    release = threading.Event()
    monkeypatch.setattr(api_mod, "WORKER_JOIN_TIMEOUT", 0.05, raising=False)

    class BlockedRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "delta", "content": "started"}
            loop = aio.get_running_loop()
            await loop.run_in_executor(None, release.wait)
            yield {"type": "done", "content": "late", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", BlockedRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_id = None
    try:
        for ev in events:
            if ev["type"] == "run":
                run_id = ev["run_id"]
            if ev["type"] == "delta":
                break
        assert run_id
        run = api_mod._AGENT_RUNS[run_id]
        assert run.queue.maxsize == api_mod.AGENT_EVENT_QUEUE_MAX
        resp.response.close()
        assert run.thread is not None and run.thread.is_alive()
        assert api_mod._AGENT_RUNS.get(run_id) is run
    finally:
        release.set()

    deadline = time.time() + 5
    while time.time() < deadline and run_id in api_mod._AGENT_RUNS:
        time.sleep(0.01)
    assert run_id not in api_mod._AGENT_RUNS


def _ask_fake_runner(captured, *, key="bash"):
    """Fake runner whose stream asks once and reacts to the verdict."""
    from backend.agent.runner import AskResult
    from backend.permission import Decision

    class AskingRunner:
        def __init__(self, *args, **kwargs):
            captured["on_ask"] = kwargs.get("on_ask")

        async def run_stream(self, user_text, history=None):
            result = await captured["on_ask"](
                "build", "run_bash", "pytest -q", key
            )
            captured["ask_result"] = result
            assert isinstance(result, AskResult)

            def remember():
                captured["remembered"] = True
                return "fake-web-grant"

            def rollback():
                captured["remembered"] = False

            consumed = result.consume(
                remember if result.remember else None,
                rollback if result.remember else None,
                "fake-web-grant" if result.remember else None,
            )
            captured["consumed_decision"] = consumed
            ok = consumed == Decision.ALLOW
            yield {
                "type": "tool_result",
                "name": "run_bash",
                "ok": ok,
                "result": "[exit 0]" if ok else "[denied]",
            }
            yield {"type": "done", "content": "finished", "messages": [], "iterations": 1}

    return AskingRunner


def test_ask_bridge_allow_flow_and_replay(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run tests", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_id = session_id = approval_token = None
    ask = None
    for ev in events:
        if ev["type"] == "session":
            session_id = ev["session_id"]
        elif ev["type"] == "run":
            run_id = ev["run_id"]
            approval_token = ev["approval_token"]
        elif ev["type"] == "ask":
            ask = ev
            break
    assert ask is not None and ask["ask_id"]
    assert ask["tool"] == "run_bash"
    assert ask["target"] == "pytest -q"
    assert ask["key"] == "bash"
    assert ask["doom_loop"] is False
    assert ask["rememberable"] is True

    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer",
        json={
            "ask_id": ask["ask_id"],
            "approval_token": approval_token,
            "decision": "allow",
            "remember": True,
        },
    )
    assert answer.status_code == 200
    assert answer.get_json() == {"ok": True}

    # A 200 means the decision is durable even before the SSE consumer drains it.
    immediate = persistence.list_session_events(session_id)
    assert [e["type"] for e in immediate].count("ask_resolved") == 1

    rest = list(events)
    types = [e["type"] for e in rest]
    assert "ask_resolved" in types
    resolved = [e for e in rest if e["type"] == "ask_resolved"][0]
    assert resolved["ask_id"] == ask["ask_id"]
    assert resolved["decision"] == "allow" and resolved["remember"] is True
    assert [e for e in rest if e["type"] == "tool_result"][0]["ok"] is True
    assert captured["ask_result"].decision == Decision.ALLOW
    assert captured["ask_result"].remember is True

    stored = persistence.list_session_events(session_id)
    stored_types = [e["type"] for e in stored]
    assert stored_types.count("ask") == 1
    assert stored_types.count("ask_resolved") == 1
    stored_resolved = [e["payload"] for e in stored if e["type"] == "ask_resolved"][0]
    assert stored_resolved["ask_id"] == ask["ask_id"]
    assert stored_resolved["decision"] == "allow"


def test_ask_bridge_deny_flow(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_id = approval_token = ask = None
    for ev in events:
        if ev["type"] == "run":
            run_id = ev["run_id"]
            approval_token = ev["approval_token"]
        elif ev["type"] == "ask":
            ask = ev
            break
    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer",
        json={
            "ask_id": ask["ask_id"],
            "approval_token": approval_token,
            "decision": "deny",
            "remember": True,
        },
    )
    assert answer.status_code == 200
    rest = list(events)
    assert captured["ask_result"].decision == Decision.DENY
    assert captured["ask_result"].remember is False
    assert [e for e in rest if e["type"] == "tool_result"][0]["ok"] is False
    resolved = [e for e in rest if e["type"] == "ask_resolved"][0]
    assert resolved["decision"] == "deny" and resolved["remember"] is False


def test_ask_timeout_denies_and_run_continues(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision

    monkeypatch.setattr(api_mod, "ASK_TIMEOUT", 0.05)
    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = list(_iter_events(resp))
    types = [e["type"] for e in events]
    ask = [e for e in events if e["type"] == "ask"][0]
    timeout = [e for e in events if e["type"] == "ask_timeout"][0]
    assert timeout["ask_id"] == ask["ask_id"]
    assert "done" in types
    assert captured["ask_result"].decision == Decision.DENY
    session_id = [e for e in events if e["type"] == "session"][0]["session_id"]
    assert "ask_timeout" in [e["type"] for e in persistence.list_session_events(session_id)]


def _registered_pending_run(
    api_mod,
    persistence,
    *,
    key="bash",
    target="pytest -q",
    event=None,
    remember_action=None,
    rollback_action=None,
    before_consume=None,
):
    import asyncio
    import queue as queue_mod
    import threading

    sid = persistence.create_session(model="mock:1b")
    run = api_mod._AgentRun(
        ask_event=event or threading.Event(),
        queue=queue_mod.Queue(),
        session_id=sid,
        created_at=time.time(),
    )
    captured = {}
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["testrun"] = run

    def drive_ask():
        result = asyncio.run(
            api_mod._make_web_ask("testrun", run)(
                "build", "run_bash", target, key
            )
        )
        captured["result"] = result
        if before_consume is not None:
            before_consume.wait()
        grant_id = (
            f"test-{run.approval_token[:16]}" if result.remember else None
        )
        if result.remember and remember_action is None:
            def action():
                captured["remembered"] = True
                return grant_id

            rollback = lambda: captured.__setitem__("remembered", False)
        elif remember_action is not None:
            def action():
                committed = remember_action()
                return grant_id if committed is None else committed

            rollback = rollback_action
        else:
            action = None
            rollback = rollback_action
        try:
            captured["grant_id"] = grant_id
            captured["decision"] = result.consume(action, rollback, grant_id)
        except Exception as e:
            captured["error"] = e

    thread = threading.Thread(target=drive_ask, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and run.pending_ask is None:
        time.sleep(0.01)
    assert run.pending_ask is not None
    return run, sid, thread, captured


def _cleanup_registered_run(api_mod, run, thread):
    with run.state_lock:
        run.cancelled = True
        run.cancel_reason = "test_cleanup"
        run.ask_event.set()
    thread.join(timeout=5)
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS.pop("testrun", None)


def test_answer_endpoint_validation_and_stale_ask_guard(flask_app, isolated_home):
    import backend.api as api_mod
    from backend.cookbook import persistence

    client = flask_app.test_client()
    assert client.post(
        "/api/agent/runs/nosuch/answer",
        json={"ask_id": "x", "approval_token": "x", "decision": "allow"},
    ).status_code == 404
    assert client.post(
        "/api/agent/runs/nosuch/answer",
        json={"ask_id": "x", "approval_token": "x", "decision": "maybe"},
    ).status_code == 400
    assert client.post(
        "/api/agent/runs/nosuch/answer",
        json={"approval_token": "x", "decision": "allow"},
    ).status_code == 400
    assert client.post(
        "/api/agent/runs/nosuch/answer",
        json={
            "ask_id": "x",
            "approval_token": "x",
            "decision": "allow",
            "remember": "false",
        },
    ).status_code == 400

    run, _, thread, _ = _registered_pending_run(api_mod, persistence)
    ask_id = run.pending_ask["ask_id"]
    try:
        assert client.post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": "wrong-token",
                "decision": "allow",
            },
        ).status_code == 403
        assert client.post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": "old-ask",
                "approval_token": run.approval_token,
                "decision": "allow",
            },
        ).status_code == 409
        assert not run.ask_event.is_set()

        response = client.post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        )
        assert response.status_code == 200
        assert client.post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        ).status_code == 409
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_answer_endpoint_rejects_known_closed_run(flask_app, isolated_home):
    import backend.api as api_mod
    from backend.cookbook import persistence

    run, _, thread, _ = _registered_pending_run(api_mod, persistence)
    ask_id = run.pending_ask["ask_id"]
    try:
        with run.state_lock:
            run.cancelled = True
            run.cancel_reason = "completed"
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        )
        assert response.status_code == 409
        assert response.get_json()["error"] == "Run is no longer active"
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_answer_endpoint_normalizes_unrememberable_decisions(flask_app, isolated_home):
    import backend.api as api_mod
    from backend.cookbook import persistence

    client = flask_app.test_client()
    for key, target in (
        ("doom_loop", "pytest -q"),
        ("bash", "cat ~/.ssh/id_rsa"),
        ("task", None),
    ):
        run, sid, thread, _ = _registered_pending_run(
            api_mod, persistence, key=key, target=target
        )
        ask_id = run.pending_ask["ask_id"]
        try:
            ask_payload = [
                event["payload"]
                for event in persistence.list_session_events(sid)
                if event["type"] == "ask"
            ][0]
            assert ask_payload["rememberable"] is False
            response = client.post(
                "/api/agent/runs/testrun/answer",
                json={
                    "ask_id": ask_id,
                    "approval_token": run.approval_token,
                    "decision": "allow",
                    "remember": True,
                },
            )
            assert response.status_code == 200
            assert run.remember is False
            resolved = [
                e["payload"]
                for e in persistence.list_session_events(sid)
                if e["type"] == "ask_resolved"
            ]
            assert len(resolved) == 1 and resolved[0]["remember"] is False
        finally:
            _cleanup_registered_run(api_mod, run, thread)


def test_concurrent_duplicate_answers_are_atomic(flask_app, isolated_home):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import backend.api as api_mod
    from backend.cookbook import persistence

    class RacingEvent:
        def __init__(self):
            self._event = threading.Event()
            self._lock = threading.Lock()
            self._reads = 0
            self._partner = threading.Event()

        def is_set(self):
            value = self._event.is_set()
            with self._lock:
                self._reads += 1
                read_no = self._reads
            if read_no == 1:
                self._partner.wait(0.1)
            elif read_no == 2:
                self._partner.set()
            return value

        def set(self):
            self._event.set()

        def clear(self):
            self._event.clear()

        def wait(self, timeout=None):
            return self._event.wait(timeout)

    run, sid, thread, _ = _registered_pending_run(
        api_mod, persistence, event=RacingEvent()
    )
    ask_id = run.pending_ask["ask_id"]
    start = threading.Barrier(3)

    def answer_once():
        start.wait()
        return flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        ).status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(answer_once) for _ in range(2)]
            start.wait()
            statuses = sorted(f.result(timeout=5) for f in futures)
        assert statuses == [200, 409]
        resolved = [e for e in persistence.list_session_events(sid) if e["type"] == "ask_resolved"]
        assert len(resolved) == 1
        assert run.ask_event.is_set()
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_answer_endpoint_is_loopback_only(flask_app, isolated_home):
    response = flask_app.test_client().post(
        "/api/agent/runs/nosuch/answer",
        json={"ask_id": "x", "decision": "allow"},
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )
    assert response.status_code == 403
    rebound = flask_app.test_client().post(
        "/api/agent/runs/nosuch/answer",
        json={"ask_id": "x", "approval_token": "x", "decision": "allow"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"Host": "attacker.example:5050"},
    )
    assert rebound.status_code == 403
    cross_origin = flask_app.test_client().post(
        "/api/agent/runs/nosuch/answer",
        json={"ask_id": "x", "approval_token": "x", "decision": "allow"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"Host": "localhost:5050", "Origin": "https://attacker.example"},
    )
    assert cross_origin.status_code == 403


def test_answer_timeout_boundary_honors_a_just_committed_decision(
    flask_app, isolated_home
):
    import threading

    import backend.api as api_mod
    from backend.cookbook import persistence

    class FalseAtBoundary:
        """Report timeout after the worker has already committed the result."""

        def __init__(self):
            self.committed = threading.Event()

        def set(self):
            self.committed.set()

        def wait(self, timeout=None):
            assert self.committed.wait(timeout)
            return False

    run, _, thread, _ = _registered_pending_run(api_mod, persistence)
    boundary = FalseAtBoundary()
    with run.state_lock:
        run.pending_ask["consumed_event"] = boundary
        ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        )
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_unacknowledged_answer_is_revoked_and_journaled_with_reason(
    flask_app, isolated_home, monkeypatch
):
    import threading

    import backend.api as api_mod
    from backend.cookbook import persistence

    release = threading.Event()
    monkeypatch.setattr(api_mod, "ANSWER_ACK_TIMEOUT", 0.05)
    run, sid, thread, captured = _registered_pending_run(
        api_mod,
        persistence,
        before_consume=release,
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        )
        assert response.status_code == 504
        release.set()
        thread.join(timeout=5)
        assert captured["decision"].value == "deny"
        resolved = [
            e["payload"]
            for e in persistence.list_session_events(sid)
            if e["type"] == "ask_resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0]["decision"] == "deny"
        assert resolved[0]["reason"] == "answer_ack_timeout"
    finally:
        release.set()
        _cleanup_registered_run(api_mod, run, thread)


def test_answer_audit_failure_is_not_acknowledged_for_deny(
    flask_app, isolated_home, monkeypatch
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    original = api_mod._persist_run_event

    def fail_resolution(run, event):
        if event.get("type") == "ask_resolved":
            raise OSError("audit database unavailable")
        return original(run, event)

    monkeypatch.setattr(api_mod, "_persist_run_event", fail_resolution)
    run, _, thread, captured = _registered_pending_run(api_mod, persistence)
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "deny",
            },
        )
        assert response.status_code == 500
        assert "audit database unavailable" in response.get_json()["detail"]
        thread.join(timeout=5)
        assert "audit database unavailable" in str(captured["error"])
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_remember_failure_denies_and_returns_500(
    flask_app, isolated_home
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    def fail_remember():
        raise OSError("permission database unavailable")

    run, sid, thread, captured = _registered_pending_run(
        api_mod,
        persistence,
        remember_action=fail_remember,
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": True,
            },
        )
        assert response.status_code == 500
        assert "permission database unavailable" in response.get_json()["detail"]
        thread.join(timeout=5)
        assert "permission database unavailable" in str(captured["error"])
        resolved = [
            e["payload"]
            for e in persistence.list_session_events(sid)
            if e["type"] == "ask_resolved"
        ]
        assert resolved == [
            {
                "type": "ask_resolved",
                "run_id": "testrun",
                "ask_id": ask_id,
                "grant_id": captured["grant_id"],
                "tool": "run_bash",
                "decision": "deny",
                "remember": False,
                "reason": "remember_persistence_failed",
            }
        ]
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_audit_failure_rolls_back_a_just_remembered_approval(
    flask_app, isolated_home, monkeypatch
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    state = {"remembered": False}

    def remember():
        state["remembered"] = True

    def rollback():
        state["remembered"] = False

    original = api_mod._persist_run_event

    def fail_resolution(run, event):
        if event.get("type") == "ask_resolved":
            raise OSError("audit write failed")
        return original(run, event)

    monkeypatch.setattr(api_mod, "_persist_run_event", fail_resolution)
    run, _, thread, _ = _registered_pending_run(
        api_mod,
        persistence,
        remember_action=remember,
        rollback_action=rollback,
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": True,
            },
        )
        assert response.status_code == 500
        assert state["remembered"] is False
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_remember_is_never_applied_before_a_durable_start_audit(
    flask_app, isolated_home
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    sid = None
    state = {"remembered": False}

    def remember():
        assert sid is not None
        stored_types = [e["type"] for e in persistence.list_session_events(sid)]
        assert stored_types == ["ask", "ask_remember_started"]
        state["remembered"] = True

    run, sid, thread, _ = _registered_pending_run(
        api_mod,
        persistence,
        remember_action=remember,
        rollback_action=lambda: state.__setitem__("remembered", False),
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": True,
            },
        )
        assert response.status_code == 200
        assert state["remembered"] is True
        stored = persistence.list_session_events(sid)
        stored_types = [e["type"] for e in stored]
        assert stored_types == ["ask", "ask_remember_started", "ask_resolved"]
        started = stored[1]["payload"]
        assert started["grant_id"]
        assert started["agent"] == "build"
        assert started["tool"] == "run_bash"
        assert started["target"] == "pytest -q"
        assert started["key"] == "bash"
        assert stored[2]["payload"]["grant_id"] == started["grant_id"]
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_failed_start_audit_prevents_remember_action(
    flask_app, isolated_home, monkeypatch
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    original = api_mod._persist_run_event
    state = {"remembered": False}

    def fail_start(run, event):
        if event.get("type") == "ask_remember_started":
            raise OSError("start audit unavailable")
        return original(run, event)

    monkeypatch.setattr(api_mod, "_persist_run_event", fail_start)
    run, sid, thread, _ = _registered_pending_run(
        api_mod,
        persistence,
        remember_action=lambda: state.__setitem__("remembered", True),
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": True,
            },
        )
        assert response.status_code == 500
        assert state["remembered"] is False
        assert "ask_remember_started" not in [
            e["type"] for e in persistence.list_session_events(sid)
        ]
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_failed_final_audit_and_rollback_leave_a_durable_start_record(
    flask_app, isolated_home, monkeypatch
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    original = api_mod._persist_run_event
    state = {"remembered": False}

    def remember():
        state["remembered"] = True

    def rollback():
        raise OSError("rollback unavailable")

    def fail_final(run, event):
        if event.get("type") == "ask_resolved":
            raise OSError("final audit unavailable")
        return original(run, event)

    monkeypatch.setattr(api_mod, "_persist_run_event", fail_final)
    run, sid, thread, _ = _registered_pending_run(
        api_mod,
        persistence,
        remember_action=remember,
        rollback_action=rollback,
    )
    ask_id = run.pending_ask["ask_id"]
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": True,
            },
        )
        assert response.status_code == 500
        assert state["remembered"] is True
        assert [e["type"] for e in persistence.list_session_events(sid)] == [
            "ask",
            "ask_remember_started",
        ]
    finally:
        _cleanup_registered_run(api_mod, run, thread)


def test_answer_disconnect_race_reports_actual_denial(flask_app, isolated_home):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision

    class DelayedWakeEvent:
        def __init__(self):
            self._event = threading.Event()
            self.release = threading.Event()
            self.set_called = threading.Event()

        def is_set(self):
            return self._event.is_set()

        def set(self):
            self._event.set()
            self.set_called.set()

        def clear(self):
            self._event.clear()
            self.set_called.clear()

        def wait(self, timeout=None):
            if not self._event.wait(timeout):
                return False
            return self.release.wait(timeout)

    gate = DelayedWakeEvent()
    run, sid, thread, captured = _registered_pending_run(
        api_mod, persistence, event=gate
    )
    ask_id = run.pending_ask["ask_id"]

    def answer():
        return flask_app.test_client().post(
            "/api/agent/runs/testrun/answer",
            json={
                "ask_id": ask_id,
                "approval_token": run.approval_token,
                "decision": "allow",
                "remember": False,
            },
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(answer)
            assert gate.set_called.wait(5)
            with run.state_lock:
                run.cancelled = True
                run.cancel_reason = "disconnect"
            gate.release.set()
            response = future.result(timeout=5)
        thread.join(timeout=5)
        assert response.status_code == 409
        assert captured["decision"] == Decision.DENY
        journal = persistence.list_session_events(sid)
        assert [e["type"] for e in journal] == ["ask", "ask_resolved"]
        assert journal[-1]["payload"]["decision"] == "deny"
        assert journal[-1]["payload"]["reason"] == "disconnect"
    finally:
        gate.release.set()
        _cleanup_registered_run(api_mod, run, thread)


def test_disconnect_while_ask_pending_denies_and_persists_resolution(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = _iter_events(resp)
    session_id = None
    for ev in events:
        if ev["type"] == "session":
            session_id = ev["session_id"]
        elif ev["type"] == "ask":
            break
    resp.response.close()

    deadline = time.time() + 5
    while time.time() < deadline and "ask_result" not in captured:
        time.sleep(0.01)
    assert captured["ask_result"].decision == Decision.DENY
    assert api_mod._AGENT_RUNS == {}
    resolved = [
        e["payload"]
        for e in persistence.list_session_events(session_id)
        if e["type"] == "ask_resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["decision"] == "deny"
    assert resolved[0]["reason"] == "disconnect"
    journal = persistence.list_session_events(session_id)
    assert [e["type"] for e in journal] == ["ask", "ask_resolved", "tool_result"]
    assert journal[-1]["payload"]["ok"] is False


def test_staged_change_event_is_persisted(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    class StagingRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "staged_change", "change_id": "c1", "path": "src/app.py"}
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", StagingRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()
    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "stage", "cwd": str(project)},
    )
    events = list(_iter_events(resp))
    session_id = [e for e in events if e["type"] == "session"][0]["session_id"]
    stored = persistence.list_session_events(session_id)
    assert [e["type"] for e in stored] == ["staged_change"]


def test_staged_change_is_audited_even_after_sse_disconnect(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import asyncio as aio
    import threading

    import backend.api as api_mod
    from backend.cookbook import persistence

    release = threading.Event()
    monkeypatch.setattr(api_mod, "WORKER_JOIN_TIMEOUT", 0.05)

    class LateStagingRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "delta", "content": "working"}
            await aio.get_running_loop().run_in_executor(None, release.wait)
            yield {"type": "staged_change", "change_id": "c-late", "path": "src/late.py"}
            yield {"type": "done", "content": "late", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", LateStagingRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()
    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "stage", "cwd": str(project)},
    )
    events = _iter_events(resp)
    session_id = run_id = None
    try:
        for ev in events:
            if ev["type"] == "session":
                session_id = ev["session_id"]
            elif ev["type"] == "run":
                run_id = ev["run_id"]
            elif ev["type"] == "delta":
                break
        resp.response.close()
    finally:
        release.set()

    deadline = time.time() + 5
    while time.time() < deadline and run_id in api_mod._AGENT_RUNS:
        time.sleep(0.01)
    stored = persistence.list_session_events(session_id)
    assert [e["type"] for e in stored] == ["staged_change"]


def test_agent_run_cancel_requires_exact_capability_and_sets_shared_cancel_event(
    flask_app, isolated_home
):
    import queue
    import threading

    import backend.api as api_mod
    from backend.cookbook import persistence

    session_id = persistence.create_session(name="cancel", model="mock:1b")
    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(),
        session_id=session_id,
        created_at=time.time(),
    )
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["cancel-run"] = run

    try:
        wrong = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": "wrong", "container_id": "client-must-not-control"},
        )
        assert wrong.status_code == 403
        assert not run.cancelled

        cancelled = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
        )
        assert cancelled.status_code == 200
        assert cancelled.get_json() == {"ok": True, "run_id": "cancel-run"}
        assert run.cancelled is True
        assert run.cancel_reason == "user_cancelled"
        assert run.ask_event.is_set()
        assert run.cancel_event.is_set()

        repeated = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
        )
        assert repeated.status_code == 200

        cancelled_events = [
            event
            for event in persistence.list_session_events(session_id)
            if event["type"] == "run_cancelled"
        ]
        assert [event["payload"] for event in cancelled_events] == [
            {
                "type": "run_cancelled",
                "run_id": "cancel-run",
                "reason": "user_cancelled",
            }
        ]
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("cancel-run", None)


def test_agent_run_cancel_replaces_queued_backlog_and_stale_sentinel(
    flask_app, isolated_home
):
    import queue
    import threading

    import backend.api as api_mod
    from backend.cookbook import persistence

    session_id = persistence.create_session(name="cancel backlog", model="mock:1b")
    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(maxsize=4),
        session_id=session_id,
        created_at=time.time(),
    )
    run.queue.put(
        api_mod._PersistedAgentEvent(
            {"type": "tool_result", "name": "old", "result": "old"}
        )
    )
    run.queue.put(api_mod._RUN_SENTINEL)
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["cancel-run"] = run

    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
        )
        assert response.status_code == 200
        queued = run.queue.get_nowait()
        assert isinstance(queued, api_mod._TerminalAgentEvent)
        assert queued.payload == {
            "type": "run_cancelled",
            "run_id": "cancel-run",
            "reason": "user_cancelled",
        }
        with pytest.raises(queue.Empty):
            run.queue.get_nowait()
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("cancel-run", None)


@pytest.mark.parametrize(
    "late_result",
    [
        "error: docker_cleanup_failed: container still present",
        "error: docker_ownership_refused: container labels changed",
        "error: sandbox_internal_error: The local task sandbox failed safely",
    ],
)
def test_agent_run_cancel_emits_terminal_event_and_audits_late_cleanup_failure(
    flask_app, isolated_home, monkeypatch, tmp_path, late_result
):
    import asyncio as aio

    import backend.api as api_mod
    from backend.cookbook import persistence

    monkeypatch.setattr(api_mod, "HEARTBEAT_INTERVAL", 0.05)

    class CancelAwareRunner:
        def __init__(self, *args, **kwargs):
            self.cancel_event = kwargs["ctx"]["cancel_event"]

        async def run_stream(self, user_text, history=None):
            yield {"type": "delta", "content": "working"}
            await aio.get_running_loop().run_in_executor(None, self.cancel_event.wait)
            yield {
                "type": "tool_result",
                "name": "run_task",
                "ok": False,
                "result": late_result,
            }
            yield {"type": "staged_change", "change_id": "must-not-persist", "path": "late.py"}
            yield {"type": "done", "content": "must not escape", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", CancelAwareRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    project = tmp_path / "project"
    project.mkdir()

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(response)
    session_id = run_id = approval_token = None
    for event in events:
        if event["type"] == "session":
            session_id = event["session_id"]
        elif event["type"] == "run":
            run_id = event["run_id"]
            approval_token = event["approval_token"]
        elif event["type"] == "delta":
            break

    cancelled = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/cancel",
        json={"approval_token": approval_token},
    )
    assert cancelled.status_code == 200

    remaining = list(events)
    assert [event for event in remaining if event["type"] == "run_cancelled"] == [
        {
            "type": "run_cancelled",
            "run_id": run_id,
            "reason": "user_cancelled",
        }
    ]
    assert all(event["type"] != "done" for event in remaining)
    assert run_id not in api_mod._AGENT_RUNS
    stored = persistence.list_session_events(session_id)
    assert [event["payload"] for event in stored if event["type"] == "run_cancelled"] == [
        {
            "type": "run_cancelled",
            "run_id": run_id,
            "reason": "user_cancelled",
        }
    ]
    assert [
        event["payload"]
        for event in stored
        if event["type"] == "tool_result" and event["payload"].get("name") == "run_task"
    ] == [
        {
            "type": "tool_result",
            "name": "run_task",
            "ok": False,
            "result": late_result,
        }
    ]
    assert all(event["type"] != "staged_change" for event in stored)


def test_agent_run_cancel_is_loopback_only(flask_app, isolated_home):
    import queue
    import threading

    import backend.api as api_mod

    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(),
        session_id="cancel-session",
        created_at=time.time(),
    )
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["cancel-run"] = run
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
            environ_base={"REMOTE_ADDR": "192.0.2.44"},
        )
        assert response.status_code == 403
        assert not run.cancelled
        assert not run.cancel_event.is_set()
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("cancel-run", None)


def test_agent_run_cancel_still_signals_when_journal_is_unavailable(
    flask_app, isolated_home, monkeypatch
):
    import queue
    import threading

    import backend.api as api_mod

    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(),
        session_id="cancel-session",
        created_at=time.time(),
    )
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["cancel-run"] = run

    def fail_journal(*_args, **_kwargs):
        raise OSError(r"C:\Users\secret-owner\broken-audit.db")

    monkeypatch.setattr(api_mod, "_persist_run_event", fail_journal)
    try:
        response = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
        )

        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "run_id": "cancel-run"}
        assert run.cancelled is True
        assert run.cancel_reason == "user_cancelled"
        assert run.cancel_event.is_set()
        assert run.ask_event.is_set()
        assert run.cancel_journaled is False
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("cancel-run", None)


def test_agent_run_cancel_signals_before_slow_journal_finishes(
    flask_app, isolated_home, monkeypatch
):
    import queue
    import threading

    import backend.api as api_mod

    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(),
        session_id="cancel-session",
        created_at=time.time(),
    )
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["cancel-run"] = run
    journal_entered = threading.Event()
    release_journal = threading.Event()
    response_holder = {}

    def slow_journal(*_args, **_kwargs):
        journal_entered.set()
        assert release_journal.wait(timeout=5)

    def request_cancel():
        response_holder["response"] = flask_app.test_client().post(
            "/api/agent/runs/cancel-run/cancel",
            json={"approval_token": run.approval_token},
        )

    monkeypatch.setattr(api_mod, "_persist_run_event", slow_journal)
    worker = threading.Thread(target=request_cancel)
    worker.start()
    try:
        assert journal_entered.wait(timeout=5)
        assert run.cancelled is True
        assert run.cancel_event.is_set()
        assert run.ask_event.is_set()
    finally:
        release_journal.set()
        worker.join(timeout=5)
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("cancel-run", None)
    assert response_holder["response"].status_code == 200


def test_agent_run_cancel_ends_stream_even_when_runner_ignores_cancel(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import asyncio as aio
    import threading

    import backend.api as api_mod

    release_runner = threading.Event()

    class IgnoringRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "delta", "content": "working"}
            await aio.get_running_loop().run_in_executor(None, release_runner.wait)
            yield {"type": "staged_change", "change_id": "late", "path": "late.py"}

    monkeypatch.setattr(api_mod, "AgentRunner", IgnoringRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(api_mod, "WORKER_JOIN_TIMEOUT", 0.05)
    project = tmp_path / "project"
    project.mkdir()
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(response)
    run_event = None
    for event in events:
        if event["type"] == "run":
            run_event = event
        elif event["type"] == "delta":
            break
    assert run_event is not None

    cancelled = flask_app.test_client().post(
        f"/api/agent/runs/{run_event['run_id']}/cancel",
        json={"approval_token": run_event["approval_token"]},
    )
    assert cancelled.status_code == 200
    remaining = list(events)
    assert [event["type"] for event in remaining] == ["run_cancelled"]

    release_runner.set()
    deadline = time.time() + 5
    while time.time() < deadline and run_event["run_id"] in api_mod._AGENT_RUNS:
        time.sleep(0.01)
    assert run_event["run_id"] not in api_mod._AGENT_RUNS


def test_agent_sandbox_capability_requires_root_and_returns_bounded_public_state(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    project = tmp_path / "project"
    project.mkdir()
    captured: dict = {}

    class Capability:
        def to_dict(self):
            return {
                "backend": "docker",
                "available": False,
                "code": "docker_daemon_unavailable",
                "message": "Docker Desktop is installed but not running.",
                "tasks": [],
                "image": None,
                "network": "none",
            }

    def probe(root):
        captured["root"] = root
        return Capability()

    monkeypatch.setattr(api_mod, "probe_project_sandbox", probe, raising=False)
    missing = flask_app.test_client().get("/api/agent/sandbox")
    assert missing.status_code == 400

    response = flask_app.test_client().get(
        "/api/agent/sandbox", query_string={"cwd": str(project)}
    )
    assert response.status_code == 200
    assert captured["root"] == project.resolve()
    assert response.get_json() == Capability().to_dict()
