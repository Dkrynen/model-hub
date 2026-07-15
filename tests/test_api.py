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
    assert "ollama_models_user_configured" in data


def test_model_store_doctor_reports_scratch_and_warnings(flask_app, isolated_home, monkeypatch, tmp_path):
    from backend import api as api_mod

    models = tmp_path / "models"
    scratch = tmp_path / "lac-hf-import-tmp"
    default_store = tmp_path / "default" / ".ollama" / "models"
    models.mkdir(parents=True)
    scratch.mkdir()
    default_store.mkdir(parents=True)
    (scratch / "partial.gguf").write_bytes(b"x" * 10)
    (default_store / "old.gguf").write_bytes(b"x" * 20)

    monkeypatch.setattr(api_mod, "_default_ollama_models_dir", lambda: models)
    monkeypatch.setattr(api_mod, "_hf_import_scratch_root", lambda: scratch)
    monkeypatch.setattr(api_mod.Path, "home", lambda: tmp_path / "default")
    monkeypatch.setattr(api_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(api_mod, "_disk_usage_payload", lambda path: {
        "free_bytes": 5 * 1024**3,
        "total_bytes": 100 * 1024**3,
        "used_bytes": 95 * 1024**3,
        "free_gb": 5.0,
        "total_gb": 100.0,
        "used_gb": 95.0,
    })

    r = flask_app.test_client().get("/api/system/model-store-doctor")

    assert r.status_code == 200
    data = r.get_json()
    assert data["state"] == "critical"
    assert data["model_store"]["path"] == str(models)
    assert data["import_scratch"]["path"] == str(scratch)
    assert data["import_scratch"]["size_bytes"] == 10
    assert data["import_scratch"]["safe_to_clear"] is True
    assert any(a["kind"] == "clear_import_scratch" for a in data["actions"])
    assert any("default Ollama model folder" in warning for warning in data["warnings"])


def test_clear_import_scratch_deletes_contents_only(flask_app, isolated_home, monkeypatch, tmp_path):
    from backend import api as api_mod

    scratch = tmp_path / "lac-hf-import-tmp"
    nested = scratch / "repo"
    nested.mkdir(parents=True)
    (nested / "partial.gguf").write_bytes(b"x" * 10)
    (scratch / "note.txt").write_text("ok")

    monkeypatch.setattr(api_mod, "_hf_import_scratch_root", lambda: scratch)

    r = flask_app.test_client().delete("/api/system/import-scratch")

    assert r.status_code == 200
    data = r.get_json()
    assert data["state"] == "cleared"
    assert data["deleted_entries"] == 2
    assert data["deleted_bytes"] == 12
    assert scratch.exists()
    assert list(scratch.iterdir()) == []


def test_clear_import_scratch_refuses_unsafe_path(flask_app, isolated_home, monkeypatch, tmp_path):
    from backend import api as api_mod

    monkeypatch.setattr(api_mod, "_hf_import_scratch_root", lambda: tmp_path)

    r = flask_app.test_client().delete("/api/system/import-scratch")

    assert r.status_code == 400
    data = r.get_json()
    assert data["state"] == "failed"
    assert "unsafe" in data["error"]


def test_model_location_reports_default(flask_app, isolated_home, monkeypatch):
    from backend import api as api_mod

    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(api_mod, "_read_user_env_var", lambda name: None)

    r = flask_app.test_client().get("/api/system/model-location")

    assert r.status_code == 200
    data = r.get_json()
    assert data["state"] == "ok"
    assert data["env_var"] == "OLLAMA_MODELS"
    assert data["configured"] is False
    assert data["process_configured"] is False
    assert data["effective_after_restart"] == data["default_dir"]
    assert data["moves_existing_models"] is False


def test_model_location_sets_user_env_without_touching_models(flask_app, isolated_home, monkeypatch, tmp_path):
    from backend import api as api_mod

    written = {}

    def fake_write(name, value):
        written[name] = value

    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(api_mod, "_read_user_env_var", lambda name: written.get(name))
    monkeypatch.setattr(api_mod, "_write_user_env_var", fake_write)

    target = tmp_path / "ollama-models"
    r = flask_app.test_client().put("/api/system/model-location", json={"path": str(target)})

    assert r.status_code == 200
    assert target.is_dir()
    data = r.get_json()
    assert written["OLLAMA_MODELS"] == str(target.resolve())
    assert data["configured_dir"] == str(target.resolve())
    assert data["effective_after_restart"] == str(target.resolve())
    assert data["restart_ollama_required"] is True
    assert data["moves_existing_models"] is False


def test_model_location_reset_removes_user_env(flask_app, isolated_home, monkeypatch):
    from backend import api as api_mod

    written = {"OLLAMA_MODELS": "D:\\Models"}

    def fake_write(name, value):
        if value is None:
            written.pop(name, None)
        else:
            written[name] = value

    monkeypatch.setattr(api_mod, "_read_user_env_var", lambda name: written.get(name))
    monkeypatch.setattr(api_mod, "_write_user_env_var", fake_write)

    r = flask_app.test_client().put("/api/system/model-location", json={"reset": True})

    assert r.status_code == 200
    assert "OLLAMA_MODELS" not in written
    assert r.get_json()["configured"] is False


def test_system_debug_bundle_is_sanitized(monkeypatch, flask_app, isolated_home):
    from backend import api as api_mod

    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://secret-gate.example/pro/download")
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setenv("OLLAMA_HOST", "http://user:ollama-secret@localhost:11434")
    monkeypatch.setattr(api_mod, "_ollama_request", lambda method, path, json_body=None, stream=False: {
        "/api/version": {"version": "0.31.1"},
        "/api/ps": {"models": []},
        "/api/tags": {"models": [{"name": "tiny:latest", "size": 1234, "modified_at": "now"}]},
    }.get(path, {}))

    client = flask_app.test_client()
    r = client.get("/api/system/debug-bundle")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    data = r.get_json()
    raw = json.dumps(data)
    assert data["app"]["version"]
    assert data["environment"]["LAC_PRO_GATE_URL"] == {"set": True}
    assert data["environment"]["HF_TOKEN"] == {"set": True}
    assert data["environment"]["OLLAMA_HOST"]["value"] == "http://localhost:11434"
    assert "secret-gate" not in raw
    assert "hf_secret" not in raw
    assert "ollama-secret" not in raw
    assert data["ollama"]["installed_models"][0]["name"] == "tiny:latest"


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


def test_sessions_list_limit(flask_app, isolated_home):
    client = flask_app.test_client()
    for i in range(3):
        r = client.post("/api/sessions", json={"name": f"s{i}", "model": "m"})
        assert r.status_code in (200, 201)

    limited = client.get("/api/sessions?limit=2")

    assert limited.status_code == 200
    assert len(limited.get_json()) == 2


def test_sessions_list_limit_must_be_integer(flask_app, isolated_home):
    response = flask_app.test_client().get("/api/sessions?limit=nope")

    assert response.status_code == 400
    assert response.get_json()["error"] == "limit must be an integer"


def test_ollama_status(flask_app, ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    client = flask_app.test_client()
    r = client.get("/api/ollama/status")
    assert r.status_code == 200


def test_ollama_model_profiles_return_only_exact_local_evidence(monkeypatch, flask_app):
    from backend import api as api_mod

    digest = "sha256:" + ("a" * 64)
    calls = []

    def fake_ollama_request(method, path, json_body=None, stream=False, timeout=30):
        calls.append((method, path, json_body, timeout))
        if path == "/api/tags":
            return {
                "models": [
                    {
                        "name": "qwen2.5:7b",
                        "size": 4_500_000_000,
                        "modified_at": "2026-07-15T12:00:00Z",
                        "digest": digest,
                        "details": {
                            "format": "gguf",
                            "family": "qwen2",
                            "families": ["qwen2"],
                            "parameter_size": "7.6B",
                            "quantization_level": "Q4_K_M",
                            "not_allowlisted": "must stay private",
                        },
                    },
                    {
                        "name": "custom:latest",
                        "size": 123,
                        "modified_at": "",
                        "digest": "custom-digest",
                    },
                ]
            }
        assert path == "/api/show"
        assert json_body in ({"model": "custom:latest"}, {"model": "qwen2.5:7b"})
        if json_body["model"] == "qwen2.5:7b":
            return {
                "model_info": {
                    "qwen2.context_length": 32768,
                    "qwen2.embedding_length": 3584,
                    "context_length": 999999,
                },
                "template": "never return this",
                "system": "never return this either",
                "modelfile": "FROM secret/path",
                "arbitrary": {"private": True},
            }
        return {"model_info": {"custom.context_length": "unknown"}}

    monkeypatch.setattr(api_mod, "_ollama_request", fake_ollama_request)

    response = flask_app.test_client().post(
        "/api/ollama/model-profiles",
        json={"models": ["custom:latest", "qwen2.5:7b"]},
    )

    assert response.status_code == 200
    profiles = response.get_json()["profiles"]
    assert [profile["name"] for profile in profiles] == ["custom:latest", "qwen2.5:7b"]
    assert set(profiles[0]) == {
        "name",
        "size_gb",
        "modified",
        "digest",
        "digest_short",
        "format",
        "family",
        "families",
        "parameter_size",
        "quantization_level",
        "context_length",
    }
    assert profiles[0] == {
        "name": "custom:latest",
        "size_gb": 0.0,
        "modified": "",
        "digest": "custom-digest",
        "digest_short": "custom-diges",
        "format": None,
        "family": None,
        "families": None,
        "parameter_size": None,
        "quantization_level": None,
        "context_length": None,
    }
    assert profiles[1]["digest"] == digest
    assert profiles[1]["digest_short"] == digest[:12]
    assert profiles[1]["format"] == "gguf"
    assert profiles[1]["family"] == "qwen2"
    assert profiles[1]["families"] == ["qwen2"]
    assert profiles[1]["parameter_size"] == "7.6B"
    assert profiles[1]["quantization_level"] == "Q4_K_M"
    assert profiles[1]["context_length"] == 32768
    assert [call[1] for call in calls] == ["/api/tags", "/api/show", "/api/show"]
    assert all(call[3] <= 10 for call in calls[1:])


def test_ollama_model_profiles_fail_closed_before_show(monkeypatch, flask_app):
    from backend import api as api_mod

    calls = []

    def fake_ollama_request(method, path, json_body=None, stream=False, timeout=30):
        calls.append((path, json_body))
        return {"models": [{"name": "installed:1b", "size": 1, "digest": "exact"}]}

    monkeypatch.setattr(api_mod, "_ollama_request", fake_ollama_request)
    client = flask_app.test_client()

    too_many = client.post(
        "/api/ollama/model-profiles",
        json={"models": ["installed:1b", "other:1b", "third:1b"]},
    )
    assert too_many.status_code == 400
    assert calls == []

    duplicate = client.post(
        "/api/ollama/model-profiles",
        json={"models": ["installed:1b", "installed:1b"]},
    )
    assert duplicate.status_code == 400
    assert calls == []

    unknown = client.post(
        "/api/ollama/model-profiles",
        json={"models": ["Installed:1b"]},
    )
    assert unknown.status_code == 404
    assert unknown.get_json() == {
        "error": "model is not installed",
        "models": ["Installed:1b"],
    }
    assert calls == [("/api/tags", None)]


def test_ollama_context_length_extraction_is_suffix_only_and_fail_closed():
    from backend import api as api_mod

    assert api_mod._extract_ollama_context_length({
        "architecture.context_length": 8192,
        "context_length": 131072,
        "nested": {"architecture.context_length": 262144},
        "other.context_length": True,
    }) == 8192
    assert api_mod._extract_ollama_context_length({
        "a.context_length": 8192,
        "b.context_length": 16384,
    }) is None
    assert api_mod._extract_ollama_context_length({
        "a.context_length": -1,
    }) is None


def test_ollama_inventory_and_residency_routes_fail_closed_on_upstream_error(monkeypatch, flask_app):
    from backend import api as api_mod

    monkeypatch.setattr(api_mod, "_ollama_request", lambda *args, **kwargs: {"error": "offline"})
    client = flask_app.test_client()

    inventory = client.get("/api/ollama/models")
    residency = client.get("/api/ollama/ps")

    assert inventory.status_code == 502
    assert inventory.get_json() == {"error": "Ollama model inventory unavailable"}
    assert residency.status_code == 502
    assert residency.get_json() == {"error": "Ollama residency unavailable"}


def test_ollama_context_length_extraction_rejects_oversized_model_info():
    from backend import api as api_mod

    scan_limit = api_mod._OLLAMA_MODEL_INFO_SCAN_LIMIT
    oversized = {"a.context_length": 8192}
    oversized.update({f"filler_{index}": index for index in range(scan_limit - 1)})
    oversized["b.context_length"] = 16384

    assert len(oversized) == scan_limit + 1
    assert api_mod._extract_ollama_context_length(oversized) is None

    oversized_without_conflict = {"a.context_length": 8192}
    oversized_without_conflict.update({
        f"filler_{index}": index for index in range(scan_limit)
    })
    assert len(oversized_without_conflict) == scan_limit + 1
    assert api_mod._extract_ollama_context_length(oversized_without_conflict) is None


def test_openapi_endpoint(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    spec = r.get_json()
    assert spec["openapi"] == "3.1.0"
    assert "/api/system/version" in spec["paths"]


def test_recommend_serializes_speed_source(
    flask_app, isolated_home, monkeypatch
):
    """Each recommendation must carry speed_source + speed_band_pct so the
    web UI can tag measured/calibrated/estimated values."""
    import backend.api as api_mod

    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3")
    assert r.status_code == 200
    data = r.get_json()
    assert "recommendations" in data and len(data["recommendations"]) > 0
    for rec in data["recommendations"]:
        assert rec["speed_source"] in ("measured", "calibrated", "estimated")
        assert isinstance(rec["speed_band_pct"], (int, float))
        assert rec["speed_band_pct"] > 0  # never a zero-width band


def test_recommend_no_calibration_escape_hatch(
    flask_app, isolated_home, monkeypatch
):
    """?no_calibration=1 must still return recs, all tagged 'estimated'."""
    import backend.api as api_mod

    monkeypatch.setattr(api_mod, "detect", _fake_detect_factory())
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3&no_calibration=1")
    assert r.status_code == 200
    recommendations = r.get_json()["recommendations"]
    assert recommendations
    for rec in recommendations:
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
        lambda method, path, json_body=None, stream=False, timeout=30: {"error": "model 'x' not found"},
    )
    r = flask_app.test_client().post("/api/ollama/delete", json={"model": "x"})
    assert r.status_code == 500
    assert r.get_json().get("success") is not True


def test_ollama_delete_treats_empty_ollama_success_as_success(monkeypatch, flask_app):
    from backend import api as api_mod

    captured = {}

    def fake_request(method, path, json_body=None, stream=False, timeout=30):
        captured.update({
            "method": method,
            "path": path,
            "json_body": json_body,
            "timeout": timeout,
        })
        return {}

    monkeypatch.setattr(api_mod, "_ollama_request", fake_request)
    r = flask_app.test_client().post("/api/ollama/delete", json={"model": "lac-delete-smoke:latest"})

    assert r.status_code == 200
    assert r.get_json() == {"success": True}
    assert captured == {
        "method": "DELETE",
        "path": "/api/delete",
        "json_body": {"name": "lac-delete-smoke:latest"},
        "timeout": 120,
    }


def test_ollama_request_accepts_empty_success_body(monkeypatch):
    from backend import api as api_mod
    import urllib.request as real_urllib_request

    class FakeResp:
        def read(self):
            return b""

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=30: FakeResp())

    assert api_mod._ollama_request("DELETE", "/api/delete", {"name": "x"}) == {}


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
            return (
                b'{"tag_name": "v9.9.9", "html_url": "x", "body": "", '
                b'"assets": [{"name": "LAC-Setup-9.9.9-windows-x64.exe", '
                b'"browser_download_url": "https://github.test/LAC-Setup.exe"}]}'
            )

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
    assert r.get_json()["download_url"] == "https://github.test/LAC-Setup.exe"


def test_check_update_does_not_offer_downgrade_for_local_patch(monkeypatch, flask_app):
    import urllib.request as real_urllib_request

    class FakeResp:
        def read(self):
            return (
                b'{"tag_name": "v2.6.3", "html_url": "x", "body": "", '
                b'"assets": [{"name": "LAC-Setup-2.6.3.exe", '
                b'"browser_download_url": "https://github.test/LAC-Setup-2.6.3.exe"}]}'
            )

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=5: FakeResp())

    r = flask_app.test_client().get("/api/system/check-update?current=2.6.4")

    assert r.status_code == 200
    assert r.get_json() == {
        "update_available": False,
        "latest_version": "2.6.3",
        "current_version": "2.6.4",
    }


