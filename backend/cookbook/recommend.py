import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .hardware import GPUInfo, SystemInfo


@dataclass
class ModelEntry:
    id: str
    name: str
    provider: str
    params_b: float
    arch: str
    context: int
    use_cases: list[str]
    is_moe: bool
    vram_q4: float = 0
    vram_q8: float = 0
    vram_f16: float = 0
    active_params_b: Optional[float] = None
    sub4bit: bool = False


@dataclass
class TierAllocation:
    """How much of a model lands on one compute tier."""
    kind: str               # "discrete" | "integrated" | "ram"
    name: str               # display name
    memory_gb: float        # tier capacity
    allocated_gb: float     # model GB placed here
    backend: str            # "rocm", "cuda", "cpu"
    device_index: int = -1
    bandwidth: float = 0.0  # GB/s
    layers: int = 0


@dataclass
class SplitPlan:
    """Per-model layer distribution across compute tiers (the 'hand-off')."""
    tiers: list  # list[TierAllocation]
    total_model_gb: float
    total_layers: int
    gpu_layers: int        # layers on GPU (not RAM)
    run_mode: str          # "gpu" | "multi_gpu" | "cpu_offload"
    env_vars: dict = field(default_factory=dict)
    summary: str = ""


@dataclass
class Recommendation:
    model: ModelEntry
    quant: str
    vram_gb: float
    score: float
    quality_score: float
    speed_score: float
    fit_score: float
    context_score: float
    context_used: int
    run_mode: str
    ollama_cmd: str
    details: dict = field(default_factory=dict)
    split_plan: Optional[SplitPlan] = None


@dataclass
class QuantInfo:
    name: str
    bpp: float
    quality_penalty: float
    speed_mult: float
    sort_order: int


QUANTS = [
    QuantInfo("F16", 2.0, 0.0, 0.6, 0),
    QuantInfo("Q8", 1.05, 0.0, 0.8, 1),
    QuantInfo("Q6_K", 0.80, -2.0, 0.9, 2),
    QuantInfo("Q5_K_M", 0.68, -3.0, 1.0, 3),
    QuantInfo("Q4_K_M", 0.58, -5.0, 1.15, 4),
    QuantInfo("Q3_K_M", 0.48, -8.0, 1.25, 5),
    QuantInfo("Q2_K", 0.37, -12.0, 1.35, 6),
]

# 1.58-bit (BitNet) models ship at a single extreme quant, not the standard
# ladder. Used only for entries with sub4bit=True.
SUB4BIT_QUANT = QuantInfo("1.58bit", 0.20, -15.0, 1.5, 7)

ARCH_SPEED_BONUS = {
    "qwen3": 1.05, "qwen": 1.0, "llama": 1.0, "mistral": 1.02,
    "gemma": 1.03, "phi3": 0.95, "phi4": 0.95, "deepseek": 0.90,
    "mellum": 1.05, "cohere": 0.85, "yi": 1.0, "nemotron": 1.0,
    "starcoder": 1.0, "dbrx": 0.92, "gpt-oss": 1.0, "smollm": 1.05,
    "falcon": 1.0, "bitnet": 1.1, "olmoe": 1.1,
}

GPU_BANDWIDTH = {
    "5090": 1792, "5080": 960, "5070 ti": 896, "5070": 640,
    "5060 ti": 507, "5060": 355,
    "4090": 1008, "4080 super": 736, "4080": 717,
    "4070 ti super": 672, "4070 ti": 672, "4070 super": 504,
    "4070": 504, "4060 ti": 288, "4060": 272,
    "3090 ti": 1008, "3090": 936, "3080 ti": 912,
    "3080": 760, "3070 ti": 608, "3070": 448,
    "3060 ti": 448, "3060": 360,
    "a100": 2039, "a100 80gb": 2039, "a6000": 768,
    "h100": 3350, "h200": 4800, "b200": 4500,
    "7900 xtx": 960, "7900 xt": 800, "7800 xt": 624,
    "7700 xt": 432, "7600 xt": 384,
    "9070 xt": 624, "9070": 488, "9060 xt": 400,
    "mi300x": 5300, "mi250": 3277, "mi210": 1638,
    "6800 xt": 512, "6800": 512, "6700 xt": 384,
    "6600 xt": 256, "6600": 224,
}

MODEL_FAMILY_QUALITY_BONUS = {
    "deepseek": 3, "qwen3": 2, "qwen": 1, "llama": 2,
    "mistral": 1, "gemma": 1, "phi3": 1, "phi4": 2,
    "mellum": 2, "cohere": 1, "yi": 0, "nemotron": 0,
    "starcoder": 0, "dbrx": 2, "gpt-oss": 3, "smollm": 0,
    "falcon": 0, "bitnet": -2, "olmoe": 1,
}

