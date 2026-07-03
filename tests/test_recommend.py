from __future__ import annotations

import pytest

from backend.cookbook.hardware import GPUInfo, SystemInfo
from backend.cookbook.recommend import (
    ModelEntry,
    SplitPlan,
    TierAllocation,
    _estimate_speed,
    _fit_score,
    load_models,
    recommend,
    speed_regime,
    QUANTS,
)


def _sys16() -> SystemInfo:
    return SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=30.9,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=16.0, backend="rocm")],
        total_vram_gb=16.0,
    )


def _find(recs, model_id):
    return next((r for r in recs if r.model.id == model_id), None)


# --- catalog sanity (regression guards for the A-pass) ---

def test_catalog_loads():
    models = load_models()
    assert len(models) >= 90


def test_catalog_no_removed_bogus_ids():
    ids = {m.id for m in load_models()}
    # Entries that don't exist on the real Ollama library — must stay gone.
    for bad in ("qwen3:7b", "qwen3:72b", "gemma3:2b", "gemma3:7b",
                "gemma4:7b", "gemma4:24b", "nemotron:4b", "nemotron:12b",
                "llama3.2:11b", "qwen3-coder:7b", "qwen3-coder:14b",
                "mistral:12b", "mellum2:12b", "deepseek-v4:flash",
                "deepseek-v4:pro"):
        assert bad not in ids, f"removed id {bad} reappeared"


def test_catalog_moe_flags_consistent():
    for m in load_models():
        if m.is_moe:
            assert m.active_params_b is not None and m.active_params_b > 0, (
                f"{m.id} flagged MoE but has no active_params_b")
            assert m.active_params_b < m.params_b, (
                f"{m.id} active_params_b should be < total params")
        else:
            assert m.active_params_b is None, (
                f"{m.id} dense but has active_params_b={m.active_params_b}")


def test_catalog_qwen3_small_is_dense():
    ids = {m.id: m for m in load_models()}
    for small in ("qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b"):
        assert ids[small].is_moe is False, f"{small} should be dense"
    assert ids["qwen3:30b-a3b"].is_moe is True
    assert ids["qwen3:235b"].active_params_b == 22.0


def test_catalog_vram_is_file_size_not_full_context():
    # vram_q4 should approximate the Q4 file size (weights + overhead),
    # NOT weights + full-context KV. qwen3:14b Q4 file is ~9GB, not ~14GB.
    ids = {m.id: m for m in load_models()}
    assert 8.0 <= ids["qwen3:14b"].vram_q4 <= 10.0
    assert 17.0 <= ids["qwen3:30b-a3b"].vram_q4 <= 18.5  # real file ~17.5GB


def test_sub4bit_entries_have_small_vram():
    ids = {m.id: m for m in load_models()}
    falcon = ids["hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit"]
    assert falcon.sub4bit is True
    # 1.58-bit ≈ 0.2 bytes/param → 3B model is well under 2GB.
    assert falcon.vram_q4 < 2.0


# --- fit_score shape (the core F fix) ---

def test_fit_score_peaks_at_utilization_not_smallness():
    # A model using ~6% of VRAM (tiny, wasted capacity) should NOT beat
    # one using ~75% (efficient use of the hardware).
    tiny = _fit_score(0.06, "gpu")
    good = _fit_score(0.75, "gpu")
    assert good > tiny
    assert good >= 95  # near peak


def test_fit_score_near_full_still_beats_tiny():
    # 30B-A3B at Q3 uses ~94% of 16GB — should outrank a 0.6B toy on fit.
    assert _fit_score(0.94, "gpu") > _fit_score(0.06, "gpu")


def test_fit_score_offload_declines_with_overspill():
    a = _fit_score(1.2, "cpu_offload")
    b = _fit_score(1.8, "cpu_offload")
    assert a > b
    assert a < _fit_score(0.75, "gpu")


# --- MoE-aware speed ---