def test_check_update_semver_handles_multi_digit_versions(monkeypatch, flask_app):
    import urllib.request as real_urllib_request

    class FakeResp:
        def read(self):
            return b'{"tag_name": "v2.10.0", "html_url": "x", "body": "", "assets": []}'

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=5: FakeResp())

    r = flask_app.test_client().get("/api/system/check-update?current=2.9.9")

    assert r.status_code == 200
    assert r.get_json()["update_available"] is True
    assert r.get_json()["latest_version"] == "2.10.0"


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
    assert data["models"][0]["files"][0]["preflight"]["selected_size_bytes"] == 4_000_000_000
    assert data["models"][0]["preflight"]["selected_size_bytes"] == 4_000_000_000
    assert data["system_vram"] == 6.0
    assert any("qwen+gguf" in url for url in captured["urls"])
    assert any("/api/models/org/model-GGUF" in url for url in captured["urls"])
    assert captured["ua"].startswith("LAC/")


def test_install_preflight_detects_hf_short_url_and_quant(monkeypatch, flask_app, tmp_path):
    from backend import api as api_mod
    from backend.cookbook.hardware import SystemInfo

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setattr(api_mod, "_default_ollama_models_dir", lambda: models_dir)
    monkeypatch.setattr(api_mod, "_hf_import_scratch_root", lambda: tmp_path / "lac-hf-import-tmp")
    monkeypatch.setattr(api_mod, "_disk_free_bytes", lambda path: 20 * 1024**3)
    monkeypatch.setattr(api_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[], total_vram_gb=6.0, combined_vram_gb=6.0, compute_tiers=[],
    ))
    monkeypatch.setattr(api_mod, "_fetch_hf_model_detail", lambda repo_id: {
        "id": repo_id,
        "author": "org",
        "downloads": 5,
        "likes": 1,
        "gated": False,
        "tags": ["gguf"],
        "siblings": [
            {"rfilename": "model-Q4_K_M.gguf", "size": 400_000_000},
            {"rfilename": "model-Q8_0.gguf", "size": 800_000_000},
        ],
    })

    r = flask_app.test_client().get("/api/model/install-preflight?target=hf.co/org/model-GGUF:Q8_0")

    assert r.status_code == 200
    data = r.get_json()
    assert data["kind"] == "hf_gguf"
    assert data["action"] == "import"
    assert data["state"] == "ok"
    assert data["repo_id"] == "org/model-GGUF"
    assert data["selected_quant"] == "Q8_0"
    assert data["selected_file"] == "model-Q8_0.gguf"
    assert data["preflight"]["model_store_dir"] == str(models_dir)
    assert data["preflight"]["selected_size_bytes"] == 800_000_000


