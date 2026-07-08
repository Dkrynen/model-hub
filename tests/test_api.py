from __future__ import annotations

import json

import pytest


def test_index_serves_html(flask_app):
    client = flask_app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"LAC" in r.data or b"lac" in r.data


def test_docs_route(flask_app):
    client = flask_app.test_client()
    for path in ("/docs", "/docs/api", "/docs/guide"):
        r = client.get(path)
        assert r.status_code == 200


def test_system_version(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/system/version")
    assert r.status_code == 200
    data = r.get_json()
    assert data["version"]


def test_system_storage_reports_on_demand_model_policy(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.get("/api/system/storage")
    assert r.status_code == 200
    data = r.get_json()
    assert data["model_install_mode"] == "on_demand_ollama_pull"
    assert data["models_are_bundled"] is False
    assert data["model_weight_files_in_app"] == []
    assert data["ollama_models_dir"]


def test_system_check_update(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/system/check-update")
    assert r.status_code == 200


def test_scan(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/scan")
    assert r.status_code == 200
    data = r.get_json()
    assert "cpu" in data or "os" in data


def test_workspaces_list(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/workspaces")
    assert r.status_code == 200


def test_sessions_crud(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/sessions", json={"model": "llama3.2:3b"})
    assert r.status_code in (200, 201)
    sid = r.get_json().get("id") or r.get_json().get("session_id")
    if sid:
        r2 = client.get(f"/api/sessions/{sid}")
        assert r2.status_code == 200


def test_ollama_status(flask_app, ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    client = flask_app.test_client()
    r = client.get("/api/ollama/status")
    assert r.status_code == 200


def test_openapi_endpoint(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    spec = r.get_json()
    assert spec["openapi"] == "3.1.0"
    assert "/api/system/version" in spec["paths"]


def test_recommend_serializes_speed_source(flask_app, isolated_home):
    """Each recommendation must carry speed_source + speed_band_pct so the
    web UI can tag measured/calibrated/estimated values."""
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3")
    assert r.status_code == 200
    data = r.get_json()
    assert "recommendations" in data and len(data["recommendations"]) > 0
    for rec in data["recommendations"]:
        assert rec["speed_source"] in ("measured", "calibrated", "estimated")
        assert isinstance(rec["speed_band_pct"], (int, float))
        assert rec["speed_band_pct"] > 0  # never a zero-width band


def test_recommend_no_calibration_escape_hatch(flask_app, isolated_home):
    """?no_calibration=1 must still return recs, all tagged 'estimated'."""
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3&no_calibration=1")
    assert r.status_code == 200
    for rec in r.get_json()["recommendations"]:
        assert rec["speed_source"] == "estimated"


def test_api_benchmark_route_removed(flask_app):
    """The free-tier web benchmark surface is gone entirely — benchmarking
    only happens through LAC Pro's autopilot from now on (spec decision 1).

    This app serves the SPA build from a catch-all static route mounted at
    "/", so any POST to a path with no registered POST handler resolves to
    405 (Werkzeug finds the static GET/HEAD/OPTIONS rule for the URL, then
    rejects the method) rather than a bare 404 — same as any other removed
    or never-existed /api/* POST route in this app. What matters here is
    that it's no longer a working 200 benchmark stream.
    """
    r = flask_app.test_client().post("/api/benchmark", json={"model": "m:1b"})
    assert r.status_code == 405


def _fake_detect_factory():
    """Return a factory that builds a fresh 2-GPU SystemInfo per detect() call.

    Mirrors real detector output: GPUInfo.device_index is left at its default
    (0) for every GPU -- build_compute_tiers() is responsible for assigning
    real, unique indices, just like it does on real hardware.
    """
    from backend.cookbook.hardware import SystemInfo, GPUInfo, build_compute_tiers

    def make():
        gpus = [
            GPUInfo(name="Big GPU", vram_gb=16.0, backend="cuda"),
            GPUInfo(name="Small GPU", vram_gb=4.0, backend="cuda"),
        ]
        return SystemInfo(
            os="Test", cpu="Test CPU", cpu_cores=8, ram_gb=64.0,
            gpus=gpus, total_vram_gb=16.0, combined_vram_gb=20.0,
            compute_tiers=build_compute_tiers(gpus, 64.0, False),
        )

    return make


def test_recommend_gpu_mask_reduces_combined_vram(monkeypatch, flask_app, isolated_home):
    from backend import api as api_mod
    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())

    client = flask_app.test_client()
    r_all = client.get("/api/recommend?use_case=general&top_k=3")
    assert r_all.get_json()["combined_vram_gb"] == 20.0

    r_masked = client.get("/api/recommend?use_case=general&top_k=3&gpu_mask=0")
    assert r_masked.get_json()["combined_vram_gb"] == 16.0


def test_recommend_no_spill_zeroes_ram(monkeypatch, flask_app, isolated_home):
    from backend import api as api_mod
    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())

    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=general&top_k=10&allow_spill=0")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ram_gb"] == 0.0
    for rec in data["recommendations"]:
        assert rec["run_mode"] != "cpu_offload"


def test_recommend_gpu_mask_isolates_second_gpu_via_assigned_index(monkeypatch, flask_app, isolated_home):
    """Real-shape regression: the fake GPUInfo objects never hand-set
    device_index (defaults only, like real detectors). build_compute_tiers
    must assign real indices so gpu_mask=1 actually isolates the second GPU
    (the 4.0 GB 'Small GPU'), not silently fail to filter."""
    from backend import api as api_mod
    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())

    client = flask_app.test_client()
    r_masked = client.get("/api/recommend?use_case=general&top_k=3&gpu_mask=1")
    assert r_masked.status_code == 200
    assert r_masked.get_json()["combined_vram_gb"] == 4.0


def test_recommend_gpu_mask_unmatched_is_ignored(monkeypatch, flask_app, isolated_home):
    """A mask that matches zero real GPU indices must be ignored entirely --
    never serve a zero-GPU result because of a bad/stale mask."""
    from backend import api as api_mod
    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())

    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=general&top_k=3&gpu_mask=99")
    assert r.status_code == 200
    assert r.get_json()["combined_vram_gb"] == 20.0


def test_recommend_gpu_mask_malformed_entries_dropped_then_ignored(monkeypatch, flask_app, isolated_home):
    """Malformed mask entries are dropped; if nothing valid remains, the mask
    is a no-op (full unmasked result), not a zero-GPU result."""
    from backend import api as api_mod
    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())

    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=general&top_k=3&gpu_mask=abc,,-1")
    assert r.status_code == 200
    assert r.get_json()["combined_vram_gb"] == 20.0


def test_switch_workspace_succeeds_for_valid_id(flask_app, isolated_home):
    client = flask_app.test_client()
    client.get("/api/workspaces")  # ensures the default workspace exists on disk
    r = client.post("/api/workspaces/default/switch")
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "workspace": "default"}


def test_switch_workspace_404_for_unknown_id(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/workspaces/does-not-exist/switch")
    assert r.status_code == 404


def test_ollama_status_reports_real_version(monkeypatch, flask_app):
    from backend import api as api_mod

    def fake_request(method, path, json_body=None, stream=False):
        assert path == "/api/version"
        return {"version": "0.31.1"}

    monkeypatch.setattr(api_mod, "_ollama_request", fake_request)
    client = flask_app.test_client()
    r = client.get("/api/ollama/status")
    assert r.status_code == 200
    assert r.get_json() == {"running": True, "version": "0.31.1"}


def test_ollama_pull_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post("/api/ollama/pull", json=["a", "b"])
    assert r.status_code == 400
    assert r.get_json()["error"] == "No model specified"


def test_ollama_delete_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post(
        "/api/ollama/delete", data="null", content_type="application/json"
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "No model specified"


def test_ollama_chat_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post("/api/ollama/chat", json="not-a-dict")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Model and messages required"


def test_ollama_delete_reports_failure_when_ollama_errors(monkeypatch, flask_app):
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod, "_ollama_request",
        lambda method, path, json_body=None, stream=False: {"error": "model 'x' not found"},
    )
    r = flask_app.test_client().post("/api/ollama/delete", json={"model": "x"})
    assert r.status_code == 500
    assert r.get_json().get("success") is not True


def test_malformed_json_returns_json_error_not_html(flask_app):
    r = flask_app.test_client().put("/api/config", data="{not valid json", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json() is not None
    assert "error" in r.get_json()


def test_method_not_allowed_returns_json_error_not_html(flask_app):
    r = flask_app.test_client().post("/api/benchmark", json={"model": "m:1b"})
    assert r.status_code == 405
    assert r.get_json() is not None
    assert "error" in r.get_json()


def test_recommend_manual_vram_override_updates_combined_vram(monkeypatch, flask_app, isolated_home):
    from backend import api as api_mod
    from backend.cookbook.hardware import SystemInfo

    monkeypatch.setattr(api_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[], total_vram_gb=0.0, combined_vram_gb=0.0, compute_tiers=[],
    ))

    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=general&top_k=3&vram=8")
    assert r.status_code == 200
    data = r.get_json()
    assert data["vram_gb"] == 8.0
    assert data["combined_vram_gb"] == 8.0


def test_check_update_uses_lac_repo_and_useragent(monkeypatch, flask_app):
    import urllib.request as real_urllib_request

    captured = {}

    class FakeResp:
        def read(self):
            return b'{"tag_name": "v9.9.9", "html_url": "x", "body": ""}'

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["ua"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    client = flask_app.test_client()
    r = client.get("/api/system/check-update?current=0.0.0")
    assert r.status_code == 200
    assert captured["url"] == "https://api.github.com/repos/Dkrynen/lac/releases/latest"
    assert captured["ua"].startswith("LAC/")
    assert captured["ua"] != "model-hub/1.0"


def test_hf_gguf_search_maps_public_metadata(monkeypatch, flask_app):
    import urllib.request as real_urllib_request
    from backend import api as api_mod
    from backend.cookbook.hardware import SystemInfo

    api_mod._HF_DETAIL_CACHE.clear()
    captured = {}
    search_body = json.dumps([
        {
            "id": "org/model-GGUF",
            "author": "org",
            "downloads": 123,
            "likes": 4,
            "gated": False,
            "lastModified": "2026-01-01T00:00:00Z",
            "tags": ["gguf", "text-generation", "license:apache-2.0"],
            "siblings": [
                {"rfilename": "model-Q4_K_M.gguf"},
                {"rfilename": "model-Q8_0.gguf"},
                {"rfilename": "README.md"},
            ],
        },
        {
            "id": "org/not-gguf",
            "tags": ["safetensors"],
            "siblings": [{"rfilename": "model.safetensors"}],
        },
    ]).encode()
    detail_body = json.dumps({
        "id": "org/model-GGUF",
        "author": "org",
        "downloads": 123,
        "likes": 4,
        "gated": False,
        "lastModified": "2026-01-01T00:00:00Z",
        "tags": ["gguf", "text-generation", "license:apache-2.0", "base_model:org/base"],
        "pipeline_tag": "text-generation",
        "cardData": {"license": "apache-2.0", "base_model": "org/base"},
        "siblings": [
            {"rfilename": "model-Q4_K_M.gguf", "size": 4_000_000_000},
            {"rfilename": "model-Q8_0.gguf", "size": 8_000_000_000},
            {"rfilename": "README.md", "size": 1000},
        ],
    }).encode()

    class FakeResp:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(req, timeout=12):
        captured.setdefault("urls", []).append(req.full_url)
        captured["ua"] = req.get_header("User-agent")
        if "/api/models/org/model-GGUF" in req.full_url:
            return FakeResp(detail_body)
        return FakeResp(search_body)

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)
    monkeypatch.setattr(api_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[], total_vram_gb=6.0, combined_vram_gb=6.0, compute_tiers=[],
    ))

    r = flask_app.test_client().get("/api/hf/gguf-search?q=qwen&limit=5")
    assert r.status_code == 200
    data = r.get_json()
    assert data["query"] == "qwen"
    assert data["total"] == 1
    assert data["models"][0]["repo_id"] == "org/model-GGUF"
    assert data["models"][0]["gguf_files"] == 2
    assert data["models"][0]["quants"] == ["Q4_K_M", "Q8_0"]
    assert data["models"][0]["license"] == "apache-2.0"
    assert data["models"][0]["base_model"] == "org/base"
    assert data["models"][0]["recommended_quant"] == "Q4_K_M"
    assert data["models"][0]["recommended_size_gb"] == 3.73
    assert data["models"][0]["fit"] == "fits"
    assert data["models"][0]["files"][0]["filename"] == "model-Q4_K_M.gguf"
    assert data["models"][0]["files"][0]["selection"] == "model-Q4_K_M.gguf"
    assert data["models"][0]["files"][0]["vram_gb"] == 4.65
    assert data["system_vram"] == 6.0
    assert any("qwen+gguf" in url for url in captured["urls"])
    assert any("/api/models/org/model-GGUF" in url for url in captured["urls"])
    assert captured["ua"].startswith("LAC/")


def test_fetch_hf_model_detail_uses_short_ttl_cache(monkeypatch):
    import urllib.request as real_urllib_request
    from backend import api as api_mod

    api_mod._HF_DETAIL_CACHE.clear()
    calls = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"id":"org/model-GGUF","siblings":[]}'

    def fake_urlopen(req, timeout=8):
        calls.append(req.full_url)
        return FakeResp()

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    assert api_mod._fetch_hf_model_detail("org/model-GGUF")["id"] == "org/model-GGUF"
    assert api_mod._fetch_hf_model_detail("org/model-GGUF")["id"] == "org/model-GGUF"
    assert len(calls) == 1