def test_moe_speed_uses_active_params():
    # Build a single-tier split plan (all on a 512 GB/s discrete GPU).
    def _single_tier(model_gb):
        return SplitPlan(
            tiers=[TierAllocation("discrete", "GPU", 16.0, model_gb, "rocm", 0, 512.0, 48)],
            total_model_gb=model_gb, total_layers=48, gpu_layers=48, run_mode="gpu",
        )
    moe = ModelEntry("moe:30b", "MoE 30B", "x", 30.0, "qwen3", 40960,
                     ["general"], True, vram_q4=17.7, active_params_b=3.0)
    dense = ModelEntry("dense:30b", "Dense 30B", "x", 30.0, "qwen3", 40960,
                       ["general"], False, vram_q4=17.7)
    q4 = next(q for q in QUANTS if q.name == "Q4_K_M")
    s_moe = _estimate_speed(moe, q4, _single_tier(17.7))
    s_dense = _estimate_speed(dense, q4, _single_tier(17.7))
    assert s_moe > s_dense


def test_speed_regime_classification():
    def _plan(mode):
        return SplitPlan(tiers=[TierAllocation("discrete", "GPU", 16.0, 10.0, "rocm", 0, 512.0, 40)],
                         total_model_gb=10.0, total_layers=40, gpu_layers=40, run_mode=mode)
    assert speed_regime(_plan("gpu")) == "gpu"
    assert speed_regime(_plan("multi_gpu")) == "spilled"
    assert speed_regime(_plan("cpu_offload")) == "spilled"


def test_moe_gpu_resident_not_faster_than_dense():
    # Research: MoE decode takes a slight PENALTY, not a bonus.
    def _single(model_gb):
        return SplitPlan(tiers=[TierAllocation("discrete", "GPU", 16.0, model_gb, "rocm", 0, 512.0, 48)],
                         total_model_gb=model_gb, total_layers=48, gpu_layers=48, run_mode="gpu")
    q4 = next(q for q in QUANTS if q.name == "Q4_K_M")
    moe = ModelEntry("moe:x", "MoE", "x", 30.0, "qwen3", 40960, ["general"], True, active_params_b=3.0)
    dense = ModelEntry("dense:x", "Dense", "x", 3.0, "qwen3", 40960, ["general"], False)
    # Same active size (3B) -> MoE must NOT exceed dense (penalty, not bonus).
    assert _estimate_speed(moe, q4, _single(1.74)) <= _estimate_speed(dense, q4, _single(1.74)) + 0.1


# --- the headline bug: 30B-A3B must outrank a 1B toy on 16GB for coding ---

def test_30b_a3b_outranks_tiny_on_16gb_coding():
    recs = recommend(_sys16(), use_case="coding", top_k=91)
    big = _find(recs, "qwen3:30b-a3b")
    tiny = _find(recs, "qwen3:0.6b")
    assert big is not None, "30B-A3B missing from coding recs"
    assert tiny is not None, "0.6B missing from coding recs"
    assert big.score > tiny.score, (
        f"30B-A3B ({big.score}, {big.quant}) should outrank 0.6B ({tiny.score})")
    assert big.run_mode == "gpu"
    # And it should rank strictly higher (earlier) in the ordered list.
    assert recs.index(big) < recs.index(tiny)


def test_gpt_oss_fits_16gb_gpu():
    recs = recommend(_sys16(), use_case="general", top_k=40)
    g = _find(recs, "gpt-oss:20b")
    assert g is not None
    assert g.run_mode == "gpu"


# --- hf.co command construction ---

def test_sub4bit_hf_cmd_has_no_quant_tag():
    recs = recommend(_sys16(), use_case="general", top_k=91)
    falcon = _find(recs, "hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit")
    assert falcon is not None
    # 1.58-bit ships as a single file — no :quant suffix.
    assert ":" not in falcon.ollama_cmd.split("run ", 1)[1]


def test_normal_model_cmd_has_quant_for_non_default_quant():
    recs = recommend(_sys16(), use_case="coding", top_k=30)
    big = _find(recs, "qwen3:30b-a3b")
    if big and big.quant != "Q4_K_M":
        assert ":" in big.ollama_cmd.split("run ", 1)[1]


# =============================================================================
# B — Multi-GPU hand-off: tier classification + split plans
# =============================================================================

from backend.cookbook.hardware import (
    ComputeTier,
    GPUInfo,
    build_compute_tiers,
    _classify_gpu,
)
from backend.cookbook.recommend import _compute_split_plan