def test_install_preflight_treats_bare_repo_as_hf(monkeypatch, flask_app):
    from backend import api as api_mod

    monkeypatch.setattr(api_mod, "_fetch_hf_model_detail", lambda repo_id: {
        "id": repo_id,
        "gated": False,
        "tags": ["safetensors"],
        "siblings": [{"rfilename": "model.safetensors", "size": 100}],
    })

    r = flask_app.test_client().get("/api/model/install-preflight?target=org/model")

    assert r.status_code == 200
    data = r.get_json()
    assert data["kind"] == "hf_unknown"
    assert data["action"] == "import"
    assert data["repo_id"] == "org/model"
    assert "safetensors conversion" in data["message"]


def test_install_preflight_keeps_ollama_tags_as_pull(monkeypatch, flask_app, tmp_path):
    from backend import api as api_mod

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setattr(api_mod, "_default_ollama_models_dir", lambda: models_dir)
    monkeypatch.setattr(api_mod, "_disk_free_bytes", lambda path: 123_456)

    r = flask_app.test_client().get("/api/model/install-preflight?target=llama3.2:3b")

    assert r.status_code == 200
    data = r.get_json()
    assert data["kind"] == "ollama"
    assert data["action"] == "pull"
    assert data["model_ref"] == "llama3.2:3b"
    assert data["model_store_dir"] == str(models_dir)
    assert data["model_store"]["free_bytes"] == 123_456


