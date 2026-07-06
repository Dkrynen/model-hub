import json, sys, types
import backend.desktop as desktop


def test_load_window_state_missing_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "nope.json")
    assert desktop.load_window_state() == {"bounds": {}, "view": ""}


def test_load_window_state_corrupt_returns_defaults(tmp_path, monkeypatch):
    p = tmp_path / "s.json"; p.write_text("{ not json")
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", p)
    assert desktop.load_window_state() == {"bounds": {}, "view": ""}


def test_open_window_applies_saved_view(monkeypatch, tmp_path):
    p = tmp_path / "s.json"; p.write_text(json.dumps({"bounds": {"x": 5, "y": 6, "width": 900, "height": 700}, "view": "settings"}))
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", p)
    calls = {}
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: calls.setdefault("args", (a, k))
    fake.start = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    desktop._open_window("127.0.0.1", 5050)
    (title, url), kw = calls["args"]
    assert "view=settings" in url
    assert kw.get("x") == 5 and kw.get("width") == 900
