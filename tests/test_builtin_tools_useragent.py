from __future__ import annotations

from backend.plugin.builtins.tools import _web_search
import backend.plugin.builtins.tools as tools_mod


def test_web_search_sends_lac_user_agent(monkeypatch):
    captured = {}

    class FakeResp:
        def read(self):
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        captured["ua"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr(tools_mod.urllib.request, "urlopen", fake_urlopen)
    _web_search({"query": "test query"}, {})

    assert captured["ua"] == "LAC/2.2.0"
