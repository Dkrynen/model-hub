# Self-Calibration Loop — Design Spec

**Date:** 2026-07-02
**Component:** `backend/cookbook/` (recommender/estimator) + `cli.py` (benchmark)
**Status:** Design approved (pending written-spec review) → implementation plan next

---

## 1. Problem & Goal

`recommend()` / `_estimate_speed()` predict tok/s purely from a theoretical
memory-bandwidth model plus hardcoded constants. This session proved that
model can be **~9× wrong** (qwen3:30b-a3b Q4: predicted 201 tok/s, measured
22.9) and the error is regime-specific. The tool already captures ground truth
(`apt benchmark` → `~/.model-hub/benchmarks/results.jsonl`) but **never reads it
back into predictions.**

**Goal:** close the loop. Predictions reflect the user's *real* measured tok/s
where they've benchmarked, and self-correct per-machine (and per-software-stack)
from their benchmark history everywhere else. The static model becomes a *prior*
that reality overrides — never a confident final answer.

### Why this is the right (and only) approach — research-validated 2026-07-02

Three research threads (full findings in §9) converged on one conclusion:
**a reliable static tok/s predictor does not exist and cannot.** Achieved
throughput on identical hardware varies 20–56% by framework/version (the box's
own qwen3 number is ~40% suppressed by Ollama 0.31.1's stale vendored
llama.cpp). Ollama's *own* experimental recommender predicts VRAM-fit and
hardware-tier buckets only — it deliberately does **not** predict tok/s.
Therefore per-machine + per-software-stack calibration against real
measurements is the only path to trustworthy speed numbers. That is exactly
this feature.

---

## 2. Scope

### In (V1)
- **Machine+stack fingerprint** stamped on every new benchmark entry.
- **Read `results.jsonl`** and use it to (a) override with exact measured tok/s
  and (b) derive per-regime correction factors.
- **Regime-aware, tier-type-aware** correction (GPU-resident vs unified-iGPU
  spill vs PCIe/CPU spill).
- **Confidence tagging** on every recommendation: `measured` / `calibrated` /
  `estimated`, plus a `±X%` band derived from calibration residuals.
- **Self-validation** via leave-one-out cross-validation, surfaced to the user.
- **Prior fixes** from research: fix the wrong-signed MoE bonus; make spill
  efficiency tier-type-aware.
- **Multi-sample benchmarking** (`apt benchmark --repeat N`) storing per-run
  results + median, to reduce single-sample noise.

### Out (future — YAGNI)
- Regression / parameter-fit correction (needs 10–20 hardware×model combos).
- Shared/crowdsourced benchmark database.
- Automated comparison against *external* published benchmarks (the fingerprint
  + a documented caveat cover the need for now).
- Batch>1 / serving-throughput modelling (decode-at-batch-1 only).
- Auto-benchmark on first run; multi-machine profile UI.

---

## 3. Architecture

New isolated, Ollama-free, unit-testable module: **`backend/cookbook/calibration.py`**.
It is pure data-in/data-out so it can be tested against a synthetic
`results.jsonl` + `SystemInfo` with no live server.

Units:

| Unit | Signature (approx) | Responsibility |
|---|---|---|
| `parse_model_tag` | `(tag) -> (catalog_id, quant_name) \| None` | Map an ollama tag (`qwen3:30b-a3b-q8_0`, bare `=Q4_K_M`, `hf.co/…` sub-4bit) to a catalog entry + quant; `None` if not in catalog. |
| `machine_fingerprint` | `(SystemInfo, stack_info) -> str` | Short stable hash of hardware **and** software stack (see §6). |
| `Calibration` (dataclass) | — | Holds: `measured[(id,quant)] -> MeasuredStat`, `regime_factor[regime] -> float`, `regime_residual_pct[regime] -> float`, `n_samples`. |
| `load_calibration` | `(SystemInfo, stack_info, results_path) -> Calibration` | Read jsonl, filter to matching fingerprint, parse tags, compute measured stats + per-regime factors + LOO residuals. |
| `apply_calibration` | `(estimate, model, quant, split, calibration) -> (tok_s, SpeedSource, band_pct)` | The precedence logic (§5). |

`recommend()` / `_estimate_speed()` call `apply_calibration`; `print_recommendations`
renders the source + band. `cli.py` gains fingerprint stamping + `--repeat`.

---

## 4. Correction model

