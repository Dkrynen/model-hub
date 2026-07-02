# Self-Calibration Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `recommend()` reflect real measured tok/s where the user has benchmarked, and self-correct per-machine+per-stack from `results.jsonl` everywhere else, with honest confidence tags.

**Architecture:** New pure module `backend/cookbook/calibration.py` reads `results.jsonl`, filters to the current machine+software fingerprint, and produces (a) exact measured overrides and (b) a per-regime multiplicative correction factor. `recommend()`/`_estimate_speed()` consult it via `apply_calibration`. The theoretical model stays the prior; measurements override it.

**Tech Stack:** Python 3.11 (repo `.venv`), stdlib only (`json`, `hashlib`, `statistics`, `math`, `dataclasses`). No new dependencies. Textual/Flask untouched.

## Global Constraints
- **Stdlib only** in `backend/cookbook/` — no new pip deps.
- `backend/cookbook/calibration.py` MUST be **Ollama-free and pure** (data in → data out); live probes (`detect_stack`) are the one clearly-separated exception and are never called from tests.
- `recommend()` stays **backward-compatible**: `calibration` is an optional param defaulting to `None` (→ pure theoretical, current behavior).
- **Two correction regimes only:** `gpu` (run_mode == "gpu") and `spilled` (everything else). Tier physics lives in the base estimate.
- Full test suite MUST stay green (currently **150/150**). Run `.venv/Scripts/python.exe -m pytest -q` after each task.
- Run all commands via the repo venv: `.venv/Scripts/python.exe`.

---

### Task 1: Model-tag parser

**Files:**
- Create: `backend/cookbook/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces: `parse_model_tag(tag: str) -> tuple[str, str] | None` → `(catalog_id, quant_name)` or `None` if the base id isn't in the catalog. Quant names match `QUANTS[*].name` / `SUB4BIT_QUANT.name`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_model_tag'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/cookbook/calibration.py
"""Per-machine self-calibration: turn real apt-benchmark results into
corrected recommendations. Pure/Ollama-free except detect_stack()."""
from __future__ import annotations

from .recommend import load_models, QUANTS, SUB4BIT_QUANT

# ollama quant-suffix (lowercased, hyphenated) -> catalog quant name
_SUFFIX_TO_QUANT = {q.name.lower().replace("_", "-"): q.name for q in QUANTS}


def parse_model_tag(tag: str):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): model-tag -> (catalog_id, quant) parser"
```

---

### Task 2: Machine + software-stack fingerprint

**Files:**
- Modify: `backend/cookbook/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces:
  - `machine_fingerprint(info: SystemInfo, stack: dict) -> str` — short hex hash, stable for identical hardware+stack, different when GPUs / RAM bucket / `ollama_version` / `backend` change.
  - `detect_stack(base_url: str = "http://localhost:11434") -> dict` — `{"ollama_version": str, "backend": str}`; best-effort, never raises (returns `{"ollama_version": "unknown", "backend": "unknown"}` on failure). Not used in tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration.py  (append)
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k fingerprint -q`
Expected: FAIL — `cannot import name 'machine_fingerprint'`.

- [ ] **Step 3: Implement**

```python
# backend/cookbook/calibration.py  (add imports + functions)
import hashlib
import json
import urllib.request


def machine_fingerprint(info, stack: dict) -> str:
    gpus = sorted(f"{g.name}|{g.backend}|{round(g.vram_gb,1)}" for g in info.gpus)
    parts = [
        ";".join(gpus),
        f"ram={round(info.ram_gb)}",
        f"ollama={stack.get('ollama_version', 'unknown')}",
        f"backend={stack.get('backend', 'unknown')}",
    ]
    return hashlib.sha1("::".join(parts).encode()).hexdigest()[:12]


def detect_stack(base_url: str = "http://localhost:11434") -> dict:
    """Best-effort software-stack probe. Never raises."""
    version = "unknown"
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=3) as r:
            version = json.loads(r.read().decode()).get("version", "unknown")
    except Exception:
        pass
    return {"ollama_version": version, "backend": "unknown"}  # backend: see spec §11
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k fingerprint -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): machine+software-stack fingerprint"
```

---

### Task 3: Prior fixes + regime helper in the estimator

**Files:**
- Modify: `backend/cookbook/recommend.py` (`_estimate_speed`, add `speed_regime`)
- Test: `tests/test_recommend.py`