def test_hf_import_preflight_follows_ollama_models(monkeypatch, tmp_path):
    from backend import api as api_mod

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    monkeypatch.delenv("LAC_HF_IMPORT_TMP", raising=False)
    monkeypatch.delenv("LAC_IMPORT_TMP", raising=False)
    monkeypatch.setattr(api_mod, "_disk_free_bytes", lambda path: 1_000)
    monkeypatch.setattr(api_mod, "_storage_volume_identity", lambda path: "shared", raising=False)

    preflight = api_mod._hf_import_preflight(100)

    assert preflight["state"] == "ok"
    assert preflight["scratch_dir"] == str(models_dir.parent / "lac-hf-import-tmp")
    assert preflight["model_store_dir"] == str(models_dir)
    assert preflight["scratch"]["required_bytes"] == 100
    assert preflight["model_store"]["required_bytes"] == 400
    assert preflight["shared_volume"] is True
    assert preflight["combined"]["required_bytes"] == 500


@pytest.mark.parametrize("free_bytes,state", [(499, "blocked"), (500, "ok")])
def test_hf_import_preflight_aggregates_shared_volume_budget(monkeypatch, tmp_path, free_bytes, state):
    from backend import api as api_mod

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    monkeypatch.delenv("LAC_HF_IMPORT_TMP", raising=False)
    monkeypatch.delenv("LAC_IMPORT_TMP", raising=False)
    monkeypatch.setattr(api_mod, "_disk_free_bytes", lambda path: free_bytes)
    monkeypatch.setattr(api_mod, "_storage_volume_identity", lambda path: "shared", raising=False)

    preflight = api_mod._hf_import_preflight(100)

    assert preflight["state"] == state
    assert preflight["shared_volume"] is True
    assert preflight["combined"]["required_bytes"] == 500
    assert preflight["combined"]["ok"] is (state == "ok")