Regimes (from the split plan's `run_mode` + tier types):
- **`gpu`** — fits one fast discrete tier. Bandwidth-bound; efficiency prior η≈0.55–0.70.
- **`igpu_spill`** — spills onto a *unified-memory* integrated GPU (shares RAM, no PCIe). Mildly penalised; prior η≈0.6 (validated: qwen3 Q4 on this box).
- **`pcie_spill`** — spills from a discrete GPU onto CPU/RAM over PCIe. Latency-catastrophic; prior η≈0.10–0.20.

(Detection: existing `hardware.py` tier `kind` already distinguishes
`discrete`/`integrated`/`ram`; `igpu_spill` = spill whose slowest active tier is
`integrated`, `pcie_spill` = slowest active tier is `ram` behind a discrete GPU.)

**Per-regime correction factor:** `factor_R = geomean(real_tps / theoretical_tps)`
over matching benchmarks in regime `R`. Applied: `corrected = theoretical × factor_R`.
No data in `R` → `factor_R = 1.0` (falls back to the prior, tagged `estimated`).

**Prior fixes** (so the un-calibrated default is closer to reality):
- MoE: replace `moe_bonus = 1.2` with a slight penalty `moe_penalty ≈ 0.9`
  (irregular per-token expert I/O; research §9.1). Keep `active_params` for the
  bytes-per-token term.
- Spill: `SPILL_EFFICIENCY` becomes tier-type-aware (`igpu_spill` vs `pcie_spill`
  priors above) instead of one 0.65 constant.

Note: these priors matter mainly before the user has benchmarked a regime; once
they have, `factor_R` corrects whatever the prior got wrong (including the ~40%
Ollama-stack suppression, which is captured per-fingerprint).

---

## 5. Precedence & confidence

For each recommendation's speed:
1. **Exact measured** — a benchmark exists for this `(catalog_id, quant)` on the
   current fingerprint → use its **median** measured tok/s. `source=measured`,
   band = observed inter-run spread; a single-sample entry carries a flagged,
   modest default band (it is real but unreplicated, so not zero-uncertainty).
2. **Calibrated** — no exact match but ≥1 benchmark in the same regime on this
   fingerprint → `theoretical × factor_R`. `source=calibrated`,
   band = `±regime_residual_pct` (from LOO-CV, §7).
3. **Estimated** — no data for this regime → theoretical prior untouched.
   `source=estimated`, band = a wide default (e.g. ±50%) with a "not yet
   calibrated" note.

`Recommendation` gains: `speed_source: str`, `speed_band_pct: float`.
`print_recommendations` shows a marker (e.g. `✓measured` / `~calibrated ±18%` /
`est ±50%`) so a guess never reads like a fact.

---

## 6. Fingerprint (§ research thread #2)

`machine_fingerprint` hashes a canonical string of:
- Sorted GPU descriptors: `name|backend|vram_gb` for each tier.
- Total RAM (bucketed, e.g. nearest GB).
- **Software stack:** Ollama version (`GET /api/version`) + backend
  (`vulkan`/`rocm`/`cuda`/`metal`, from the server log/config or capability probe).

Stamped into each new benchmark entry as `fingerprint` (+ raw `stack` fields for
debuggability). **Rationale:** identical hardware swings ~40% across Ollama
versions/backends, so a benchmark taken today must not silently calibrate
predictions after a backend update or on a different machine.

**Legacy entries** (no `fingerprint`): treated as belonging to the current
machine **only for the exact-measured-override path** (best-effort, logged), and
**excluded from the fitted `factor_R`** (unknown provenance must not contaminate
the fit). The 3 benchmarks already in `results.jsonl` fall here until re-run.

---

## 7. Self-validation (leave-one-out CV)

When a regime has ≥3 matching benchmarks: for each benchmark, recompute
`factor_R` from the *others*, predict the held-out point, record
`abs(pred-real)/real`. `regime_residual_pct` = the (e.g.) 68th-percentile of
those residuals → the `±X%` band for `calibrated` predictions. With <3 samples,
use a conservative default band and tag confidence as "low (n=k)".

This makes the tool self-aware: it reports how well its own corrections
reproduce measured reality, rather than asserting a bare number.

---

## 8. Data flow

```
detect() ──► SystemInfo
              │
Ollama /api/version + backend ──► stack_info
              │
              ├──► machine_fingerprint(SystemInfo, stack_info)
              │
results.jsonl ─┴─► load_calibration(…)  ──►  Calibration
                                              │
recommend() ─► per (model,quant): _estimate_speed (theoretical)
                     │
                     └─► apply_calibration(estimate, …, Calibration)
                             └─► (tok_s, source, band)  ──► print_recommendations
```

`apt benchmark` (write path): run(s) → `_benchmark_metrics` → stamp
`fingerprint` + `stack` → append to `results.jsonl`.

---

## 9. Research findings that shaped this (2026-07-02, cited)

### 9.1 Decode physics / model form
- Decode is memory-bandwidth-bound; `tok/s ≈ bandwidth / bytes_per_token`, real
  efficiency ~60–82% (~70% typical).
- Spilled collapse = **slowest-tier + sequential-pipeline, latency-dominated**
  (a cited Mixtral offload case: ~2400× latency overhead vs 16× bandwidth
  ratio). Validates the separate spilled regime and its low η.
- MoE decode is governed by **active params**, but takes a **slight penalty**
  (~0.85–0.95) from irregular per-token expert I/O — *not* a bonus. Unified-iGPU
  spill is far less penalised than PCIe/CPU offload.
- Quantization scales ~inversely with bytes/weight × κ(0.85–0.95).
- Sources: llama.cpp/ggml discussions, EleutherAI transformer-math, Kipply
  inference-arithmetic, MoE-offloading papers (SpecOffload, NEO).

### 9.2 RX 6800 XT cross-verification
- Vulkan on the 6800 XT is competitive with ROCm (not slower).
- The "AMD driver too old" warning is a **documented false alarm** (ROCm-path
  artifact on consumer cards) — **not** a throughput suppressor.
- Real suppressor: Ollama 0.31.1 vendors a stale llama.cpp missing Wave32
  flash-attention → **~40% gap** on AMD Vulkan. So the box's qwen3 Q4 = 22.9 is
  ~40% below an updated backend (~32–42). Falcon3:3b 178 is solid; Q8 correctly
  RAM-bound.
- Implication → software-stack fingerprint (§6); document the suppression as a
  known caveat; do **not** chase the driver warning.
- Sources: llama.cpp GPU benchmark scoreboard (2026-04), Ollama issues
  #15601/#16677, Qwen speed benchmarks.

### 9.3 Prior art / methodology
- VRAM = weights (params×bytes) + KV (2·layers·kv_heads·head_dim·tokens·bytes) +
  ~1.2× overhead (HF estimator, ±5%). Context length dominates KV.
- Ollama's experimental recommender = 4-factor score (VRAM 40 / RAM 25 / disk 15
  / speed-class 20); **does not predict tok/s**.
