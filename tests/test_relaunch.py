import json
import backend.desktop as desktop


def test_save_window_state_writes_under_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "window_state.json")
    desktop.save_window_state({"x": 10, "y": 20, "width": 1200, "height": 800}, "settings")
    data = json.loads((tmp_path / "window_state.json").read_text())
    assert data["view"] == "settings"
    assert data["bounds"]["width"] == 1200


def test_relaunch_spawns_then_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(desktop, "save_window_state", lambda *a, **k: None)
    spawned = {}
    monkeypatch.setattr(desktop.proc, "popen", lambda cmd, **k: spawned.setdefault("cmd", cmd))
    exited = {}
    monkeypatch.setattr(desktop.os, "_exit", lambda code: exited.setdefault("code", code))
    ok = desktop.relaunch(view="browse")
    assert spawned["cmd"][-1] == "--window"
    assert exited["code"] == 0


def test_relaunch_failure_does_not_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "save_window_state", lambda *a, **k: None)
    def boom(cmd, **k):
        raise OSError("spawn failed")
    monkeypatch.setattr(desktop.proc, "popen", boom)
    called = {}
    monkeypatch.setattr(desktop.os, "_exit", lambda code: called.setdefault("code", code))
    assert desktop.relaunch() is False
    assert "code" not in called          # never exits on spawn failure
