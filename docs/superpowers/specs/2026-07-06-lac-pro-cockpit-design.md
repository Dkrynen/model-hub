# Design — LAC Pro Cockpit: Felt Value (S3)

**Created:** 2026-07-06 · **Status:** approved for planning · **Repos:** `model-hub` (open core) + `lac-pro` (private, no remote)
**Predecessor context:** `docs/superpowers/HANDOFF-lac-desktop-shell-and-pro-expansion.md`

> Slice **3 of 4**. Sequence: S1 shell ✅ → S2 Pro self-serve ✅ → **S3 Pro cockpit (felt value)** → S4 chat latency.
> S3 makes Pro *felt*: it surfaces the Pro capabilities that already exist but are buried in the CLI, as a
> premium **`/pro` cockpit** — with model-tuning-as-craft (before→after tok/s proof) as the hero.
> The thesis: the jump to Pro should feel like a different class of tool in the first 60 seconds and every
> session after. **This slice builds UI + thin API routes only — it adds no new Pro algorithms.**

## 1. Problem

Every paid capability is real, tested, and shipping — but invisible:
- **Model tuning** (`lac_pro/tune.py::run_sweep`): sweeps GPU-offload configs, benchmarks each on the user's
  hardware, picks a winner, applies a `-tuned` Ollama variant. **Zero web surface.** This is the moat
  ("I could code it myself — but this is better").
- **Calibration insights** (`lac_pro/insights.py::analyze`): per-model measured tok/s history + regression
  detection. **Zero web, zero API.**
- **Benchmark** (`lac_pro/benchmark_cli.py` + `backend/cookbook/benchmark.py`): on-demand tok/s + history.
  **Zero web.**
- **Autopilot** (`lac_pro/autopilot.py`): auto benchmark→sweep→apply on install. Has a status route +
  transient toast, but no persistent view of what it measured/applied.
- **Custom HF import** (`lac_pro/hf_import.py`): a basic card + toast in Browse; no quant picker, no history.

And S2's activation celebration literally lists "Model tuning cockpit" and "Calibration insights" as
unlocked features — UI that does not exist yet. S3 builds exactly that page.

## 2. Architecture

A new **`/pro` cockpit page** in the web UI (layout: a **tuning hero** at top, a **2×2 grid** of the other
four panels below) plus a **"Pro" sidebar entry** and a Pro-status header.

- **`lac-pro`** adds license-gated `/api/pro/*` routes to `ProPlugin.register_api`, each a thin wrapper over
  an existing pure function (`run_sweep`, `apply_config`, `analyze`, `run_benchmark`/`history`, autopilot
  status reads, import status reads). Gate with `lac_pro.license.check()` → `{"state":"not_licensed"}` when
  unlicensed, exactly like the existing `/api/pro/import-*` routes.
- **Long-running jobs** (sweep, benchmark) reuse the proven autopilot/import pattern: a `POST` kicks off a
  **daemon thread** and returns `{"accepted": true}`; the work writes a **status file** under
  `~/.model-hub/`; the frontend **polls** a `GET …-status` route. Never raises.
- **`model-hub`** adds only the `/pro` page, the nav entry, and polling client methods. It does **not** import
  `lac_pro` (the S2 boundary guard still holds).

## 3. Goal & non-goals

**Goal:** A licensed user opens `/pro` and can tune a model with visible before→after proof, see their
measured-speed history + regressions, re-benchmark on demand, see what Autopilot did, and import a custom
model with a quant picker + history — all felt as a coherent premium surface.