USE_CASE_WEIGHTS = {
    "general": (0.35, 0.25, 0.30, 0.10),
    "coding": (0.40, 0.15, 0.30, 0.15),
    "reasoning": (0.45, 0.10, 0.30, 0.15),
    "chat": (0.30, 0.30, 0.30, 0.10),
}

CONTEXT_TARGETS = {"general": 4096, "coding": 8192, "reasoning": 8192, "chat": 4096}

DATA_DIR = Path(__file__).parent / "data"


def load_models() -> list[ModelEntry]:
    path = DATA_DIR / "models.json"
    if not path.exists():
        raise FileNotFoundError(f"Model database not found at {path}")
    with open(path) as f:
        raw = json.load(f)
    return [ModelEntry(**m) for m in raw]


def _quality_base(params_b: float) -> float:
    if params_b < 1: return 30
    if params_b < 3: return 45
    if params_b < 7: return 60
    if params_b < 10: return 75
    if params_b < 20: return 82
    if params_b < 40: return 89
    return 95


def _estimate_bandwidth(info: SystemInfo) -> float:
    if info.is_apple_silicon and info.gpus:
        name = info.gpus[0].name.lower()
        for key, bw in sorted(GPU_BANDWIDTH.items(), key=lambda x: -len(x[0])):
            if key in name: return bw
        return 150
    for gpu in info.gpus:
        name = gpu.name.lower()
        for key, bw in sorted(GPU_BANDWIDTH.items(), key=lambda x: -len(x[0])):
            if key in name: return bw
    if info.gpus:
        return {"cuda": 220, "rocm": 180, "metal": 150, "vulkan": 120}.get(info.gpus[0].backend, 100)
    return 50


def _estimate_vram(model: ModelEntry, quant: QuantInfo, ctx: int) -> float:
    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    weights_gb = model.params_b * quant.bpp
    kv_gb = 0.000008 * active * ctx
    overhead = 0.5
    return round(weights_gb + kv_gb + overhead, 2)


def _fit_score(utilization: float, run_mode: str) -> float:
    """Fit quality: rewards EFFICIENT USE of available VRAM, not smallness.

    A model that uses >=75% of VRAM (good utilization) scores 100 — using more
    VRAM for a better quant is a GOOD trade, so we do not penalize the 75-100%
    band. A tiny model that wastes most of the VRAM scores ~50 (it fits, but
    underuses the hardware). Spilling into CPU offload declines with overspill.

    This is what stops a 1B toy from outranking a 30B-A3B that fits at Q3:
    both "fit", but the 30B uses the hardware instead of wasting it.
    """
    if run_mode == "cpu_offload":
        return max(0.0, 60.0 - (utilization - 1.0) * 45.0)
    if utilization <= 0.10:
        return 50.0
    if utilization < 0.75:
        # 50 -> 100 as utilization rises from 10% to 75%.
        return 50.0 + (utilization - 0.10) / 0.65 * 50.0
    # 75% -> 100% : flat — all "good utilization", let quality decide between quants.
    return 100.0


def _ollama_cmd(model: ModelEntry, quant: QuantInfo) -> str:
    # HuggingFace GGUF pulls (hf.co/...) need an explicit :QUANT tag (uppercase)
    # to select the file — except 1.58-bit models, which ship as a single file.
    if model.id.startswith("hf.co/"):
        if model.sub4bit:
            return f"ollama run {model.id}"
        return f"ollama run {model.id}:{quant.name}"
    # Standard Ollama models: Q4_K_M is the default tag, so omit it.
    quant_tag = quant.name.lower().replace("_", "-")
    if quant_tag == "q4-k-m":
        return f"ollama run {model.id}"
    return f"ollama run {model.id}:{quant_tag}"


# --- Hand-off: multi-GPU + iGPU + RAM layer-split planning ---

# Usable fraction of each tier's memory. _estimate_vram already includes a
# 0.5 GB compute overhead, so discrete GPUs can use full VRAM. iGPU shares
# system RAM so we keep more free; RAM is split with the OS + app.
TIER_HEADROOM = {"discrete": 1.0, "integrated": 0.90, "ram": 0.50}

# Rough bandwidth (GB/s) for non-discrete tiers.
_IGPU_BANDWIDTH = 64.0   # DDR5 dual-channel via GPU
_RAM_BANDWIDTH = 50.0    # DDR5 via CPU