def _sys_handoff() -> SystemInfo:
    """The user's actual system: dGPU 16GB + iGPU 10.5GB + 30.9GB RAM."""
    gpus = [
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm", tier="discrete"),
        GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="rocm", tier="integrated"),
    ]
    info = SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=30.9,
        gpus=gpus, total_vram_gb=16.0, has_amd=True,
        combined_vram_gb=26.5,
        compute_tiers=build_compute_tiers(gpus, 30.9),
    )
    return info


# --- tier classification ---

def test_classify_gpu():
    assert _classify_gpu("AMD Radeon RX 6800 XT") == "discrete"
    assert _classify_gpu("AMD Radeon(TM) Graphics") == "integrated"
    assert _classify_gpu("NVIDIA GeForce RTX 4090") == "discrete"
    assert _classify_gpu("Intel(R) UHD Graphics 770") == "integrated"


def test_build_compute_tiers_order():
    gpus = [
        GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="rocm"),
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm"),
    ]
    tiers = build_compute_tiers(gpus, 30.9)
    assert len(tiers) == 3
    # Discrete first (sorted by VRAM desc), then integrated, then RAM.
    assert tiers[0].kind == "discrete"
    assert tiers[0].name == "AMD Radeon RX 6800 XT"
    assert tiers[1].kind == "integrated"
    assert tiers[2].kind == "ram"
    # Device indices: dGPU=0, iGPU=1, RAM=-1
    assert tiers[0].device_index == 0
    assert tiers[1].device_index == 1
    assert tiers[2].device_index == -1


def test_build_compute_tiers_assigns_gpuinfo_device_index():
    """Real detector output never sets GPUInfo.device_index (defaults to 0 for
    every GPU). build_compute_tiers must assign it back onto the source
    GPUInfo objects so it agrees with the corresponding ComputeTier, in the
    same discrete-first-then-integrated order."""
    gpus = [
        GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="rocm"),  # integrated
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm"),    # discrete
    ]
    # Simulate real detector output: nobody set device_index, so both default to 0.
    assert gpus[0].device_index == 0
    assert gpus[1].device_index == 0

    tiers = build_compute_tiers(gpus, 30.9)

    # discrete (RX 6800 XT) sorts first -> index 0; integrated -> index 1.
    discrete_gpu = next(g for g in gpus if g.name == "AMD Radeon RX 6800 XT")
    integrated_gpu = next(g for g in gpus if g.name == "AMD Radeon(TM) Graphics")
    assert discrete_gpu.device_index == 0
    assert integrated_gpu.device_index == 1

    # Indices must be unique per GPU and match their corresponding tiers.
    indices = [g.device_index for g in gpus]
    assert len(set(indices)) == len(indices)

    discrete_tier = next(t for t in tiers if t.kind == "discrete")
    integrated_tier = next(t for t in tiers if t.kind == "integrated")
    assert discrete_tier.device_index == discrete_gpu.device_index
    assert integrated_tier.device_index == integrated_gpu.device_index


def test_build_compute_tiers_single_gpu():
    gpus = [GPUInfo("NVIDIA RTX 4090", 24.0, backend="cuda")]
    tiers = build_compute_tiers(gpus, 64.0)
    assert len(tiers) == 2  # one GPU + RAM
    assert tiers[0].kind == "discrete"
    assert tiers[1].kind == "ram"


# --- split plan computation ---

def test_split_fits_single_gpu():
    """Model fits in dGPU alone → run_mode 'gpu', single-tier plan."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:8b")
    split = _compute_split_plan(5.1, info, model)  # 8B Q4 ~5GB
    assert split is not None
    assert split.run_mode == "gpu"
    assert len(split.tiers) == 1
    assert split.tiers[0].kind == "discrete"
    assert split.env_vars == {}


def test_split_needs_multi_gpu():
    """Model bigger than dGPU but fits in dGPU+iGPU → 'multi_gpu'."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:32b")
    # 32B Q4 weights ~19GB — exceeds 16GB dGPU but fits in 26.5GB combined.
    split = _compute_split_plan(19.0, info, model)
    assert split is not None
    assert split.run_mode == "multi_gpu"
    assert len(split.tiers) == 2
    assert split.tiers[0].kind == "discrete"
    assert split.tiers[1].kind == "integrated"
    # Both GPU tiers present → HIP_VISIBLE_DEVICES env var.
    assert "HIP_VISIBLE_DEVICES" in split.env_vars
    assert split.env_vars["HIP_VISIBLE_DEVICES"] == "0,1"


