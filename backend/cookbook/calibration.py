# backend/cookbook/calibration.py
"""Per-machine self-calibration: turn real apt-benchmark results into
corrected recommendations. Pure/Ollama-free except detect_stack()."""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .recommend import (
    load_models, QUANTS, SUB4BIT_QUANT,
    _estimate_vram, _estimate_bandwidth, _compute_split_plan, _estimate_speed,
    speed_regime,
)

_CTX = 4096  # match Ollama's default_num_ctx used at benchmark time

# ollama quant-suffix (lowercased) -> catalog quant name. Most catalog quant
# names (e.g. "Q4_K_M", "Q6_K") already match the ollama tag suffix once
# lowercased. A couple of ollama's real suffixes are irregular relative to
# the catalog name ("q8_0" for "Q8", "fp16" for "F16") and need an alias.
_QUANT_ALIASES = {"Q8": "q8_0", "F16": "fp16"}
_SUFFIX_TO_QUANT: dict[str, str] = {}
for _q in QUANTS:
    _SUFFIX_TO_QUANT[_q.name.lower()] = _q.name
    if _q.name in _QUANT_ALIASES:
        _SUFFIX_TO_QUANT[_QUANT_ALIASES[_q.name]] = _q.name


def parse_model_tag(tag: str) -> tuple[str, str] | None:
    """Map an ollama tag to (catalog_id, quant_name), or None if unknown."""
    ids = {m.id: m for m in load_models()}
    # exact catalog id (incl. hf.co sub-4bit) -> its single/default quant
    if tag in ids:
        m = ids[tag]
        return (tag, SUB4BIT_QUANT.name if m.sub4bit else "Q4_K_M")
    # try stripping a quant suffix: "<id>-<quant>"
    for suffix, qname in _SUFFIX_TO_QUANT.items():
        needle = "-" + suffix
        if tag.lower().endswith(needle):
            base = tag[: -len(needle)]
            if base in ids:
                return (base, qname)
    return None


def machine_fingerprint(info, stack: dict) -> str:
    # JSON canonical form (sorted) so a GPU name containing '|'/';'/'=' can't
    # collide with the field delimiters and silently blend two stacks.
    gpus = sorted(
        ({"name": g.name, "backend": g.backend, "vram_gb": round(g.vram_gb, 1)}
         for g in info.gpus),
        key=lambda d: (d["name"], d["backend"], d["vram_gb"]),
    )
    canon = {
        "gpus": gpus,
        "ram_gb": round(info.ram_gb),
        "ollama_version": stack.get("ollama_version", "unknown"),
        "backend": stack.get("backend", "unknown"),
    }
    return hashlib.sha1(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:12]


def detect_stack(base_url: str = "http://localhost:11434", info=None) -> dict:
    """Best-effort software-stack probe. Never raises.

    Backend is inferred only when unambiguous (never guesses the AMD
    rocm-vs-vulkan case, which needs OLLAMA_LLM_LIBRARY or log parsing —
    spec §11). OLLAMA_LLM_LIBRARY (the authoritative Ollama override) wins;
    else Apple Silicon -> metal, NVIDIA -> cuda, otherwise 'unknown'.
    """
    version = "unknown"
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=1.5) as r:
            version = json.loads(r.read().decode()).get("version", "unknown")
    except Exception:
        pass
    return {"ollama_version": version, "backend": _infer_backend(info)}


def _infer_backend(info) -> str:
    # OLLAMA_LLM_LIBRARY is the authoritative, user-explicit override and the
    # only reliable way to distinguish AMD rocm vs vulkan; honor it first.
    lib = os.environ.get("OLLAMA_LLM_LIBRARY")
    if lib:
        return lib.lower()
    if info is None:
        return "unknown"
    if getattr(info, "is_apple_silicon", False):
        return "metal"
    if getattr(info, "has_nvidia", False):
        return "cuda"
    return "unknown"


@dataclass
class MeasuredStat:
    median_tps: float
    n_runs: int
    spread_pct: float
    provenance: str = "measured"


@dataclass
class Calibration:
    measured: dict = field(default_factory=dict)         # (id,quant) -> MeasuredStat
    regime_factor: dict = field(default_factory=dict)    # regime -> float
    regime_band_pct: dict = field(default_factory=dict)  # regime -> float
    n: int = 0


def _geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else 1.0


def _quant_by_name(name):
    if name == SUB4BIT_QUANT.name:
        return SUB4BIT_QUANT
    return next((q for q in QUANTS if q.name == name), None)


