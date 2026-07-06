import backend.api as api


def _client():
    return api.app.test_client()


def test_activate_happy_path(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin", lambda k: {"state": "installed", "path": "x"})
    captured = {}
    class R:  # fake CompletedProcess
        returncode = 0
        stdout = "  activated — LAC Pro unlocked on this machine"
        stderr = ""
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return R()
    monkeypatch.setattr(api.proc, "run", fake_run)
    r = _client().post("/api/pro/activate", json={"key": "SECRET-KEY"})
    assert r.get_json() == {"state": "activated"}
    # key travels via stdin, NEVER in argv:
    assert "SECRET-KEY" not in " ".join(captured["cmd"])
    assert captured["input"].strip() == "SECRET-KEY"
    assert captured["cmd"][-2:] == ["pro", "activate"]


def test_activate_install_failure_passthrough(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin",
                        lambda k: {"state": "failed", "error_type": "invalid_key", "message": "bad key"})
    r = _client().post("/api/pro/activate", json={"key": "x"})
    body = r.get_json()
    assert body["state"] == "install_failed"
    assert body["error_type"] == "invalid_key"


def test_activate_subprocess_failure(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin", lambda k: {"state": "installed", "path": "x"})
    class R:
        returncode = 1
        stdout = "  activation rejected (status: expired)"
        stderr = ""
    monkeypatch.setattr(api.proc, "run", lambda cmd, **kw: R())
    body = _client().post("/api/pro/activate", json={"key": "x"}).get_json()
    assert body["state"] == "activation_failed"
    assert "expired" in body["message"] or body["message"]


def test_activate_missing_key():
    assert _client().post("/api/pro/activate", json={}).status_code == 400