def test_hf_import_preflight_blocks_when_volume_identity_is_unavailable(monkeypatch, tmp_path):
    from backend import api as api_mod

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    monkeypatch.setattr(api_mod, "_disk_free_bytes", lambda path: 10_000)
    monkeypatch.setattr(
        api_mod,
        "_storage_volume_identity",
        lambda path: (_ for _ in ()).throw(OSError("volume unavailable")),
    )

    preflight = api_mod._hf_import_preflight(100)

    assert preflight["state"] == "blocked"
    assert preflight["shared_volume"] is None
    assert preflight["combined"] is None
    assert "Could not verify" in preflight["warnings"][0]


def test_hf_import_preflight_keeps_separate_volume_budgets_independent(monkeypatch, tmp_path):
    from backend import api as api_mod

    scratch_dir = tmp_path / "scratch"
    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setenv("LAC_HF_IMPORT_TMP", str(scratch_dir))
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    monkeypatch.setattr(
        api_mod,
        "_storage_volume_identity",
        lambda path: "scratch" if path == scratch_dir else "ollama",
    )
    monkeypatch.setattr(
        api_mod,
        "_disk_free_bytes",
        lambda path: 100 if path == scratch_dir else 400,
    )

    preflight = api_mod._hf_import_preflight(100)

    assert preflight["state"] == "ok"
    assert preflight["shared_volume"] is False
    assert preflight["combined"] is None
    assert preflight["scratch"]["required_bytes"] == 100
    assert preflight["model_store"]["required_bytes"] == 400