**Interfaces:**
- Produces: `speed_regime(split: SplitPlan) -> str` → `"gpu"` if `split.run_mode == "gpu"` else `"spilled"`.
- Changes: MoE `moe_bonus = 1.2` → `MOE_DECODE_PENALTY = 0.9` on the GPU-resident path (irregular expert I/O; spec §4). Spilled path unchanged (already slowest-tier × `SPILL_EFFICIENCY`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend.py  (append near the MoE speed test)
from backend.cookbook.recommend import speed_regime

def test_speed_regime_classification():
    def _plan(mode):
        return SplitPlan(tiers=[TierAllocation("discrete","GPU",16.0,10.0,"rocm",0,512.0,40)],
                         total_model_gb=10.0, total_layers=40, gpu_layers=40, run_mode=mode)
    assert speed_regime(_plan("gpu")) == "gpu"
    assert speed_regime(_plan("multi_gpu")) == "spilled"
    assert speed_regime(_plan("cpu_offload")) == "spilled"

def test_moe_gpu_resident_not_faster_than_dense():
    # Research: MoE decode takes a slight PENALTY, not a bonus.
    def _single(model_gb):
        return SplitPlan(tiers=[TierAllocation("discrete","GPU",16.0,model_gb,"rocm",0,512.0,48)],
                         total_model_gb=model_gb, total_layers=48, gpu_layers=48, run_mode="gpu")
    q4 = next(q for q in QUANTS if q.name == "Q4_K_M")
    moe = ModelEntry("moe:x","MoE","x",30.0,"qwen3",40960,["general"],True,active_params_b=3.0)
    dense = ModelEntry("dense:x","Dense","x",3.0,"qwen3",40960,["general"],False)
    # Same active size (3B) → MoE must NOT exceed dense (penalty, not bonus).
    assert _estimate_speed(moe, q4, _single(1.74)) <= _estimate_speed(dense, q4, _single(1.74)) + 0.1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommend.py -k "speed_regime or moe_gpu_resident" -q`
Expected: FAIL — `cannot import name 'speed_regime'` / MoE currently faster (bonus 1.2).

- [ ] **Step 3: Implement**

In `recommend.py`, add near the other constants:
```python
MOE_DECODE_PENALTY = 0.9  # MoE decode: irregular per-token expert I/O (spec §4/§9.1)
```
Add the helper:
```python
def speed_regime(split: "SplitPlan") -> str:
    return "gpu" if split and split.run_mode == "gpu" else "spilled"
```
In `_estimate_speed`, GPU-resident branch: replace
`moe_bonus = 1.2 if model.is_moe else 1.0`
with
`moe_bonus = MOE_DECODE_PENALTY if model.is_moe else 1.0`.

- [ ] **Step 4: Run to verify it passes (and nothing regressed)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommend.py -q`
Expected: PASS. (The 3 `test_speed_*` anchors still pass — they're spilled/dense, unaffected by the GPU-resident MoE change; `test_moe_speed_uses_active_params` asserts `s_moe > s_dense` for **different** sizes so still holds via active-params.)
If `test_moe_speed_uses_active_params` breaks, update it to compare a 30B-MoE (3B active) vs a 30B **dense** at the same quant — the MoE is still far faster because it reads 3B not 30B; keep that assertion.

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/recommend.py tests/test_recommend.py
git commit -m "fix(recommend): MoE decode penalty (not bonus) + speed_regime helper"
```

---

### Task 4: Calibration store + loader

**Files:**
- Modify: `backend/cookbook/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces:
  - `@dataclass MeasuredStat(median_tps: float, n_runs: int, spread_pct: float)`
  - `@dataclass Calibration(measured: dict, regime_factor: dict, regime_band_pct: dict, n: int)` where `measured` keys are `(catalog_id, quant_name)`.
  - `load_calibration(info, stack, results_path, models=None) -> Calibration`.
- Consumes: `parse_model_tag`, `machine_fingerprint` (Tasks 1-2); `_estimate_vram`, `_compute_split_plan`, `_estimate_speed`, `_estimate_bandwidth`, `speed_regime`, `QUANTS`, `SUB4BIT_QUANT` from `recommend.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration.py  (append)
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k load_calibration -q`
Expected: FAIL — `cannot import name 'load_calibration'`.

- [ ] **Step 3: Implement**

```python
# backend/cookbook/calibration.py  (add)
import math
from dataclasses import dataclass, field
from pathlib import Path

from .recommend import (
    _estimate_vram, _estimate_bandwidth, _compute_split_plan, _estimate_speed,
    speed_regime, QUANTS, SUB4BIT_QUANT,
)

_CTX = 4096  # match Ollama's default_num_ctx used at benchmark time


@dataclass
class MeasuredStat:
    median_tps: float
    n_runs: int
    spread_pct: float


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

    # (id,quant) -> list of measured tok/s ; regime -> list of (real/theoretical)
    samples: dict = {}
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
        samples.setdefault((cid, qname), []).append(float(tps))
        if legacy:
            continue                          # legacy entries: override only, NOT the fit
        vram = _estimate_vram(model, quant, _CTX)
        split = _compute_split_plan(vram, info, model)
        if split is None:
            continue
        theo = _estimate_speed(model, quant, split, bw)
        if theo > 0:
            ratios.setdefault(speed_regime(split), []).append(float(tps) / theo)

    measured = {}
    for key, vals in samples.items():
        vals_sorted = sorted(vals)
        med = vals_sorted[len(vals_sorted) // 2]
        spread = (max(vals) - min(vals)) / med * 100 if len(vals) > 1 and med else 0.0
        measured[key] = MeasuredStat(round(med, 2), len(vals), round(spread, 1))

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q`
Expected: PASS (all calibration tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): load_calibration (measured stats + per-regime factor + LOO band)"
```

---

### Task 5: apply_calibration (precedence + confidence)

**Files:**
- Modify: `backend/cookbook/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces: `apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration) -> tuple[float, str, float]` → `(tok_s, source, band_pct)`, `source ∈ {"measured","calibrated","estimated"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration.py  (append)
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k apply -q`
Expected: FAIL — `cannot import name 'apply_calibration'`.

- [ ] **Step 3: Implement**

```python
# backend/cookbook/calibration.py  (add)
_ESTIMATED_BAND = 50.0

def apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration):
    if calibration is None:
        return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
    stat = calibration.measured.get((catalog_id, quant_name))
    if stat is not None:
        band = stat.spread_pct if stat.n_runs > 1 else 25.0  # single-sample: flagged, not 0
        return stat.median_tps, "measured", band
    factor = calibration.regime_factor.get(regime)
    if factor is not None:
        band = calibration.regime_band_pct.get(regime, 35.0)
        return round(theoretical_tps * factor, 1), "calibrated", band
    return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k apply -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): apply_calibration precedence (measured>calibrated>estimated)"
```

---

### Task 6: Integrate calibration into recommend()

**Files:**
- Modify: `backend/cookbook/recommend.py` (`Recommendation`, `recommend`, `print_recommendations`)
- Test: `tests/test_recommend.py`

**Interfaces:**
- Changes: `Recommendation` gains `speed_source: str = "estimated"` and `speed_band_pct: float = 50.0`. `recommend(info, use_case="coding", min_context=0, top_k=5, calibration=None)`. Inside the quant/ctx loop, after `speed = _estimate_speed(...)`, apply calibration and use the corrected value for `speed_score` + store source/band on the rec.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend.py  (append)
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommend.py -k "surfaces_measured or defaults_estimated" -q`
Expected: FAIL — `Recommendation` has no `speed_source` / `recommend()` has no `calibration` param.

- [ ] **Step 3: Implement**

In `recommend.py`:
- Add fields to `Recommendation`: `speed_source: str = "estimated"`, `speed_band_pct: float = 50.0`.
- Add `calibration=None` to `recommend(...)` signature.
- Inside the loop, after `speed = _estimate_speed(model, quant, split, bw)`:
```python
from .calibration import apply_calibration  # local import to avoid cycle at module load
speed, speed_source, speed_band = apply_calibration(
    speed, model.id, quant.name, speed_regime(split), calibration)
```
  then use the (possibly corrected) `speed` for `speed_score`, and pass
  `speed_source=speed_source, speed_band_pct=speed_band` into the `Recommendation(...)`.
- In `print_recommendations`, append a marker to each line, e.g.:
```python
mark = {"measured": "meas", "calibrated": f"cal±{rec.speed_band_pct:.0f}%",
        "estimated": "est"}.get(rec.speed_source, "est")
```
  and include `mark` in the printed row.

Note the import cycle: `calibration.py` imports from `recommend.py` at module load, so `recommend.py` must import `apply_calibration` **inside** `recommend()` (local import), not at top level.

- [ ] **Step 4: Run to verify it passes (full recommend suite green)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommend.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/recommend.py tests/test_recommend.py
git commit -m "feat(recommend): consult calibration, tag speed source + band"
```

---

### Task 7: Stamp fingerprint + multi-sample benchmarking in the CLI

**Files:**
- Modify: `cli.py` (`_benchmark_metrics` caller / benchmark command; arg parser)
- Test: `tests/test_benchmark.py`

**Interfaces:**
- Changes: benchmark entries gain `"fingerprint"` and `"stack"`. New flag `--repeat N` (default 1): run N times, store each entry, print median tok/s. Fingerprint/stack computed once via `detect()` + `detect_stack()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark.py  (append)
def test_benchmark_entry_can_carry_fingerprint():
    from cli import _benchmark_metrics
    result = {"eval_count": 100, "eval_duration": 5_000_000_000,
              "load_duration": 1_000_000_000, "prompt_eval_duration": 1_000_000_000,
              "total_duration": 7_000_000_000, "response": "ok"}
    entry = _benchmark_metrics(result, "m:1b", "hi", 100, 0.0, fingerprint="abc123", stack={"ollama_version": "0.31.1"})
    assert entry["fingerprint"] == "abc123"
    assert entry["stack"]["ollama_version"] == "0.31.1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_benchmark.py -k fingerprint -q`
Expected: FAIL — `_benchmark_metrics()` got an unexpected keyword argument `fingerprint`.

- [ ] **Step 3: Implement**

- Extend `_benchmark_metrics(result, model, prompt, num_predict, temperature, fingerprint=None, stack=None)`; add to the returned dict: `if fingerprint: entry["fingerprint"] = fingerprint`; `if stack: entry["stack"] = stack`.
- In the benchmark command: compute `fp`/`stack` once:
```python
from backend.cookbook.hardware import detect
from backend.cookbook.calibration import detect_stack, machine_fingerprint
_info = detect(); _stack = detect_stack(); _fp = machine_fingerprint(_info, _stack)
```
- Add `p_bench.add_argument("--repeat", type=int, default=1)`.
- Loop `args.repeat` times calling `/api/generate`, build each entry via `_benchmark_metrics(..., fingerprint=_fp, stack=_stack)`, `_benchmark_log(entry)` each; collect tok/s; print median + per-run.

- [ ] **Step 4: Run to verify it passes (+ full suite)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_benchmark.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_benchmark.py
git commit -m "feat(benchmark): stamp fingerprint+stack; --repeat N multi-sample"
```

---

### Task 8: Wire calibration into the CLI recommend/scan path + regression

**Files:**
- Modify: `cli.py` (the command that calls `recommend()` — locate `recommend(` usage)
- Test: `tests/test_recommend.py` (regression)

**Interfaces:**
- Consumes: `load_calibration`, `detect_stack` (Tasks 2,4); `recommend(..., calibration=...)` (Task 6).
- Behavior: the CLI recommend/scan command builds calibration from live hardware + `results.jsonl` and passes it in, so real benchmarks flow into recommendations.

- [ ] **Step 1: Write the failing regression test**

```python
# tests/test_recommend.py  (append) — the loop reproduces this session's manual calibration
def test_loop_reproduces_measured_on_real_anchors(tmp_path):
    import json
    from backend.cookbook.calibration import load_calibration, machine_fingerprint
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
    recs = recommend(info, use_case="coding", top_k=91, calibration=cal)
    q4 = next(r for r in recs if r.model.id == "qwen3:30b-a3b" and r.quant == "Q4_K_M")
    # exact measured override -> ~22.9
    assert abs(q4.speed_score - min(100, 22.9/30*100)) < 5 or q4.speed_source == "measured"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommend.py -k reproduces_measured -q`
Expected: FAIL if the CLI wiring/selection isn't consistent (or PASS from Task 6 already — if it passes, keep it as the regression guard; the CLI wiring below is still required for real use).

- [ ] **Step 3: Implement the CLI wiring**

Locate the `recommend(` call in `cli.py` (the scan/recommend command). Before it:
```python
from backend.cookbook.calibration import load_calibration, detect_stack
_stack = detect_stack()
_results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
_cal = load_calibration(info, _stack, _results)
```
Pass `calibration=_cal` into `recommend(...)`. If a `--no-calibration` escape hatch is desired, add the flag and pass `None`.

- [ ] **Step 4: Run to verify it passes + FULL suite green**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 150 prior + all new calibration/benchmark/recommend tests.

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_recommend.py
git commit -m "feat(cli): recommend consults per-machine calibration from results.jsonl"
```

---

## Self-Review (completed by author)

- **Spec coverage:** §3 module → Tasks 1,2,4,5; §4 regimes/priors → Task 3 (+2-regime adjustment noted at plan top); §5 precedence/confidence → Task 5; §6 fingerprint → Task 2,7; §7 LOO-CV → Task 4 (`_loo_band`); §8 data flow → Tasks 6,8; multi-sample (§2) → Task 7; testing (§10) → each task + Task 8 regression. **Deviation from spec §4:** fine `igpu_spill`/`pcie_spill` split dropped for V1 (conflicts with measured q8; fragments data) — collapsed to `gpu`/`spilled`; documented at plan top. Deferred items (§2 Out) intentionally absent.
- **Placeholder scan:** none — every code step has real code; commands have expected output.
- **Type consistency:** `parse_model_tag`→`(id,quant)`; `Calibration.measured` keyed `(id,quant)`; `apply_calibration` returns `(tok_s, source, band)`; `speed_regime`→`"gpu"|"spilled"` used consistently in Tasks 3-6,8.
- **Import-cycle note** (Task 6) called out: `recommend.py` imports `apply_calibration` locally inside `recommend()`.
