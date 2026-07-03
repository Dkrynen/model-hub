"""API plugin mounting + /api/plugins listing."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def test_api_plugins_endpoint_lists(monkeypatch, flask_app):
    plug = SimpleNamespace(name="fake", version="9.9")
    monkeypatch.setattr(plugins_mod, "discover", lambda: [
        LoadedPlugin("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="nope"),
    ])
    client = flask_app.test_client()
    r = client.get("/api/plugins")
    assert r.status_code == 200
    data = r.get_json()
    assert {p["name"] for p in data} == {"fake", "broken"}
    assert next(p for p in data if p["name"] == "broken")["ok"] is False


def test_register_api_mounts_routes(monkeypatch, flask_app):
    def register_api(app):
        @app.route("/api/pro/ping")
        def _pro_ping():
            return {"pong": True}

    plug = SimpleNamespace(name="fake", version="9.9", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "9.9", plug)])

    # flask_app is a shared module-level app; a prior test in this session may
    # have already dispatched a request against it, which locks route
    # registration. In production _mount_plugins(app) runs once at import
    # time, before any request is served — replicate that ordering here.
    monkeypatch.setattr(flask_app, "_got_first_request", False)

    from backend.api import _mount_plugins
    _mount_plugins(flask_app)
    client = flask_app.test_client()
    assert client.get("/api/pro/ping").get_json() == {"pong": True}


def test_broken_register_api_is_isolated(monkeypatch, flask_app):
    def register_api(app):
        raise RuntimeError("boom")

    plug = SimpleNamespace(name="bad", version="0.0", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("bad", "0.0", plug)])
    from backend.api import _mount_plugins
    _mount_plugins(flask_app)  # must not raise
