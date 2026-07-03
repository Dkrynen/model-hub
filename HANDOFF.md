# Apt — Session Handoff Prompt

Copy-paste the block below into a new session to continue exactly where we left off.

---

## Context

I'm working on **Apt** — a local LLM manager (hardware scan → model recommendation → install → chat) built on Python/Flask + React/Vite/Tailwind/shadcn frontend. The repo is at `C:\Users\User\repos\model-hub`.

**My hardware:** AMD Ryzen 5 7600 (6 cores), 30.9 GB RAM, AMD Radeon RX 6800 XT (16 GB VRAM, ROCm), AMD Radeon integrated graphics (10.5 GB, ROCm). Total discrete VRAM 16 GB, combined GPU VRAM 26.5 GB.

**Ollama:** v0.30.11 running at `http://localhost:11434`. 3 models installed: gemma3:12b, llama3.2:3b, phi4:14b.

**Test status:** 146 passing, 0 failing. Run with `.venv\Scripts\python.exe -m pytest -q`.

## What's done

### A — Catalog (expanded + corrected)
- 67 → 91 entries, verified against live Ollama library (235 families checked).
- Fixed 14 bogus entries (qwen3:7b, gemma3:2b, mellum2, etc.), fixed MoE flags on qwen3 small dense models, corrected deepseek-coder-v2 to MoE, qwen3:235b active 16→22B.
- Added qwen3.5, qwen2.5 non-coder, gpt-oss, mixtral 8x22b, dbrx, smollm2, falcon3, mistral-small3.2, deepseek-v3, phi4-mini, gemma3:27b, BitNet 1.58-bit (hf.co), OLMoE (hf.co).
- vram formula changed from weights+full-context KV to weights-only (file-size accurate). `vram_q4` = file size (weights + ~0.3 GB overhead), not bloated by KV cache.
- `qwen3:30b-a3b Q3_K_M` not on Ollama library — only Q4_K_M, Q8_0, FP16 available. Custom GGUF needed for Q3.

### F — Scoring engine rebalance
- 14 new tests (first ever for recommend.py).
- `fit_score` fixed: rewards VRAM utilization (50 at 6% → 100 at 75%+), flat 100 in 75-100% band.
- Context saturates at 32k (was 65k).
- MoE speed correctly uses `active_params_b`.
- Headline bug fixed: 30B-A3B Q3_K_M 15.69 GB now ranks #2 on 16GB system (was losing to 1B toys).

### B — Multi-GPU hand-off tiering
- `ComputeTier`/`TierAllocation`/`SplitPlan` dataclasses added.
- `hardware.py`: `build_compute_tiers()` classifies GPUs as discrete/integrated, builds ordered tier list (dGPU → iGPU → RAM), sets `combined_vram_gb`.
- `recommend.py`: `_compute_split_plan()` computes per-model split plans (greedy fill: dGPU 16 GB → iGPU 9.45 GB usable → RAM 15.45 GB usable).
- `_estimate_speed()` uses split-aware weighted bandwidth.
- `fit_score` uses model weights (`params_b * bpp`), not KV-bloated total — prevents 131k ctx from faking "good utilization".
- API (`/api/scan`, `/api/recommend`) exposes `compute_tiers`, `combined_vram_gb`, `split_plan`.
- `types.ts` updated with `ComputeTier`, `TierAllocation`, `SplitPlan`.
- CLI scan shows tier breakdown; CLI recommend shows split plan summaries.
- 25 recommend tests, 146 total.

### D2 — Benchmark CLI command
- `apt benchmark <model>` — runs model through Ollama `/api/generate` with deterministic prompt (temp 0, num_predict 128, stream=false).
- Outputs: eval_count, eval_duration_ms, tokens/second, time-to-first-token.
- Logs results to `~/.model-hub/benchmarks/results.jsonl`.
- Supports `--list` (show history), `--export CSV|JSON|JSONL`, `--prompt`, `--num-predict`, `--temperature`, `--no-cache`.
- 5 tests for benchmark logging + CLI help.

## Current state (2026-07-03)

