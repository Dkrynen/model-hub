# LAC Pro Autopilot — Design Spec

**Date:** 2026-07-04 · **Status:** Approved · **Relates to:** `2026-07-03-apt-v2-overhaul-design.md`
§2.6 ("Deep-Dive Mode is free tier... Pro stays: tune cockpit, insights, future automation") —
this spec is that "future automation" landing, and makes the free/Pro line concrete for the
first time.

## 1. Why

After the LAC rebrand shipped, Duan looked at the running app and said: "I still do not see
why people would need to pay for the pro version." The current Pro feature set (`lac pro tune`,
`lac pro insights`) is CLI-only, requires the user to remember to run a command, and isn't
surfaced anywhere in the web UI a typical user would actually be looking at. The ask: make Pro
genuinely worth paying for, and make the whole experience as frictionless as Cursor/OpenCode —
no commands to learn, it just works.

## 2. Decisions locked (do not re-litigate)

1. **Both `tune` and `benchmark` become Pro-gated.** The free CLI's `lac benchmark <model>`
   command is removed entirely — benchmarking only happens through the Pro pipeline below (plus
   a manual `lac pro benchmark` for on-demand re-runs, Pro-gated the same as `lac pro tune`).
2. **Autopilot, not a button.** The moment a model finishes installing — through the web UI or
   `lac pull` in the CLI — Pro automatically benchmarks it, sweeps GPU-offload configs, and
   applies the fastest, with zero user action beyond the original install click/command. No
   "Optimize" button, no separate step.
3. **Free users see nothing extra from the backend.** If Pro isn't installed, or isn't
   licensed, the install flow itself is byte-identical to today — no nag, no blocked install,
   no backend awareness of Pro's absence (see §3/§4 — this decision lives in the frontend, not
   core or the hook). A single one-time toast after a free user's first install invites them
   to try Pro; it does not repeat on every install.
4. **First-run-on-Pro explanation.** The very first time a *licensed* Pro user's autopilot
   fires, show a one-time toast explaining what's happening ("optimizing in the background —
   here's what that means"). Every run after that is silent, matching Duan's "as easy as
   Cursor" bar — no repeated interruption once the user knows what it is.
5. **Free/Pro calibration reframe.** The recommendation engine's calibration-source badges
   (measured > calibrated > estimated, from the existing calibration loop) now read: free tier
   tops out at "calibrated"/"estimated" (no path to "measured" without benchmarking); Pro
   reaches "measured" automatically as a side effect of autopilot. Pro's pitch becomes
   concrete: *pay and your recommendations get measured-accurate, and every model you install
   gets auto-tuned to your exact hardware — automatically, forever.*
6. **Optimization is strictly additive.** A sweep/apply failure never blocks or delays the
   install itself — the model is usable the moment the pull finishes regardless of what
   autopilot does afterward. Failures degrade to "left at Ollama's default" silently logged,
   not surfaced as an error toast (this isn't the user's problem to solve).
7. **Pricing/checkout unchanged.** The existing Polar.sh checkout and $3/month-billed-annually
   copy on the landing page stay as they are — this spec changes what Pro *does*, not what it
   *costs*. (The landing page's feature bullets will need a copy pass to describe autopilot
   instead of the old manual `tune`/`insights` framing — that's an implementation task, not a
   pricing change.)

## 3. Architecture

Core (model-hub) gains exactly one new capability in the existing open-core plugin interface
(`backend/plugins.py`, the `lac.plugins` entry-point seam) — an **optional** hook:

```
on_model_installed(model_name: str) -> None
```

Called after any successful model install, from both of the two places installs actually
happen in core: `backend/api.py`'s `ollama_pull()` (web) and `cli.py`'s `cmd_pull()` (CLI).
Core does not know or care what this hook does — the call is wrapped in the same
per-plugin try/except isolation `backend/plugins.py::discover()` already uses, so a Pro
failure (or Pro simply not being installed) can never break a plain install. This preserves
the existing "no Pro in core" hard constraint: core adds one generic extension point, not any
tuning/benchmarking/licensing logic.

`lac-pro` implements the hook:

1. License check (existing `require()`/`check()` contract from `lac_pro/license.py` — no
   changes to the licensing contract itself).
2. Not licensed → do nothing (silent), except the one-time free-tier toast (§2.3) — tracked via
   a simple "have we shown this before" flag in the same on-disk grant/config location the
   license module already uses, so it survives restarts but only fires once per install.
3. Licensed → kick off benchmark → GPU-offload sweep → apply-winning-config, reusing the
   existing `run_sweep()` / apply machinery `lac pro tune` already has (`lac_pro/tune.py`,
   `lac_pro/apply.py`) — no new sweep algorithm, just a new automatic entry point into the
   same one.