def test_hf_import_preflight_blocks_when_model_store_is_short(monkeypatch, tmp_path):
    from backend import api as api_mod

    models_dir = tmp_path / "ollama" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))

    def fake_free(path):
        return 500 if path.name == "lac-hf-import-tmp" else 50

    monkeypatch.setattr(api_mod, "_disk_free_bytes", fake_free)
    monkeypatch.setattr(
        api_mod,
        "_storage_volume_identity",
        lambda path: "scratch" if path.name == "lac-hf-import-tmp" else "ollama",
        raising=False,
    )

    preflight = api_mod._hf_import_preflight(100)

    assert preflight["state"] == "blocked"
    assert preflight["shared_volume"] is False
    assert preflight["scratch"]["ok"] is True
    assert preflight["model_store"]["ok"] is False
    assert "Ollama model store" in preflight["warnings"][0]


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


def test_hf_gguf_files_block_cpu_tuned_variants():
    from backend import api as api_mod

    files = api_mod._hf_gguf_files(
        [
            {"rfilename": "model-Q4_0.gguf", "size": 800_000_000},
            {"rfilename": "model-Q4_0_4_4.gguf", "size": 700_000_000},
            {"rfilename": "model-Q4_0_8_8.gguf", "size": 600_000_000},
        ],
        system_vram=16.0,
        ram_gb=32.0,
    )
    by_name = {file["filename"]: file for file in files}

    assert by_name["model-Q4_0.gguf"]["importable"] is True
    assert by_name["model-Q4_0_4_4.gguf"]["quant"] == "Q4_0"
    assert by_name["model-Q4_0_4_4.gguf"]["importable"] is False
    assert "CPU-tuned" in by_name["model-Q4_0_8_8.gguf"]["compatibility_note"]
    assert api_mod._choose_hf_file(files)["filename"] == "model-Q4_0.gguf"


