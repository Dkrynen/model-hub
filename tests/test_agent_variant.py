from backend.agent_launch.variant import agent_variant_name, ensure_agent_variant


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


def test_ensure_is_idempotent_when_variant_exists():
    calls = []
    def fake_create(name, from_model, params):
        calls.append(name)
    variant = ensure_agent_variant("qwen3:8b", 32768,
                                   list_names=lambda: ["qwen3:8b", "qwen3:8b-agent"],
                                   create=fake_create)
    assert variant == "qwen3:8b-agent"
    assert calls == [], "create must not be called when the variant already exists"


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