# Spilled runs (multi_gpu / cpu_offload) never hit the byte-weighted average
# bandwidth: every token must traverse the layers on the slowest tier, and MoE
# expert-routing scatters reads onto it. Calibrated 2026-07-02 against real
# benchmarks (qwen3:30b-a3b Q4 dGPU+iGPU: real 22.9 vs 201 predicted; Q8
# dGPU+iGPU+RAM: real 12.6 vs 45) — effective throughput tracks the slowest
# active tier's bandwidth x this efficiency. NOTE: fit to MoE-spill anchors on
# an AMD/Vulkan box; dense-spill is unvalidated — revisit with a dense >dGPU
# anchor before trusting large-dense-model estimates.
SPILL_EFFICIENCY = 0.65

# GPU-resident MoE decode takes a slight PENALTY vs dense, not a bonus:
# irregular per-token expert I/O breaks the clean sequential read pattern
# that dense bandwidth-bound decode enjoys (spec §4/§9.1).
MOE_DECODE_PENALTY = 0.9


def _estimate_layers(model: ModelEntry) -> int:
    """Rough layer count for the --gpu-layers flag.

    Calibrated against known architectures: 7B~32, 14B~40, 30B MoE~48, 70B~80.
    """
    return max(16, int(14 * model.params_b ** 0.38))


def _tier_bandwidth(tier) -> float:
    """Estimate memory bandwidth (GB/s) for a compute tier."""
    if tier.kind == "ram":
        return _RAM_BANDWIDTH
    if tier.kind == "integrated":
        return _IGPU_BANDWIDTH
    # Discrete: look up by GPU name in the bandwidth table.
    name = tier.name.lower()
    for key, bw in sorted(GPU_BANDWIDTH.items(), key=lambda x: -len(x[0])):
        if key in name:
            return bw
    return {"cuda": 220, "rocm": 180, "metal": 150, "vulkan": 120}.get(tier.backend, 100)


def _compute_split_plan(vram_needed: float, info: SystemInfo,
                        model: ModelEntry) -> Optional[SplitPlan]:
    """Distribute a model's memory across compute tiers (dGPU → iGPU → RAM).

    Returns None if the model doesn't fit even across all tiers ("too_big").
    """
    tiers = info.compute_tiers
    if not tiers:
        from .hardware import ComputeTier
        if info.total_vram_gb > 0:
            tiers = [ComputeTier("GPU", info.total_vram_gb,
                                  "rocm" if info.has_amd else "cuda", "discrete", 0)]
        if info.ram_gb > 0:
            tiers = tiers + [ComputeTier("System RAM", info.ram_gb, "cpu", "ram", -1)]
        if not tiers:
            return None

    remaining = vram_needed
    allocs: list[TierAllocation] = []
    used_kinds: set[str] = set()

    for tier in tiers:
        if remaining <= 0.01:
            break
        capacity = tier.memory_gb * TIER_HEADROOM.get(tier.kind, 0.75)
        allocated = min(remaining, capacity)
        if allocated > 0.01:
            allocs.append(TierAllocation(
                kind=tier.kind, name=tier.name, memory_gb=tier.memory_gb,
                allocated_gb=round(allocated, 2), backend=tier.backend,
                device_index=tier.device_index, bandwidth=_tier_bandwidth(tier),
            ))
            remaining -= allocated
            used_kinds.add(tier.kind)

    # Doesn't fit even with all tiers (allow small tolerance for rounding).
    total_capacity = sum(t.memory_gb * TIER_HEADROOM.get(t.kind, 0.75) for t in tiers)
    if vram_needed > total_capacity + 0.5:
        return None

    # Absorb tiny rounding remainder into the last tier.
    if remaining > 0.01 and allocs:
        allocs[-1].allocated_gb = round(allocs[-1].allocated_gb + remaining, 2)
        remaining = 0

    # Determine run mode.
    gpu_kinds = used_kinds & {"discrete", "integrated"}
    if "ram" in used_kinds:
        run_mode = "cpu_offload"
    elif len(gpu_kinds) > 1 or "integrated" in used_kinds:
        run_mode = "multi_gpu"
    else:
        run_mode = "gpu"

    # Distribute layers proportionally to GB.
    total_layers = _estimate_layers(model)
    total_gb = sum(a.allocated_gb for a in allocs)
    for a in allocs:
        a.layers = max(0, round(total_layers * a.allocated_gb / max(total_gb, 0.1)))
    gpu_layers = sum(a.layers for a in allocs if a.kind in ("discrete", "integrated"))

    # Env vars to expose both GPUs.
    env_vars: dict[str, str] = {}
    gpu_allocs = [a for a in allocs if a.kind in ("discrete", "integrated")]
    if len(gpu_allocs) > 1:
        indices = ",".join(str(a.device_index) for a in gpu_allocs)
        if any(a.backend == "rocm" for a in gpu_allocs):
            env_vars["HIP_VISIBLE_DEVICES"] = indices
        elif any(a.backend == "cuda" for a in gpu_allocs):
            env_vars["CUDA_VISIBLE_DEVICES"] = indices

    # Human-readable summary.
    parts = []
    for a in allocs:
        short = a.name.replace("AMD Radeon ", "").replace("AMD ", "")
        if a.kind == "ram":
            short = "RAM"
        parts.append(f"{a.allocated_gb} GB {short} ({a.layers}L)")
    summary = " + ".join(parts)

    return SplitPlan(
        tiers=allocs, total_model_gb=round(total_gb, 2),
        total_layers=total_layers, gpu_layers=gpu_layers,
        run_mode=run_mode, env_vars=env_vars, summary=summary,
    )