**Non-goals:**
- No new Pro *algorithms* or changes to tuning/benchmark/insights logic (surface only).
- No out-of-process Pro host (S2's parked Option B).
- No chat latency work (S4).
- Not removing the existing lightweight Browse import card (kept as a shortcut).

## 4. Design — the five panels

### Panel 1 — Tune (hero) — the approved design

Select an installed model → **Run sweep** → live progress → result:
- A **before→after** payoff: `baseline → winner tok/s (+N%)`, winner labelled (e.g. "all-33 · full GPU offload").
- A **config table**: one row per swept config (plain-English label, a bar, median tok/s, per-row **Apply**),
  the **winner row expanded** by default showing developer detail (`num_gpu`, `num_ctx`, `VRAM≈`, per-run
  tok/s, spread %); other rows expand on demand.
- A primary **Apply winner → `<model>-tuned`** action.

**Routes (`lac-pro`):**
- `POST /api/pro/tune {model}` → license-gate; spawn a daemon thread running `run_sweep`; write
  `~/.model-hub/pro_tune_status.json` (keyed by model); return `{"accepted": true}` | `{"state":"not_licensed"}`
  | `400` (missing/blank model).
- `GET /api/pro/tune-status?model=` → `{"state": "idle"|"running"|"done"|"failed", ...}`. `done` carries
  `{"layers": int, "results": [{"label","num_gpu","num_ctx","median_tps","runs":[...]}], "winner": {…},
  "baseline_tps": float|null}`. **baseline_tps** = the model's most recent measured tok/s from
  `results.jsonl` (via `benchmark.history()`), or `null` if never measured (UI then shows "winner" without a
  delta). `failed` → `{"state":"failed","message":str}`. Unlicensed → `{"state":"not_licensed"}` wins over any
  stale file (mirror `optimize_status`).
- `POST /api/pro/tune-apply {model, num_gpu, num_ctx}` → license-gate; `apply_config(model, num_gpu, num_ctx)`;
  return `{"state":"applied","tuned_model":str}` | `{"state":"failed","message":str}` | `not_licensed` | `400`.

### Panel 2 — Insights

A table of per-model speed trend: `model · baseline_tps · recent_tps · Δ% · [regression]`. Regressions
flagged. Empty state when <4 samples for every model ("benchmark a few models to build history").

**Route:** `GET /api/pro/insights?threshold=` → license-gate; `analyze(history(), threshold=…)` →
`{"state":"ok","rows":[{model,runs,baseline_tps,recent_tps,delta_pct,regression}]}` | `not_licensed`.
`threshold` defaults to `0.15`; parse defensively (bad value → default).

### Panel 3 — Benchmark

Pick an installed model → **Benchmark now** → shows latest `tok/s` + `time-to-first-token` + a short recent
history for that model.

**Routes:** `POST /api/pro/benchmark {model}` → license-gate; daemon thread `run_benchmark(model)`; return
`{"accepted":true}` | `not_licensed` | `400`. `GET /api/pro/benchmark-history?model=` → license-gate; filter
`history()` to the model, newest first, cap ~20 → `{"state":"ok","runs":[{tokens_per_second,
time_to_first_token_ms,timestamp}]}` | `not_licensed`. (Live-progress uses the same `results.jsonl` tail; the
panel re-fetches history after kicking off a run.)

### Panel 4 — Autopilot

The persistent view of what Autopilot did per install: `model · state · tok/s · when`.

**Route:** `GET /api/pro/autopilot-log` → license-gate; read all entries of `pro_optimize_status.json` →
`{"state":"ok","entries":[{model,state,tokens_per_second?,updated_at}]}` | `not_licensed`.

### Panel 5 — Import (elevated)

Repo-id input + a **quant picker** (the `/api/pro/import-model` body already accepts `quant`) + Import → live
progress (reuse the existing status states) → done. Plus an **import history** list.

**Routes:** existing `POST /api/pro/import-model {repo_id, quant}` + `GET /api/pro/import-status?repo_id=`
(unchanged). Add `GET /api/pro/import-history` → license-gate; all entries of `pro_import_status.json` →
`{"state":"ok","entries":[{repo_id,state,model_name?,quant?,error_type?,message?,updated_at}]}` | `not_licensed`.

### Page shell, nav, and unlicensed state

- **`web/src/pages/pro.tsx`** — the cockpit: a Pro-status header (reuse `GET /api/pro/status` → plan · expires
  · machine) + the hero + the 2×2 grid.
- **Nav:** a new "Pro" entry (Sparkles icon) in `web/src/components/sidebar.tsx`; a `<Route path="/pro">` in
  `web/src/App.tsx`.
- **Unlicensed state:** `/pro` is reachable by anyone. When `proStatus().licensed` is false, the page renders a
  **locked teaser** (what the cockpit does + an **Activate** CTA linking to Settings' `<ProActivation />`).
  The routes independently return `not_licensed`, so nothing leaks.
- **Charts** (tuning bars, insights deltas) are built against the **`dataviz` skill** palette so they read as
  one system in light and dark.

## 5. Data flow (tuning hero, happy path)

```
[/pro] select model → POST /api/pro/tune {model}
   → daemon: run_sweep(...) → write pro_tune_status.json {state:running → done{results,winner,baseline}}
[/pro] poll GET /api/pro/tune-status?model=  → done → render before→after + config table
[/pro] Apply → POST /api/pro/tune-apply {model,num_gpu,num_ctx} → apply_config → {tuned_model}
```

## 6. Error handling

- Every route never raises; honest JSON. Unlicensed → `{"state":"not_licensed"}` (routes) / locked teaser (page).
- Background job crash → status file records `failed` with a message; the panel shows it and offers retry.
- Ollama down / model gone → `run_sweep`/`run_benchmark` surface a `failed` state (not a 500).
- `baseline_tps` null (never benchmarked) → hero shows the winner without a delta, not a broken "+∞%".
- Missing/blank `model`/`repo_id` → `400`.

## 7. Testing

**Automated (`lac-pro` route tests):** each route returns the right shape licensed vs `not_licensed`; the
`POST` job-start routes spawn a thread (mock the pure fn) + a `400` on missing arg; `tune-status`/
`autopilot-log`/`import-history` parse their status files (and handle missing/corrupt → empty/idle);
`insights` maps `analyze` output; `tune-apply` maps `apply_config`. Isolate `GRANT_PATH` + the status-file
paths; mock `run_sweep`/`run_benchmark`/`apply_config`/`analyze`/`history`.

**Frontend:** `npm run typecheck && npm run build` (no web test runner configured). Component logic kept thin
and data-driven so the manual smoke is the behavioral gate.

**Manual smoke (controller/Duan-gated, recorded in the ledger):** on the packaged exe with Pro licensed +
Ollama up + a real model: open `/pro` → Run sweep → before→after renders with real numbers → Apply → `-tuned`
variant created → Benchmark now → insights/autopilot/import-history populate → unlicensed build shows the
locked teaser.

## 8. Risks & mitigations

- **Sweep is slow / heavy** (real benchmarks). Mitigation: background thread + status polling (never blocks the
  UI); a clear running state.
- **New status file (`pro_tune_status.json`)** must be concurrency-safe enough for single-user desktop use —
  mirror autopilot's whole-file read/write (last-writer-wins is acceptable for one user).
- **Boundary drift:** all Pro logic stays in `lac-pro`; `model-hub` only polls. The S2 boundary guard test
  keeps `model-hub` clean.
- **Chart consistency:** use the `dataviz` palette; verify light/dark.

## 9. Definition of done

- `/pro` cockpit reachable from the sidebar; Pro-status header renders.
- Tune hero: sweep → before→after + detail-rich config table → Apply creates a `-tuned` variant (smoke-proven).
- Insights, Benchmark, Autopilot, Import panels each render real data from their routes; Import has a quant
  picker + history.
- Unlicensed users see a locked teaser + Activate CTA; every route returns `not_licensed` when unlicensed.
- Charts read correctly in light + dark.
- `model-hub` still never imports `lac_pro` (guard green); full suites green (`model-hub` + `lac-pro` +
  web typecheck/build); manual smoke recorded.
- Nothing pushed/published without Duan's explicit go; `lac-pro` never gets a remote.