def test_hf_gguf_search_empty_query_is_local_only(flask_app):
    r = flask_app.test_client().get("/api/hf/gguf-search")
    assert r.status_code == 200
    assert r.get_json() == {"query": "", "total": 0, "models": []}


# --- POST /api/pro/unlock (web "Activate Pro" -> bootstrap-install the plugin) ---
# The route is the browser twin of `lac unlock`: it hands the license key to
# install_pro_plugin (which NEVER raises) and returns that helper's honest dict
# verbatim at HTTP 200 -- the frontend branches on `state`. A 400 is reserved
# strictly for a malformed request body (missing / non-string key).


def test_pro_unlock_installed_returns_200_with_body(monkeypatch, flask_app):
    """A successful bootstrap returns install_pro_plugin's dict verbatim at 200,
    and the submitted key is threaded through to the helper."""
    from backend import api as api_mod

    captured = {}

    def fake_install(key, **kwargs):
        captured["key"] = key
        return {"state": "installed", "path": "/home/u/.model-hub/plugins"}

    monkeypatch.setattr(api_mod, "install_pro_plugin", fake_install)
    r = flask_app.test_client().post("/api/pro/unlock", json={"key": "LAC-PRO-123"})
    assert r.status_code == 200
    assert r.get_json() == {"state": "installed", "path": "/home/u/.model-hub/plugins"}
    assert captured["key"] == "LAC-PRO-123"