- Calibration: multiplicative correction factor (LOO-CV + bootstrap residuals
  for ±σ) for small data; regression (log-linear, k-fold, R²) once ≥10–20 combos.
- Takeaways: VRAM-fit is reliable gating; tok/s is the uncertain part → be honest
  with confidence; don't collapse quants; context length is a VRAM foot-gun.
- Sources: HF accelerate estimator, EleutherAI, Kipply, Ollama issue #14771.

---

## 10. Testing (TDD)

Unit (no Ollama; synthetic `results.jsonl` + `SystemInfo`):
- `parse_model_tag`: base=Q4_K_M, `-q8_0`/`-fp16` suffixes, `hf.co/…` sub-4bit,
  unmappable tag → `None`.
- `machine_fingerprint`: stable for same hw+stack; differs when GPU set, RAM
  bucket, Ollama version, or backend changes.
- `load_calibration`: correct measured-override + per-regime factor from a
  synthetic set; empty data → factors=1.0, all `estimated`; foreign-fingerprint
  entries excluded from the fit; legacy (no-fingerprint) entries feed
  exact-override only.
- `apply_calibration` precedence: measured > calibrated > estimated; correct
  source tag + band each case.
- LOO-CV residual computation on a known synthetic set.
- Prior fixes: MoE GPU-resident now ≤ dense equivalent (penalty, not bonus);
  `pcie_spill` prior < `igpu_spill` prior < `gpu`.

Integration / regression:
- **The real 3 benchmarks drive corrected speed back to ≈ measured** — the loop
  reproduces this session's manual calibration automatically.
- `recommend()` surfaces `measured` for a benchmarked config, `calibrated` for
  same-regime, `estimated` otherwise.

Full suite must stay green (currently 150/150).

---

## 11. Risks / open questions
- **Regime granularity vs data:** more regimes (gpu/igpu_spill/pcie_spill) means
  each needs its own benchmarks to calibrate. Acceptable — un-benchmarked regimes
  fall back to (research-improved) priors, honestly tagged `estimated`.
- **Backend detection reliability:** reading Ollama version is trivial
  (`/api/version`); backend (vulkan/rocm) may require parsing the server log or a
  capability probe. If backend can't be determined, include only version in the
  fingerprint and note it.
- **Single-sample legacy data:** the 3 existing benchmarks are single-sample and
  un-fingerprinted; they seed exact-override but not the fit until re-run with
  `--repeat`.
