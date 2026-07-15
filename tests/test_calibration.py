# tests/test_calibration.py
from __future__ import annotations
from backend.cookbook.calibration import parse_model_tag


def test_parse_bare_tag_defaults_to_q4km():
    assert parse_model_tag("qwen3:30b-a3b") == ("qwen3:30b-a3b", "Q4_K_M")

def test_parse_quant_suffix():
    assert parse_model_tag("qwen3:30b-a3b-q8_0") == ("qwen3:30b-a3b", "Q8")
    assert parse_model_tag("qwen3:30b-a3b-q4_K_M") == ("qwen3:30b-a3b", "Q4_K_M")
    assert parse_model_tag("qwen3:30b-a3b-fp16") == ("qwen3:30b-a3b", "F16")

def test_parse_hf_sub4bit():
    assert parse_model_tag("hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit") == (
        "hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit", "1.58bit")

def test_parse_unknown_returns_none():
    assert parse_model_tag("totally-made-up:99b") is None


from backend.cookbook.calibration import machine_fingerprint
from backend.cookbook.hardware import SystemInfo, GPUInfo


def _info():
    return SystemInfo(os="Windows", cpu="Ryzen 5 7600", cpu_cores=6, ram_gb=30.9,
                      gpus=[GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm")],
                      total_vram_gb=16.0)

def test_fingerprint_stable():
    s = {"ollama_version": "0.31.1", "backend": "vulkan"}
    assert machine_fingerprint(_info(), s) == machine_fingerprint(_info(), s)

def test_fingerprint_changes_with_ollama_version():
    a = machine_fingerprint(_info(), {"ollama_version": "0.31.1", "backend": "vulkan"})
    b = machine_fingerprint(_info(), {"ollama_version": "0.32.0", "backend": "vulkan"})
    assert a != b

def test_fingerprint_changes_with_gpu():
    info2 = _info(); info2.gpus = [GPUInfo("NVIDIA RTX 4090", 24.0, backend="cuda")]
    s = {"ollama_version": "0.31.1", "backend": "vulkan"}
    assert machine_fingerprint(_info(), s) != machine_fingerprint(info2, s)


import json
from backend.cookbook.calibration import load_calibration, detect_stack

_STACK = {"ollama_version": "0.31.1", "backend": "vulkan"}

def _write_results(path, info, rows):
    from backend.cookbook.calibration import machine_fingerprint
    fp = machine_fingerprint(info, _STACK)
    with open(path, "w") as f:
        for tag, tps, extra in rows:
            e = {"model": tag, "tokens_per_second": tps, "eval_count": 128}
            if extra != "no-fp":
                e["fingerprint"] = fp if extra == "match" else "deadbeef0000"
            f.write(json.dumps(e) + "\n")

def test_load_calibration_measured_override(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [("falcon3:3b", 178.0, "match")])
    cal = load_calibration(info, _STACK, str(p))
    assert cal.measured[("falcon3:3b", "Q4_K_M")].median_tps == 178.0


def test_legacy_row_without_fingerprint_is_not_classified_as_measured(tmp_path):
    """Pre-fingerprint history remains useful, but its machine provenance is
    unknowable and therefore cannot truthfully be presented as an exact run."""
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [("falcon3:3b", 178.0, "no-fp")])

    cal = load_calibration(info, _STACK, str(p))
    tps, src, band = apply_calibration(
        200.0, "falcon3:3b", "Q4_K_M", "gpu", cal,
    )

    assert tps == 178.0
    assert src == "estimated"
    assert band >= 50.0


def test_matching_fingerprint_samples_do_not_mix_with_legacy_rows(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [
        ("falcon3:3b", 999.0, "no-fp"),
        ("falcon3:3b", 178.0, "match"),
    ])

    cal = load_calibration(info, _STACK, str(p))
    tps, src, _ = apply_calibration(
        200.0, "falcon3:3b", "Q4_K_M", "gpu", cal,
    )

    assert (tps, src) == (178.0, "measured")