def load_calibration(info, stack, results_path, models=None) -> Calibration:
    path = Path(results_path)
    if not path.exists():
        return Calibration()
    fp = machine_fingerprint(info, stack)
    ids = {m.id: m for m in (models or load_models())}
    bw = _estimate_bandwidth(info)

    # Keep exact current-machine samples separate from pre-fingerprint rows.
    # Legacy values remain useful as estimates, but mixing them would make an
    # unknown machine/stack look like an exact measurement for this one.
    exact_samples: dict = {}
    legacy_samples: dict = {}
    ratios: dict = {}
    n = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        tps = e.get("tokens_per_second")
        tag = e.get("model")
        if not tps or not tag:
            continue
        entry_fp = e.get("fingerprint")
        legacy = entry_fp is None            # unknown provenance
        if entry_fp is not None and entry_fp != fp:
            continue                          # foreign machine/stack -> skip entirely
        parsed = parse_model_tag(tag)
        if not parsed:
            continue
        cid, qname = parsed
        model = ids.get(cid)
        quant = _quant_by_name(qname)
        if model is None or quant is None:
            continue
        n += 1
        if legacy:
            legacy_samples.setdefault((cid, qname), []).append(float(tps))
            continue                          # unknown provenance: never fit current hardware
        exact_samples.setdefault((cid, qname), []).append(float(tps))
        vram = _estimate_vram(model, quant, _CTX)
        split = _compute_split_plan(vram, info, model)
        if split is None:
            continue
        theo = _estimate_speed(model, quant, split, bw)
        if theo > 0:
            ratios.setdefault(speed_regime(split), []).append(float(tps) / theo)

    measured = {}
    for key in exact_samples.keys() | legacy_samples.keys():
        vals = exact_samples.get(key) or legacy_samples[key]
        provenance = "measured" if key in exact_samples else "estimated"
        med = statistics.median(vals)
        spread = (max(vals) - min(vals)) / med * 100 if len(vals) > 1 and med else 0.0
        measured[key] = MeasuredStat(
            round(med, 2), len(vals), round(spread, 1), provenance,
        )

    regime_factor = {r: _geomean(v) for r, v in ratios.items()}
    regime_band_pct = {r: _loo_band(v) for r, v in ratios.items()}
    return Calibration(measured, regime_factor, regime_band_pct, n)


def _loo_band(ratios) -> float:
    """Leave-one-out residual band (68th pct). <3 samples -> conservative default."""
    if len(ratios) < 3:
        return 35.0
    resid = []
    for i in range(len(ratios)):
        others = ratios[:i] + ratios[i + 1:]
        pred_factor = _geomean(others)
        resid.append(abs(ratios[i] - pred_factor) / pred_factor * 100)
    resid.sort()
    return round(resid[int(0.68 * (len(resid) - 1))], 1)


_ESTIMATED_BAND = 50.0


def apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration):
    """Turn a theoretical tok/s estimate into (tok_s, source, band_pct).

    Precedence: exact current-machine (id,quant) > another quant for the
    same model id (a calibrated proxy, never an exact measurement) >
    regime-level calibrated factor > uncalibrated estimated. Legacy rows
    without a machine fingerprint can supply an estimate, but can never be
    labelled measured. A None calibration always falls through to
    "estimated".

    The fallback exists because recommend() scores every quant per model
    but returns only the single best-SCORING one: a small model can have
    F16 win on composite score even though a plain `ollama pull` actually
    installs (and LAC Pro's autopilot benchmarks) Q4_K_M. Without this
    fallback, a real run can still inform another quant's recommendation,
    while its provenance remains explicit.
    """
    if calibration is None:
        return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
    stat = calibration.measured.get((catalog_id, quant_name))
    cross_quant = False
    if stat is None:
        candidates = [(k, v) for k, v in calibration.measured.items() if k[0] == catalog_id]
        if candidates:
            candidates.sort(key=lambda kv: (
                kv[1].provenance != "measured",
                -kv[1].n_runs,
                kv[0][1],
            ))
            stat = candidates[0][1]
            cross_quant = True
    if stat is not None:
        band = stat.spread_pct if stat.n_runs > 1 else 25.0  # single-sample: flagged, not 0
        if stat.provenance != "measured":
            return stat.median_tps, "estimated", max(band, _ESTIMATED_BAND)
        if cross_quant:
            return stat.median_tps, "calibrated", max(band, _ESTIMATED_BAND)
        return stat.median_tps, "measured", band
    factor = calibration.regime_factor.get(regime)
    if factor is not None:
        band = calibration.regime_band_pct.get(regime, 35.0)
        return round(theoretical_tps * factor, 1), "calibrated", band
    return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
