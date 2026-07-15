import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.agent_launch.variant import (
    BaseModelNotInstalled,
    agent_variant_name,
    ensure_agent_variant,
)


def test_agent_variant_name_appends_suffix():
    assert agent_variant_name("qwen3:8b") == "qwen3:8b-agent"
    assert agent_variant_name("llama3.1") == "llama3.1-agent"


def test_ensure_creates_variant_when_absent():
    calls = []
    def fake_create(name, from_model, params):
        calls.append((name, from_model, params))
    variant = ensure_agent_variant("qwen3:8b", 32768,
                                    list_names=lambda: ["qwen3:8b"],
                                    create=fake_create)
    assert variant == "qwen3:8b-agent"
    assert calls == [("qwen3:8b-agent", "qwen3:8b", {"num_ctx": 32768})]


def test_ensure_refuses_when_base_model_is_not_installed():
    """Ollama's /api/create silently PULLS the base from the registry when it is
    not local — an unannounced multi-GB download (18.56GB for qwen3:30b-a3b).
    The seam must refuse rather than let that happen.
    """
    calls = []
    def fake_create(name, from_model, params):
        calls.append(name)

    with pytest.raises(BaseModelNotInstalled) as exc:
        ensure_agent_variant("qwen3:30b-a3b", 32768,
                             list_names=lambda: ["gpt-oss:20b"],   # base absent
                             create=fake_create)

    assert "qwen3:30b-a3b" in str(exc.value)
    assert calls == [], "create must NOT run when the base model is not installed"


def test_ensure_matches_installed_latest_tag():
    """Ollama reports a bare `qwen3` pull as `qwen3:latest`; treat them as the same."""
    calls = []
    def fake_create(name, from_model, params):
        calls.append(name)
    variant = ensure_agent_variant("qwen3", 32768,
                                   list_names=lambda: ["qwen3:latest"],
                                   create=fake_create)
    assert variant == "qwen3-agent"
    assert calls == ["qwen3-agent"]


def test_ensure_rebuilds_an_existing_variant_so_num_ctx_always_matches():
    """An existing `-agent` variant may carry a stale num_ctx from an earlier run,
    which would leave the agent running a context the launcher did not ask for (and
    reports wrongly). Rebuilding is idempotent and instant (~0.05s, no download)
    when the base is local, so always restate the parameters.
    """
    calls = []
    def fake_create(name, from_model, params):
        calls.append((name, from_model, params))

    variant = ensure_agent_variant("qwen3:8b", 65536,
                                   list_names=lambda: ["qwen3:8b", "qwen3:8b-agent"],
                                   create=fake_create)

    assert variant == "qwen3:8b-agent"
    assert calls == [("qwen3:8b-agent", "qwen3:8b", {"num_ctx": 65536})], \
        "existing variant must be rebuilt with the requested num_ctx"


class _FakeResp:
    """Stand-in for the urlopen response _request returns (context manager + read)."""
    def __init__(self):
        self.read_called = False
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        self.read_called = True
        return b'{"status":"success"}'


def test_ollama_create_posts_correct_body_and_consumes_response(monkeypatch):
    from backend.provider.ollama import OllamaProvider
    captured = {}
    resp = _FakeResp()
    p = OllamaProvider(base_url="http://localhost:11434")

    def fake_request(method, path, body=None, timeout=30, stream=False):
        captured.update(method=method, path=path, body=body, timeout=timeout)
        return resp

    monkeypatch.setattr(p, "_request", fake_request)
    p.create("qwen3:8b-agent", "qwen3:8b", {"num_ctx": 32768})

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/create"
    assert captured["body"] == {"model": "qwen3:8b-agent", "from": "qwen3:8b",
                                "parameters": {"num_ctx": 32768}, "stream": False}
    assert resp.read_called, "create must consume the response so the build completes"


class _StubOllamaHandler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _StubOllamaHandler.received = json.loads(self.rfile.read(length).decode())
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"success"}')

    def log_message(self, *args):
        pass  # keep pytest output pristine


@pytest.fixture
def stub_ollama():
    server = HTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_create_completes_over_a_real_socket(stub_ollama):
    """create() must use a blocking socket.

    A timeout of 0 puts urllib's socket into NON-BLOCKING mode, so connect()
    fails instantly (BlockingIOError / WinError 10035) instead of waiting.
    The monkeypatched tests above cannot catch this — they never open a socket.
    """
    from backend.provider.ollama import OllamaProvider

    _StubOllamaHandler.received = {}
    port = stub_ollama.server_address[1]
    p = OllamaProvider(base_url=f"http://127.0.0.1:{port}")

    p.create("qwen3:8b-agent", "qwen3:8b", {"num_ctx": 32768})

    assert _StubOllamaHandler.received == {
        "model": "qwen3:8b-agent",
        "from": "qwen3:8b",
        "parameters": {"num_ctx": 32768},
        "stream": False,
    }