## 4. Surfacing progress

**CLI (`lac pull <model>`):** runs the optimize step inline, synchronously, right after the
pull completes — printing progress the same way `lac pro tune` already does today. No new
async machinery needed; a CLI session is already a blocking, watch-it-happen context.

**Web (`/api/ollama/pull`):** the sweep takes a minute or more, so it cannot block the pull's
HTTP response. The hook spawns a background thread; progress/result is written to a small
status file (same on-disk convention as `tune.jsonl`/`results.jsonl` — e.g.
`~/.model-hub/pro_optimize_status.json`, keyed by model name) that a new Pro-mounted route
(`GET /api/pro/optimize-status?model=<name>`, registered via the existing `register_api(app)`
plugin capability) reads and returns. The frontend polls this after `pullWithToast`'s `onDone`
fires, showing a toast that updates from "Optimizing gpt-oss:20b…" to "73 tok/s ✓" — the same
toast/polling shape `installer.ts` already uses for download progress, just a second phase
after the download toast resolves.

**Free-tier upsell decision lives entirely in the frontend.** After an install completes, the
frontend already knows whether Pro is present via the existing `GET /api/plugins` listing (the
same data `lac plugins` renders today) — no new backend signal needed. If `pro` isn't in that
list, or `/api/pro/optimize-status` isn't reachable (404 — the route only exists when the Pro
plugin is loaded), or a request to it returns `{"state": "not_licensed"}`, the frontend shows
the one-time upsell toast, gated by a `localStorage` flag so it only ever fires once. Core and
the plugin hook itself never make this decision — they don't need to know Pro's marketing
exists at all.

## 5. Free/Pro boundary changes (concrete)

- `cli.py::cmd_benchmark` and its `benchmark` subparser registration are removed from core.
  `tests/test_benchmark.py`-equivalent coverage moves to `lac-pro` as tests of
  `lac pro benchmark`.
- The calibration loop's "measured" tier (currently populated by `lac benchmark`'s output into
  `~/.model-hub/benchmarks/results.jsonl`) is now populated exclusively by the Pro autopilot's
  benchmark step — same file, same format, just a different (Pro-gated) producer. No changes
  to the calibration *scoring* logic itself, only to what can feed it.
- Landing page (`site/index.html`) Pro feature-bullet copy gets rewritten to describe autopilot
  (implementation task, not a design decision — exact copy is Duan's call at review time, not
  locked here).

## 6. Error handling

- Sweep/apply failure → logged, model stays at Ollama's default config, no user-facing error
  (per §2.6 — this is not a problem the user needs to solve).
  Web polling route returns `{"state": "failed_silent"}` in this case; frontend simply doesn't
  show a completion toast rather than showing a scary error.
- Ollama unreachable mid-sweep → same silent-degrade path.
- Concurrent installs (user installs two models in quick succession) → each gets its own
  autopilot run; no shared mutable state beyond the per-model status file, so no locking
  needed beyond what already exists for `tune.jsonl` appends.
- Plugin hook itself raising → caught by the existing per-plugin isolation in
  `backend/plugins.py`, logged, install unaffected (this is the exact case that isolation was
  already built for).

## 7. Testing approach

- Core: a test that a plugin's `on_model_installed` is called after both `cmd_pull` and
  `ollama_pull()` succeed, and a test that a raising/missing hook never breaks the install
  (mirroring the existing plugin-discovery isolation tests).
- `lac-pro`: unit tests for the hook's license-gate branch (unlicensed → no-op + one-time
  toast flag; licensed → sweep+apply invoked), and for the new `optimize-status` route's state
  transitions (idle → running → done/failed_silent).
- No live-Ollama test changes required beyond what `tune`'s existing tests already cover — the
  autopilot hook calls the same `run_sweep`/apply functions already under test.

## 8. Out of scope

- Changing the Polar.sh checkout, pricing, or licensing contract itself (§2.7).
- Any change to the sweep algorithm, scoring, or split-plan logic — this wires an *automatic
  trigger* onto existing, already-shipped tuning logic, it does not change what tuning does.
- W1 (Deep-Dive Mode), W2 (Surfaces/TUI), W5 (Hardware identity) — unaffected, separate
  workstreams.
- A full task-queue/job system — a background thread + status file is enough for this scope;
  revisit only if autopilot needs to survive a server restart mid-sweep (not a current
  requirement).

## Changelog

- 2026-07-04: Initial spec. Approved by Duan after two rounds of discussion (value-gap
  diagnosis, then approach selection: full autopilot over a manual "Optimize" button).