def test_hf_gguf_search_empty_query_is_local_only(flask_app):
    r = flask_app.test_client().get("/api/hf/gguf-search")
    assert r.status_code == 200
    assert r.get_json() == {"query": "", "total": 0, "models": []}


def test_performance_diagnosis_detects_fast_generation_slow_start():
    from backend import api as api_mod

    diagnosis = api_mod._diagnose_performance({
        "tokens_per_second": 374.0,
        "time_to_first_token_ms": 2400.0,
        "load_duration_ms": 0.0,
        "prompt_eval_duration_ms": 2200.0,
    })

    assert diagnosis["state"] == "watch"
    assert "before generation" in diagnosis["summary"]
    assert all("first token" not in signal["label"].lower() for signal in diagnosis["signals"])
    assert any(signal["kind"] == "fast_after_start" for signal in diagnosis["signals"])
    assert any(action["kind"] == "warm" for action in diagnosis["actions"])


def test_performance_diagnosis_keeps_unusable_counters_unmeasured():
    from backend import api as api_mod

    unusable_records = [
        {"model": "tiny:latest", "source": "diagnostic_probe"},
        {
            "tokens_per_second": 0,
            "time_to_first_token_ms": 0,
            "load_duration_ms": 0,
            "prompt_eval_duration_ms": 0,
            "total_duration_ms": 0,
            "eval_duration_ms": 0,
            "eval_count": 0,
        },
    ]

    for metrics in unusable_records:
        diagnosis = api_mod._diagnose_performance(metrics)
        assert diagnosis["state"] == "unmeasured"
        assert diagnosis["summary"] == "No usable Ollama measurement was reported."
        assert all(signal.get("kind") != "healthy" for signal in diagnosis["signals"])


