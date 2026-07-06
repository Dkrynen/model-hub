import sys
import types
import pytest
from backend import desktop


def test_wait_until_serving_true_when_up(monkeypatch):
    monkeypatch.setattr(desktop, "_serving", lambda h, p: True)
    assert desktop._wait_until_serving("127.0.0.1", 5050, timeout=1.0) is True


def test_wait_until_serving_false_on_timeout(monkeypatch):
    monkeypatch.setattr(desktop, "_serving", lambda h, p: False)
    assert desktop._wait_until_serving("127.0.0.1", 5050, timeout=0.3) is False


def test_launch_desktop_creates_window_when_serving(monkeypatch):
    calls = {}
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: calls.setdefault("create", (a, k))
    fake.start = lambda *a, **k: calls.setdefault("start", True)
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "_set_taskbar_identity", lambda: None)
    monkeypatch.setattr(desktop, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "_start_server_thread", lambda h, p: None)
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda h, p, timeout=20.0: True)
    monkeypatch.setattr(desktop, "load_window_state", lambda: {"bounds": {}, "view": ""})
    rc = desktop.launch_desktop("127.0.0.1", 5050)
    assert rc == 0
    assert calls["create"][0][0] == "LAC"
    assert calls["create"][0][1] == "http://127.0.0.1:5050"
    assert calls.get("start") is True


def test_launch_desktop_returns_1_when_server_never_starts(monkeypatch):
    monkeypatch.setattr(desktop, "_set_taskbar_identity", lambda: None)
    monkeypatch.setattr(desktop, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "_start_server_thread", lambda h, p: None)
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda h, p, timeout=20.0: False)
    monkeypatch.setattr(desktop, "_show_startup_error", lambda *a, **k: None)
    assert desktop.launch_desktop("127.0.0.1", 5050) == 1


def test_should_use_window_defaults_to_frozen(monkeypatch):
    import server
    ns = lambda **k: type("NS", (), {"window": False, "no_window": False, **k})()
    monkeypatch.setattr(server.sys, "frozen", True, raising=False)
    assert server._should_use_window(ns()) is True
    assert server._should_use_window(ns(no_window=True)) is False
    monkeypatch.setattr(server.sys, "frozen", False, raising=False)
    assert server._should_use_window(ns()) is False
    assert server._should_use_window(ns(window=True)) is True