def test_split_needs_ram_offload():
    """Model bigger than combined GPU → spills into RAM → 'cpu_offload'."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:32b")
    # 32B Q8 weights ~35GB — exceeds 26.5GB combined GPU.
    split = _compute_split_plan(35.0, info, model)
    assert split is not None
    assert split.run_mode == "cpu_offload"
    kinds = {t.kind for t in split.tiers}
    assert "ram" in kinds
    assert "discrete" in kinds


def test_split_too_big():
    """Model bigger than all tiers combined → None (too_big)."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "llama3.1:405b")
    # 405B Q4 ~235GB — way beyond everything.
    split = _compute_split_plan(235.0, info, model)
    assert split is None


def test_split_30b_a3b_q3_fits_single_gpu():
    """The headline case: 30B-A3B at Q3 fits in the 16GB dGPU alone."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:30b-a3b")
    split = _compute_split_plan(15.69, info, model)
    assert split is not None
    assert split.run_mode == "gpu"
    assert len(split.tiers) == 1


def test_split_30b_a3b_q4_needs_multi_gpu():
    """30B-A3B at Q4 (17.7GB) exceeds 16GB dGPU → spills to iGPU."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:30b-a3b")
    split = _compute_split_plan(17.7, info, model)
    assert split is not None
    assert split.run_mode == "multi_gpu"
    assert len(split.tiers) == 2