def test_performance_diagnostics_reports_inventory_and_residency_reliability(monkeypatch, flask_app):
    from backend import api as api_mod

    def fake_ollama_request(method, path, *args, **kwargs):
        if path == "/api/tags":
            return {"models": [{"name": "tiny:latest"}]}
        if path == "/api/ps":
            return {"error": "offline"}
        raise AssertionError(path)

    monkeypatch.setattr(api_mod, "_ollama_request", fake_ollama_request)
    monkeypatch.setattr(api_mod, "_benchmark_history_for_model", lambda model: [])

    response = flask_app.test_client().get("/api/diagnostics/performance?model=tiny%3Alatest")

    assert response.status_code == 200
    data = response.get_json()
    assert data["installed_models"] == ["tiny:latest"]
    assert data["installed_models_reported"] is True
    assert data["running_models"] == []
    assert data["running_models_reported"] is False


def test_performance_probe_returns_metrics_and_diagnosis(monkeypatch, flask_app):
    from backend import api as api_mod

    captured = {}

    def fake_ollama_request(method, path, json_body=None, stream=False, timeout=30):
        captured.update({"method": method, "path": path, "json": json_body, "timeout": timeout})
        return {
            "eval_count": 16,
            "eval_duration": 100_000_000,
            "load_duration": 2_000_000_000,
            "prompt_eval_duration": 100_000_000,
            "total_duration": 2_200_000_000,
            "response": "ready",
        }

    monkeypatch.setattr(api_mod, "_ollama_request", fake_ollama_request)
    monkeypatch.setattr(api_mod, "_interactive_context", lambda: 6144)

    r = flask_app.test_client().post("/api/diagnostics/performance/probe", json={"model": "tiny:latest"})

    assert r.status_code == 200
    data = r.get_json()
    assert data["state"] == "done"
    assert data["metrics"]["source"] == "diagnostic_probe"
    assert data["metrics"]["protocol_id"] == "lac.quick-latency.v1"
    assert data["metrics"]["num_ctx"] == 6144
    assert data["metrics"]["tokens_per_second"] == 160.0
    assert data["metrics"]["load_duration_ms"] == 2000.0
    assert "prompt" not in data["metrics"]
    assert "response" not in data["metrics"]
    assert data["diagnosis"]["state"] in {"ok", "watch"}
    assert captured["path"] == "/api/generate"
    assert captured["json"]["keep_alive"] == "30m"
    assert captured["json"]["options"]["num_predict"] == 32
    assert captured["json"]["options"]["num_ctx"] == 6144


def test_performance_history_is_bounded_allowlisted_and_redacted(monkeypatch):
    from backend import api as api_mod
    from backend.cookbook import benchmark

    monkeypatch.setattr(benchmark, "history", lambda: [{
        "model": "tiny:latest",
        "timestamp": 42,
        "source": "pro_benchmark",
        "tokens_per_second": 12.5,
        "time_to_first_token_ms": 120,
        "prompt": "private benchmark prompt",
        "response": "private model output",
        "arbitrary": {"must": "not pass through"},
    }])

    assert api_mod._benchmark_history_for_model("tiny:latest") == [{
        "model": "tiny:latest",
        "timestamp": 42,
        "source": "pro_benchmark",
        "tokens_per_second": 12.5,
        "time_to_first_token_ms": 120,
    }]


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