def recommend(info: SystemInfo, use_case: str = "coding",
              min_context: int = 0, top_k: int = 5) -> list[Recommendation]:
    models = load_models()
    bw = _estimate_bandwidth(info)
    has_tiers = bool(info.compute_tiers)
    if has_tiers:
        combined_gpu = sum(t.memory_gb for t in info.compute_tiers if t.kind != "ram")
        avail_vram = combined_gpu if combined_gpu > 0 else max(info.ram_gb * 0.5, 0.1)
    else:
        avail_vram = max(info.total_vram_gb, info.ram_gb * 0.25)

    w_quality, w_speed, w_fit, w_context = USE_CASE_WEIGHTS.get(use_case, (0.35, 0.25, 0.30, 0.10))
    ctx_target = max(CONTEXT_TARGETS.get(use_case, 4096), min_context)

    all_recs: list[Recommendation] = []

    for model in models:
        if use_case not in model.use_cases and "general" not in model.use_cases:
            continue

        best_rec: Optional[Recommendation] = None
        model_vram_q4 = model.vram_q4 or _estimate_vram(model, QUANTS[4], model.context)
        ctx_options = [c for c in [model.context, 65536, 32768, 16384, 8192, 4096, 2048]
                       if c <= model.context]
        quants = [SUB4BIT_QUANT] if model.sub4bit else QUANTS

        for quant in quants:
            for ctx in ctx_options:
                vram_needed = _estimate_vram(model, quant, ctx)

                # Compute the hand-off split plan (or fallback for no tiers).
                split = _compute_split_plan(vram_needed, info, model)
                if split is None:
                    continue
                run_mode = split.run_mode

                quality = max(0, min(100, _quality_base(model.params_b)
                    + MODEL_FAMILY_QUALITY_BONUS.get(model.arch, 0) + quant.quality_penalty))

                speed = _estimate_speed(model, quant, split, bw)
                target_speed = 20 if use_case == "reasoning" else 30
                speed_score = min(100, (speed / target_speed) * 100)

                # Fit: base utilization on MODEL WEIGHTS (the useful payload),
                # not total VRAM including KV cache. A bloated KV at 131k ctx is
                # overhead, not "good utilization" — a 12B model at Q4 is still
                # only ~7 GB of actual model regardless of context.
                weights_gb = model.params_b * (SUB4BIT_QUANT.bpp if model.sub4bit else quant.bpp)
                utilization = weights_gb / max(avail_vram, 0.1)
                fit = _fit_score(utilization, run_mode)

                # Context: meeting the target ~60, scaling toward 100 at a practical
                # reference (32k is plenty for local coding/chat), so 32k+ saturates
                # and we don't over-reward 128k/262k context that costs KV memory.
                ctx_ref = max(ctx_target * 4, 32768)
                if ctx >= ctx_ref:
                    cscore = 100.0
                elif ctx >= ctx_target:
                    cscore = 60.0 + (ctx - ctx_target) / (ctx_ref - ctx_target) * 40.0
                else:
                    cscore = 60.0 * (ctx / max(ctx_target, 1))
                composite = quality * w_quality + speed_score * w_speed + fit * w_fit + cscore * w_context

                rec = Recommendation(
                    model=model, quant=quant.name, vram_gb=vram_needed,
                    score=round(composite, 1), quality_score=round(quality, 1),
                    speed_score=round(speed_score, 1), fit_score=round(fit, 1),
                    context_score=round(cscore, 1), context_used=ctx,
                    run_mode=run_mode, ollama_cmd=_ollama_cmd(model, quant),
                    details={
                        "vram_q4": model_vram_q4, "params_b": model.params_b,
                        "provider": model.provider,
                        "active_params_b": model.active_params_b,
                        "is_moe": model.is_moe,
                    },
                    split_plan=split,
                )

                if best_rec is None or rec.score > best_rec.score:
                    best_rec = rec

        if best_rec:
            all_recs.append(best_rec)

    all_recs.sort(key=lambda r: r.score, reverse=True)
    return all_recs[:top_k]