def test_load_calibration_regime_factor(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    # falcon = gpu regime; if theoretical ~186 and real 178, factor ~0.96
    _write_results(p, info, [("falcon3:3b", 178.0, "match")])
    cal = load_calibration(info, _STACK, str(p))
    assert 0.7 <= cal.regime_factor["gpu"] <= 1.2

def test_load_calibration_ignores_foreign_fingerprint(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [("falcon3:3b", 999.0, "foreign")])
    cal = load_calibration(info, _STACK, str(p))
    assert ("falcon3:3b", "Q4_K_M") not in cal.measured
    assert cal.regime_factor.get("gpu", 1.0) == 1.0  # no matching data -> default

def test_load_calibration_empty(tmp_path):
    cal = load_calibration(_info(), _STACK, str(tmp_path / "none.jsonl"))
    assert cal.n == 0 and cal.regime_factor == {}


from backend.cookbook.calibration import apply_calibration, Calibration, MeasuredStat

def test_apply_measured_wins():
    cal = Calibration(measured={("m","Q4_K_M"): MeasuredStat(50.0, 3, 4.0)},
                      regime_factor={"gpu": 0.5}, regime_band_pct={"gpu": 10.0}, n=3)
    tps, src, band = apply_calibration(200.0, "m", "Q4_K_M", "gpu", cal)
    assert (tps, src) == (50.0, "measured")

def test_apply_calibrated_when_regime_has_factor():
    cal = Calibration(regime_factor={"spilled": 0.25}, regime_band_pct={"spilled": 20.0}, n=2)
    tps, src, band = apply_calibration(100.0, "m", "Q8", "spilled", cal)
    assert (round(tps,1), src, band) == (25.0, "calibrated", 20.0)

def test_apply_estimated_when_no_data():
    tps, src, band = apply_calibration(100.0, "m", "Q8", "spilled", Calibration())
    assert (tps, src) == (100.0, "estimated") and band >= 50.0

def test_apply_none_calibration_is_estimated():
    tps, src, band = apply_calibration(100.0, "m", "Q8", "spilled", None)
    assert src == "estimated"


def test_load_calibration_multi_run_median_and_spread(tmp_path):
    # 4 runs (even N) also guards the statistics.median fix: the old
    # index-based upper-median returned 185 here, statistics.median -> 180.
    import statistics
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [
        ("falcon3:3b", 170.0, "match"),
        ("falcon3:3b", 175.0, "match"),
        ("falcon3:3b", 185.0, "match"),
        ("falcon3:3b", 190.0, "match"),
    ])
    cal = load_calibration(info, _STACK, str(p))
    stat = cal.measured[("falcon3:3b", "Q4_K_M")]
    assert stat.n_runs == 4
    assert stat.median_tps == statistics.median([170.0, 175.0, 185.0, 190.0])
    assert stat.spread_pct > 0

def test_load_calibration_loo_band_real_branch(tmp_path):
    # >=3 matching-fp entries in one regime -> the real LOO band is computed,
    # not the conservative 35.0 default used for <3 samples.
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [
        ("falcon3:3b", 170.0, "match"),
        ("falcon3:3b", 180.0, "match"),
        ("falcon3:3b", 190.0, "match"),
    ])
    cal = load_calibration(info, _STACK, str(p))
    band = cal.regime_band_pct["gpu"]
    assert band != 35.0 and 0 < band < 30

def test_fingerprint_multi_gpu_does_not_raise():
    # Regression: sorted() over a generator of dicts raised TypeError when
    # there was more than one GPU. Must classify without raising and stay
    # stable across calls.
    info = SystemInfo(os="Windows", cpu="x", cpu_cores=6, ram_gb=30.9,
                      gpus=[GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm"),
                            GPUInfo("Intel Arc A380", 2.0, backend="cuda")],
                      total_vram_gb=18.0)
    s = {"ollama_version": "0.31.1", "backend": "vulkan"}
    fp = machine_fingerprint(info, s)
    assert machine_fingerprint(info, s) == fp

