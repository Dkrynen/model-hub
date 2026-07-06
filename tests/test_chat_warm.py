from __future__ import annotations

import json

import backend.api as api_mod


# --- POST /api/ollama/warm ---------------------------------------------
# Preloads a model into VRAM off the chat critical path (fire-and-forget)
# so the first chat message doesn't pay the ~4.5s cold-load penalty.

def test_warm_missing_model_returns_400(flask_app):
    r = flask_app.test_client().post("/api/ollama/warm", json={})
    assert r.status_code == 400


def test_warm_accepts_and_runs_in_background(monkeypatch, flask_app):
    captured = {}

    def fake_warm(model):
        captured["model"] = model

    monkeypatch.setattr(api_mod, "_warm_ollama", fake_warm)

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(api_mod.threading, "Thread", FakeThread)

    r = flask_app.test_client().post("/api/ollama/warm", json={"model": "m"})
    assert r.status_code == 200
    assert r.get_json() == {"accepted": True}
    assert captured["model"] == "m"


def test_warm_ollama_sends_keep_alive_to_generate_endpoint(monkeypatch):
    import urllib.request as real_urllib_request

    captured = {}

    class FakeResp:
        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=120):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    api_mod._warm_ollama("m")

    assert captured["url"].endswith("/api/generate")
    assert captured["data"]["keep_alive"] == "30m"
    assert captured["data"]["model"] == "m"


def test_warm_ollama_never_raises_on_failure(monkeypatch):
    import urllib.request as real_urllib_request

    def fake_urlopen(req, timeout=120):
        raise OSError("connection refused")

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    # Must not raise.
    api_mod._warm_ollama("m")


# --- POST /api/ollama/chat keeps the model resident ---------------------

def test_ollama_chat_sends_keep_alive(monkeypatch, flask_app):
    import urllib.request as real_urllib_request

    captured = {}

    class FakeResp:
        def __iter__(self):
            return iter([b'{"message":{"content":"hi"},"done":true}\n'])

    def fake_urlopen(req, timeout=300):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    client = flask_app.test_client()
    r = client.post(
        "/api/ollama/chat",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    # Consume the streamed response body to trigger generate().
    r.get_data()

    assert captured["url"].endswith("/api/chat")
    assert captured["data"]["keep_alive"] == "30m"
    assert captured["data"]["model"] == "m"