**Everything below D2 is DONE and superseded by the v1 public-launch effort.**
Spec: `docs/superpowers/specs/2026-07-02-apt-v1-public-launch-design.md` (open-core,
cheap-subscription Pro via LemonSqueezy, Windows-first launch with teased macOS/Linux).

- **Calibration loop** — DONE + merged (measured > calibrated > estimated; per-machine
  fingerprint; `apt benchmark` feeds it).
- **Web technical controls** — DONE + merged to master (`ffca692`): calibration source
  badges, expandable split-plan rows, per-GPU what-if toggles + RAM-spill switch,
  browser benchmark launcher streaming `/api/benchmark`.
- **Open-core plugin seam** — DONE + merged to master (`47dde65`): `apt.plugins`
  entry-point discovery (`backend/plugins.py`), CLI + Flask mounting, `apt plugins`,
  `GET /api/plugins`. See `docs/PLUGINS.md`. (TUI agent-tools moved to `apt.tools`.)
- **apt-pro** — private repo `C:\Users\User\repos\apt-pro` (NEVER gets a public
  remote): license-gate stub (`APT_PRO_DEV=1` / `~/.model-hub/license.json`,
  `require()` exits 3), `apt pro status`, `apt pro tune <model> [--repeat N] [--apply]`
  (offload sweep -> tune.jsonl, winner baked into `<model>-tuned` via /api/create).

### Remaining
- Live smoke of `apt pro tune llama3.2:3b --repeat 1` with Ollama up (mocked suite green;
  license gate + CLI discovery proven live).
- **Plan 2** — LemonSqueezy licensing (replace license.py internals; `apt pro activate`),
  calibration insights. **Plan 3** — release engineering (CI matrix, installers, rebrand,
  secrets sweep, landing page + waitlist).

### C — Partial conditional activation (not started)
MoE expert pruning, early exit, speculative decoding, dynamic quant — all still research. No code written.

## Data locations

| What | Path |
|---|---|
| Model catalog | `backend/cookbook/data/models.json` (91 entries) |
| Ollama library cache | `backend/cookbook/data/library_cache.json` |
| Benchmark results | `~/.model-hub/benchmarks/results.jsonl` |
| Download history | `~/.model-hub/downloads/history.jsonl` |
| Chat sessions | `~/.model-hub/cookbook.db` (SQLite) |
| App config | `~/.model-hub/config.json` |

## Key files

| Purpose | Path |
|---|---|
| CLI entry point | `cli.py` |
| Scoring engine | `backend/cookbook/recommend.py` |
| Hardware scanner | `backend/cookbook/hardware.py` |
| Catalog generator | `backend/cookbook/generate_models.py` |
| Model catalog (JSON) | `backend/cookbook/data/models.json` |
| Flask API | `backend/api.py` |
| Web frontend | `web/src/` (React + Tailwind) |
| TUI | `backend/tui/app.py` |
| Tests (recommend) | `tests/test_recommend.py` (25 tests) |
| Tests (benchmark) | `tests/test_benchmark.py` (5 tests) |

## Commands

```powershell
cd C:\Users\User\repos\model-hub
.venv\Scripts\python.exe server.py              # Flask on :5050
cd web; npm run dev                              # Vite dev (proxies /api)
cd web; npm run build                            # production build → web/dist
.venv\Scripts\python.exe cli.py scan             # hardware scan
.venv\Scripts\python.exe cli.py rec              # recommendations
.venv\Scripts\python.exe cli.py benchmark <model> # benchmark a model
.venv\Scripts\python.exe cli.py benchmark --list  # show benchmark history
.venv\Scripts\python.exe cli.py tui              # TUI chat
.venv\Scripts\python.exe -m pytest -q            # tests (146 pass)
```

## Next steps (after user pulls models)

1. Run benchmarks (commands above) to get real tok/s for qwen3:30b-a3b at Q4_K_M, Q8_0, FP16 and Falcon3 1.58-bit vs Q4.
2. Compare real-vs-estimated size/speed to calibrate `_estimate_speed()` in `backend/cookbook/recommend.py`.
3. Use benchmark data to verify/improve the scoring engine's speed predictions.
4. Optionally build custom GGUF for qwen3:30b-a3b Q3_K_M if user wants it.