def speed_regime(split: "SplitPlan") -> str:
    """Classify a split plan into the speed regime used by _estimate_speed."""
    return "gpu" if split and split.run_mode == "gpu" else "spilled"


def _estimate_speed(model: ModelEntry, quant: QuantInfo, split: SplitPlan,
                    bw_fallback: float = 200.0) -> float:
    """Estimate decode tok/s from the split plan.

    Two regimes, calibrated 2026-07-02 against real benchmarks (RX 6800 XT +
    iGPU + RAM, Ollama 0.31.1 / Vulkan, num_predict=128 temp=0):

    * GPU-RESIDENT (run_mode 'gpu', one fast tier): decode is bandwidth-bound
      on the bytes read per token (active params x bpp). Validated —
      falcon3:3b Q4 predicted 186 vs real 178 tok/s.
    * SPILLED (multi_gpu / cpu_offload): every token must traverse the layers
      sitting on the slow tier, so throughput collapses toward the SLOWEST
      active tier's bandwidth — NOT the byte-weighted average, which
      over-predicted ~9x (qwen3:30b-a3b Q4 dGPU+iGPU: real 22.9, not 201).
      Both measured points fit tps ~= slow_bw * SPILL_EFFICIENCY / (active*bpp).
    """
    if split is None or not split.tiers:
        return 1.0

    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    model_gb = active * quant.bpp
    arch = ARCH_SPEED_BONUS.get(model.arch, 1.0)

    if split.run_mode != "gpu":
        # Spilled: bottleneck on the slowest tier that holds real weight. The
        # quant speed_mult / moe_bonus are dropped here — bpp is already in
        # model_gb, and MoE offload gets no expert-locality benefit once
        # experts live on the slow tier.
        slow_bw = min((a.bandwidth for a in split.tiers if a.allocated_gb > 0.05),
                      default=_RAM_BANDWIDTH)
        tps = (slow_bw * SPILL_EFFICIENCY / max(model_gb, 0.5)) * arch
        return round(tps, 1)

    # GPU-resident: bandwidth-bound on the single fast tier.
    total_gb = split.total_model_gb
    bw_eff = sum(a.allocated_gb / max(total_gb, 0.1) * a.bandwidth for a in split.tiers)
    moe_bonus = MOE_DECODE_PENALTY if model.is_moe else 1.0
    tps = (bw_eff / max(model_gb, 0.5)) * 0.55 * quant.speed_mult * moe_bonus * arch
    return round(tps, 1)


def print_recommendations(recs: list[Recommendation], info: SystemInfo, use_case: str) -> None:
    if not recs:
        print("No models found that fit your hardware.")
        return
    print(f"Top {len(recs)} recommendations for '{use_case}' on your hardware:\n")
    print(f"{'#':<3} {'Model':<35} {'Quant':<7} {'Score':<7} {'VRAM':<7} {'Ctx':<7} {'Mode':<13} {'Command'}")
    print("-" * 120)
    for i, rec in enumerate(recs, 1):
        mode = {"gpu": "GPU", "multi_gpu": "Multi-GPU", "cpu_offload": "Offload"}.get(rec.run_mode, rec.run_mode)
        print(f"{i:<3} {rec.model.name:<35} {rec.quant:<7} {rec.score:<7} {rec.vram_gb:<7} {rec.context_used:<7} {mode:<13} {rec.ollama_cmd}")
    print()
    for i, rec in enumerate(recs[:3], 1):
        split_info = f"  split: {rec.split_plan.summary}" if rec.split_plan and rec.split_plan.run_mode != "gpu" else ""
        print(f"  {i}. {rec.model.name} ({rec.quant}) — Quality: {rec.quality_score:.0f} Speed: {rec.speed_score:.0f} Fit: {rec.fit_score:.0f} Ctx: {rec.context_score:.0f} = {rec.score}{split_info}")
    for i, rec in enumerate(recs[:3], 1):
        print(f"  {i}. {rec.model.name} ({rec.quant}) — Quality: {rec.quality_score:.0f} Speed: {rec.speed_score:.0f} Fit: {rec.fit_score:.0f} Ctx: {rec.context_score:.0f} = {rec.score}")