def test_pro_unlock_failed_returns_200_with_honest_body(monkeypatch, flask_app):
    """A failed install is NOT an HTTP error: 200 with the honest failure body
    (state/error_type/message) so the UI can surface the real message."""
    from backend import api as api_mod

    failure = {
        "state": "failed",
        "error_type": "invalid_key",
        "message": "Your license key was not accepted (invalid or expired).",
    }
    monkeypatch.setattr(api_mod, "install_pro_plugin", lambda key, **kw: failure)
    r = flask_app.test_client().post("/api/pro/unlock", json={"key": "bad-key"})
    assert r.status_code == 200
    assert r.get_json() == failure


def test_pro_unlock_missing_key_returns_400(monkeypatch, flask_app):
    """A body with no key is malformed -> 400, and the installer is never called."""
    from backend import api as api_mod

    called = {"n": 0}
    monkeypatch.setattr(
        api_mod, "install_pro_plugin",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"state": "installed"},
    )
    r = flask_app.test_client().post("/api/pro/unlock", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()
    assert called["n"] == 0


def test_pro_unlock_non_string_key_returns_400(monkeypatch, flask_app):
    """A non-string key is malformed -> 400 (never handed to the installer)."""
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod, "install_pro_plugin",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer must not run")),
    )
    r = flask_app.test_client().post("/api/pro/unlock", json={"key": 123})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_pro_unlock_non_dict_body_returns_400(flask_app):
    """A non-dict JSON body is malformed -> 400 (mirrors the other POST guards)."""
    r = flask_app.test_client().post("/api/pro/unlock", json=["not", "a", "dict"])
    assert r.status_code == 400
    assert "error" in r.get_json()