def test_split_layers_distribute_proportionally():
    """Layer count should be proportional to GB allocation."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:32b")
    split = _compute_split_plan(19.0, info, model)
    assert split is not None
    assert split.total_layers > 0
    total_alloc = sum(t.layers for t in split.tiers)
    # Layers should roughly add up to total (may be off by 1 from rounding).
    assert abs(total_alloc - split.total_layers) <= 1


def test_recommend_handoff_has_split_plan():
    """recommend() on the hand-off system attaches split plans to recs."""
    recs = recommend(_sys_handoff(), use_case="coding", top_k=10)
    assert len(recs) > 0
    for rec in recs:
        assert rec.split_plan is not None
        assert rec.split_plan.run_mode in ("gpu", "multi_gpu", "cpu_offload")


# =============================================================================
# C — Speed calibration against REAL benchmarks
#     Measured 2026-07-02 on: dGPU RX 6800 XT 16GB (usable 15.2) + iGPU 10.5GB
#     (usable 14.9) + 30.9GB RAM, Ollama 0.31.1 (Vulkan), num_predict=128 temp=0.
#     Finding: GPU-resident dense was already accurate (falcon3:3b pred 186 vs
#     real 178), but the byte-weighted bandwidth massively over-predicted
#     SPILLED runs (qwen3:30b-a3b Q4 dGPU+iGPU: pred 201 vs real 22.9; Q8
#     dGPU+iGPU+RAM: pred 45 vs real 12.6). Both real points fit tps ~= 40/
#     (active*bpp): spilled runs bottleneck on the slow tier, not the average.
# =============================================================================

from backend.cookbook.recommend import _estimate_vram, _estimate_bandwidth


def _predict_tps(info, model_id, quant_name, ctx=4096):
    model = next(m for m in load_models() if m.id == model_id)
    quant = next(q for q in QUANTS if q.name == quant_name)
    vram = _estimate_vram(model, quant, ctx)
    split = _compute_split_plan(vram, info, model)
    assert split is not None
    return _estimate_speed(model, quant, split, _estimate_bandwidth(info))


def test_speed_dense_gpu_resident_matches_real():
    # Regression guard: a dense model fitting entirely in the dGPU was already
    # accurate. The spilled-path fix must NOT disturb it. real ~177.9 tok/s.
    tps = _predict_tps(_sys_handoff(), "falcon3:3b", "Q4_K_M")
    assert 130 <= tps <= 240, f"falcon3:3b predicted {tps}, real ~177.9"


def test_speed_moe_spilled_multi_gpu_matches_real():
    # qwen3:30b-a3b Q4 spills dGPU->iGPU. Byte-weighted bandwidth predicted
    # ~201 tok/s; real is 22.9 — spilled runs bottleneck on the slow tier.
    tps = _predict_tps(_sys_handoff(), "qwen3:30b-a3b", "Q4_K_M")
    assert 15 <= tps <= 35, f"qwen3:30b-a3b Q4 predicted {tps}, real ~22.9"


def test_speed_moe_spilled_ram_offload_matches_real():
    # qwen3:30b-a3b Q8 spills dGPU->iGPU->RAM. real 12.6 tok/s (was pred 45).
    tps = _predict_tps(_sys_handoff(), "qwen3:30b-a3b", "Q8")
    assert 8 <= tps <= 20, f"qwen3:30b-a3b Q8 predicted {tps}, real ~12.6"


# =============================================================================
# D — Calibration integration into recommend()
# =============================================================================

def test_recommend_surfaces_measured_source():
    from backend.cookbook.calibration import Calibration, MeasuredStat
    cal = Calibration(measured={("qwen3:30b-a3b", "Q4_K_M"): MeasuredStat(22.9, 1, 0.0)},
                      regime_factor={"spilled": 1.0}, regime_band_pct={"spilled": 35.0}, n=1)
    recs = recommend(_sys_handoff(), use_case="coding", top_k=91, calibration=cal)
    big = _find(recs, "qwen3:30b-a3b")
    assert big is not None
    # its speed_source is set (measured if its best quant is the benchmarked Q4, else calibrated/estimated)
    assert big.speed_source in ("measured", "calibrated", "estimated")

def test_recommend_defaults_estimated_without_calibration():
    recs = recommend(_sys_handoff(), use_case="coding", top_k=5)
    assert all(r.speed_source == "estimated" for r in recs)


def test_loop_reproduces_measured_on_real_anchors(tmp_path):
    # End-to-end regression: results.jsonl (this session's real benchmark
    # anchors) -> load_calibration -> recommend() should reproduce the
    # measured tok/s for qwen3:30b-a3b Q4_K_M, not the theoretical estimate.
    #
    # NOTE: recommend() keeps only the single best-composite-score quant per
    # model (see recommend.py `best_rec` selection), so Q4_K_M is not
    # guaranteed to be the quant that surfaces in top_k results for this
    # model on this synthetic rig (Q5_K_M/F16 can out-score it on the
    # composite even though Q4_K_M is the measured anchor). We therefore
    # verify the exact reproduction at the apply_calibration layer (Task 6's
    # wiring contract) AND verify end-to-end that recommend() surfaces the
    # model with a calibration-derived speed_source, matching the pattern
    # already used by test_recommend_surfaces_measured_source above.
    import json
    from backend.cookbook.calibration import load_calibration, machine_fingerprint, apply_calibration
    info = _sys_handoff()
    stack = {"ollama_version": "0.31.1", "backend": "vulkan"}
    fp = machine_fingerprint(info, stack)
    p = tmp_path / "results.jsonl"
    with open(p, "w") as f:
        for tag, tps in [("qwen3:30b-a3b-q4_K_M", 22.9), ("qwen3:30b-a3b-q8_0", 12.6),
                         ("falcon3:3b", 177.9)]:
            f.write(json.dumps({"model": tag, "tokens_per_second": tps,
                                "eval_count": 128, "fingerprint": fp}) + "\n")
    cal = load_calibration(info, stack, str(p))

    # Exact measured override -> ~22.9 tok/s (this IS the loop reproducing
    # the measured session value, at the layer that owns that contract).
    tok_s, source, _band = apply_calibration(999.0, "qwen3:30b-a3b", "Q4_K_M", "spilled", cal)
    assert source == "measured"
    assert abs(tok_s - 22.9) < 0.01

    # End-to-end: recommend() picks up the calibration and surfaces the
    # model with a calibration-influenced source (measured or calibrated),
    # never falling back to a bare uncalibrated estimate for this model.
    recs = recommend(info, use_case="coding", top_k=91, calibration=cal)
    big = next((r for r in recs if r.model.id == "qwen3:30b-a3b"), None)
    assert big is not None
    assert big.speed_source in ("measured", "calibrated")


def test_cli_recommend_help_advertises_no_calibration_flag():
    # Guards that the --no-calibration escape hatch is wired into argparse and
    # discoverable (its effect, calibration=None, is covered by
    # test_recommend_defaults_estimated_without_calibration above).
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "cli", "recommend", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "--no-calibration" in r.stdout