def test_infer_backend_env_overrides(monkeypatch):
    from backend.cookbook.calibration import _infer_backend
    monkeypatch.setenv("OLLAMA_LLM_LIBRARY", "vulkan")
    assert _infer_backend(_info()) == "vulkan"

def test_infer_backend_amd_stays_unknown(monkeypatch):
    # rocm-vs-vulkan on AMD is genuinely ambiguous -> must not guess.
    from backend.cookbook.calibration import _infer_backend
    monkeypatch.delenv("OLLAMA_LLM_LIBRARY", raising=False)
    assert _infer_backend(_info()) == "unknown"

def test_infer_backend_nvidia_cuda(monkeypatch):
    from backend.cookbook.calibration import _infer_backend
    monkeypatch.delenv("OLLAMA_LLM_LIBRARY", raising=False)
    info = SystemInfo(os="Windows", cpu="x", cpu_cores=8, ram_gb=32.0,
                      gpus=[GPUInfo("NVIDIA RTX 4090", 24.0, backend="cuda")],
                      total_vram_gb=24.0, has_nvidia=True)
    assert _infer_backend(info) == "cuda"


def test_apply_calibration_falls_back_to_any_measured_quant_for_same_model():
    """recommend() scores every quant per model but returns only the
    best-SCORING one -- for a small model, F16 can win on composite score
    even though a plain `ollama pull` actually downloads (and gets
    benchmarked at) Q4_K_M. An exact (id, quant) lookup then never sees the
    Q4_K_M measured entry when scoring the F16 candidate, silently falling
    back to 'calibrated'/'estimated' even though real measured data exists
    for this model. Loosen the match: fall back to ANY measured quant for
    the same model id."""
    cal = Calibration(
        measured={("qwen3:0.6b", "Q4_K_M"): MeasuredStat(417.0, 1, 25.0)},
        regime_factor={}, regime_band_pct={}, n=1,
    )
    tps, src, band = apply_calibration(999.0, "qwen3:0.6b", "F16", "gpu", cal)
    assert (tps, src) == (417.0, "calibrated")
    assert band >= 50.0


def test_apply_calibration_exact_match_still_wins_over_fallback():
    cal = Calibration(
        measured={
            ("m", "Q4_K_M"): MeasuredStat(50.0, 3, 4.0),
            ("m", "F16"): MeasuredStat(20.0, 1, 25.0),
        },
        regime_factor={"gpu": 0.5}, regime_band_pct={"gpu": 10.0}, n=4,
    )
    tps, src, band = apply_calibration(200.0, "m", "Q4_K_M", "gpu", cal)
    assert (tps, src) == (50.0, "measured")


def test_apply_calibration_falls_back_when_multiple_other_quants_measured():
    """When several other quants for the same model have measured data,
    fall back deterministically (most runs first) rather than raising or
    picking arbitrarily."""
    cal = Calibration(
        measured={
            ("m", "Q8"): MeasuredStat(80.0, 1, 25.0),
            ("m", "Q5_K_M"): MeasuredStat(90.0, 5, 6.0),
        },
        regime_factor={}, regime_band_pct={}, n=6,
    )
    tps, src, band = apply_calibration(999.0, "m", "F16", "gpu", cal)
    assert src == "calibrated"
    assert tps == 90.0  # the higher-n_runs entry wins the tie-break


def test_cross_quant_prefers_current_machine_measurement_over_larger_legacy_sample():
    cal = Calibration(
        measured={
            ("m", "Q8"): MeasuredStat(
                80.0, 10, 6.0, provenance="estimated",
            ),
            ("m", "Q4_K_M"): MeasuredStat(
                50.0, 1, 25.0, provenance="measured",
            ),
        },
        regime_factor={}, regime_band_pct={}, n=11,
    )

    tps, src, band = apply_calibration(999.0, "m", "F16", "gpu", cal)

    assert (tps, src) == (50.0, "calibrated")
    assert band >= 50.0
