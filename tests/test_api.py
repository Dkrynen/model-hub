from __future__ import annotations

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
