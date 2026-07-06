import sys
import types
import pytest
from backend import desktop


def test_open_window_falls_back_when_webview_import_fails(monkeypatch):
    # Simulate no webview module available
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(desktop, "_show_dialog", lambda *a, **k: None)
    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.setdefault("url", url))
    rc = desktop._open_window("127.0.0.1", 5050)
    assert rc == 0
    assert opened["url"] == "http://127.0.0.1:5050"


def test_open_window_falls_back_when_start_raises(monkeypatch):
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: None
    def _boom(*a, **k):
        raise RuntimeError("WebView2 runtime missing")
    fake.start = _boom
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "_show_dialog", lambda *a, **k: None)
    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.setdefault("url", url))
    rc = desktop._open_window("127.0.0.1", 5050)
    assert rc == 0
    assert opened["url"] == "http://127.0.0.1:5050"
