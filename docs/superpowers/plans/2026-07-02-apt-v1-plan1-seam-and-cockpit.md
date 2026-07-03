# APT v1 — Plan 1: Web Controls Finish + Plugin Seam + Pro Tuning Cockpit

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the in-flight free-tier web technical controls, add the open-core plugin seam to APT core, and ship the first Pro feature set (`apt pro tune` offload auto-tuner + apply) as a separate closed-source `apt-pro` package with a stubbed license gate.

**Architecture:** Core (public, MIT) gains an entry-point plugin discovery module (`backend/plugins.py`) that mounts plugin CLI subcommands and Flask routes. `apt-pro` (private repo, `C:\Users\User\repos\apt-pro`) registers via the `apt.plugins` entry-point group and implements the Tuning Cockpit: layer-count-aware offload config sweeps benchmarked through Ollama, with a `--apply` path that bakes the winner into an Ollama model variant. Licensing is a local-grant stub (`APT_PRO_DEV=1` or `~/.model-hub/license.json`) whose internals Plan 2 replaces with LemonSqueezy — the `check()/require()` interface is the contract.

**Tech Stack:** Python 3.10+ / Flask / argparse / importlib.metadata entry points / pytest · React 18 + TS + Vite + Tailwind + shadcn (Radix) + sonner · Ollama HTTP API.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-02-apt-v1-public-launch-design.md`. Free tier = everything shipped today + the web-technical-controls branch (what-if filters, benchmark launcher). Pro v1 = CLI/API only (Pro dashboard is spec Phase 2).
- **No Pro code in the model-hub repo.** The only Pro-related code in core is the generic plugin seam.
- Python tests: `cd C:\Users\User\repos\model-hub && .venv\Scripts\python.exe -m pytest -q` — suite must stay green after every task.
- Frontend gates: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build` — clean after every frontend task.
- Tune benchmark runs log to `~/.model-hub/benchmarks/tune.jsonl`, **never** `results.jsonl` (non-default offload configs would poison the calibration loop).
- CLI behavior of existing commands must not change (regression gate: `apt recommend`, `apt benchmark`, `apt scan` output shapes unchanged).
- apt-pro repo: `C:\Users\User\repos\apt-pro`, own git, own pytest suite, shares core's venv (editable install). **This repo must never get a public remote.**

---

## Part A — Finish `feat/web-technical-controls` (free tier)

Work on the existing branch `feat/web-technical-controls` in `C:\Users\User\repos\model-hub`.

### Task 1: Verify and commit T4 (GPU-mask / spill params)

The working tree already contains the finished T4 implementation (`backend/api.py` gpu_mask/allow_spill handling) and its two tests (`tests/test_api.py::test_recommend_gpu_mask_reduces_combined_vram`, `::test_recommend_no_spill_zeroes_ram`). This task proves and lands it.

**Files:**
- Modify: none (verification + commit only)
- Test: `tests/test_api.py` (already written, uncommitted)

**Interfaces:**
- Produces: `GET /api/recommend?gpu_mask=0,1&allow_spill=0|1` — masks GPUs by `device_index`; `allow_spill=0` removes the RAM tier and zeroes `ram_gb`. Response fields `combined_vram_gb`/`ram_gb` reflect the effective (post-mask) hardware. Task 3 consumes this.

- [ ] **Step 1: Run the two new tests**

Run: `cd C:\Users\User\repos\model-hub && .venv\Scripts\python.exe -m pytest tests/test_api.py -q -k "gpu_mask or no_spill"`
Expected: 2 passed

- [ ] **Step 2: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass, 0 failed

- [ ] **Step 3: Commit**

```bash
git add backend/api.py tests/test_api.py
git commit -m "feat(api): gpu_mask + allow_spill params on /api/recommend"
```

### Task 2: Split-plan display in the rec table (web)

Expandable row under each recommendation whose `run_mode !== "gpu"`, showing the split summary and per-tier allocation. Additive — no table restructuring.

**Files:**
- Modify: `web/src/pages/scan.tsx`

**Interfaces:**
- Consumes: `Recommendation.split_plan: SplitPlan | null` and `run_mode` (already in `web/src/lib/types.ts:46-70`; already serialized by the API).

- [ ] **Step 1: Add expand state + chevron + split row to scan.tsx**

In `web/src/pages/scan.tsx`:

1. Change line 1 to include React (needed for Fragment):
```tsx
import React, { useState } from "react";
```
2. Extend the lucide import (line 2):
```tsx
import { Cpu, MemoryStick, HardDrive, Microchip, Gauge, ChevronDown, ChevronRight, Layers } from "lucide-react";
```
3. Inside `Scan()`, add expand state next to the other `useState` calls:
```tsx
const [expanded, setExpanded] = useState<string | null>(null);
```
4. Replace the existing `{recs.data.recommendations.map((r) => ( <tr ...>…</tr> ))}` block in the `<tbody>` with:

```tsx
{recs.data.recommendations.map((r) => {
  const key = r.model_id + r.quant;
  const hasSplit = r.split_plan !== null && r.run_mode !== "gpu";
  const isOpen = expanded === key;
  return (
    <React.Fragment key={key}>
      <tr className="transition-colors hover:bg-panel-3/40">
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5">
            {hasSplit ? (
              <button
                aria-label={isOpen ? "Hide split plan" : "Show split plan"}
                onClick={() => setExpanded(isOpen ? null : key)}
                className="text-fg-faint hover:text-fg"
              >
                {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            ) : (
              <span className="w-3.5" />
            )}
            <div className="font-mono text-[13px] font-semibold">{r.name}</div>
          </div>
          <div className="mt-0.5 flex gap-1.5 pl-5">
            <Badge variant="accent">{r.quant}</Badge>
            <Badge variant="neutral">{fmtParams(r.params_b)}</Badge>
            <Badge variant="neutral">{fmtContext(r.context)}k</Badge>
            <SourceBadge source={r.speed_source} band={r.speed_band_pct} />
          </div>
        </td>
        <td className="hidden px-4 py-3 md:table-cell">
          <div className="grid w-[280px] grid-cols-2 gap-x-4 gap-y-1.5">
            <ScoreBar label="Quality" v={r.scores.quality} />
            <ScoreBar label="Speed" v={r.scores.speed} />
            <ScoreBar label="Fit" v={r.scores.fit} />
            <ScoreBar label="Context" v={r.scores.context} />
          </div>
        </td>
        <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
          {r.vram_gb.toFixed(1)} GB
        </td>
        <td className="px-4 py-3 text-right">
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              pullWithToast(r.ollama_cmd?.replace(/^ollama run\s+/, "") || r.model_id)
            }
          >
            Install
          </Button>
        </td>
      </tr>
      {isOpen && r.split_plan && (
        <tr className="bg-panel-2/60">
          <td colSpan={4} className="px-4 py-3">
            <div className="flex items-center gap-2 text-[12px] text-fg-muted">
              <Layers className="h-3.5 w-3.5 text-iris" />
              <span className="font-medium">{r.split_plan.summary}</span>
              <Badge variant="neutral">{r.run_mode}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {r.split_plan.tiers.filter((t) => t.allocated_gb > 0).map((t, i) => (
                <Badge key={i} variant="neutral">
                  {t.name}: {t.allocated_gb.toFixed(1)} GB
                  {t.layers > 0 && ` · ${t.layers} layers`}
                </Badge>
              ))}
            </div>
          </td>
        </tr>
      )}
    </React.Fragment>
  );
})}
```

- [ ] **Step 2: Typecheck + build**

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both exit 0

- [ ] **Step 3: Commit**

Check `git status` first — if `web/dist` is tracked, include it; if gitignored, source only.
```bash
git add web/src/pages/scan.tsx
git commit -m "feat(web): expandable split-plan row per recommendation"
```

### Task 3: GPU-offload controls in the Scan controls card (web)

Per-GPU on/off switches + "Allow RAM spill" switch, wired to `/api/recommend` via the Task-1 params.

**Files:**
- Modify: `web/src/lib/api.ts:76-82` (recommend params), `web/src/pages/scan.tsx` (controls card)

**Interfaces:**
- Consumes: Task 1's query params; `ScanInfo.gpus[].device_index` (types.ts:8); `Switch` from `@/components/ui/switch` (exists).
- Produces: `api.recommend({ gpu_mask?: number[]; allow_spill?: boolean; ... })`.

- [ ] **Step 1: Extend api.recommend**

In `web/src/lib/api.ts`, replace the `recommend:` entry with:

```ts
recommend: (
  params: { vram?: number; use_case?: string; top_k?: number; gpu_mask?: number[]; allow_spill?: boolean } = {}
) => {
  const q = new URLSearchParams();
  if (params.vram) q.set("vram", String(params.vram));
  if (params.use_case) q.set("use_case", params.use_case);
  if (params.top_k) q.set("top_k", String(params.top_k));
  if (params.gpu_mask && params.gpu_mask.length > 0) q.set("gpu_mask", params.gpu_mask.join(","));
  if (params.allow_spill === false) q.set("allow_spill", "0");
  return getJSON<import("./types").RecommendResponse>(`/api/recommend?${q}`);
},
```

- [ ] **Step 2: Add the switches to scan.tsx**

1. Import Switch (with the other ui imports):
```tsx
import { Switch } from "@/components/ui/switch";
```
2. Inside `Scan()`, add state and derived mask:
```tsx
const [disabledGpus, setDisabledGpus] = useState<Set<number>>(new Set());
const [allowSpill, setAllowSpill] = useState(true);

const gpuMask =
  scan.data && disabledGpus.size > 0
    ? scan.data.gpus.map((g) => g.device_index).filter((i) => !disabledGpus.has(i))
    : undefined;
```
Behavior note: disabling ALL GPUs produces an empty mask array → the `length > 0` check in api.ts omits the param → falls back to "all GPUs". That fallback is the requirement (never send an empty mask).
3. Update the recs `useAsync` call to pass the new params and re-fetch on change:
```tsx
const recs = useAsync(
  () =>
    api.recommend({
      use_case: useCase,
      top_k: 12,
      vram: effectiveVram || undefined,
      gpu_mask: gpuMask,
      allow_spill: allowSpill,
    }),
  [useCase, effectiveVram, Array.from(disabledGpus).join(","), allowSpill]
);
```
4. In the Controls `<Card>` (after the VRAM-slider `div`, before the Card closes), append:
```tsx
{scan.data && scan.data.gpus.length > 0 && (
  <div className="flex flex-wrap items-center gap-4">
    {scan.data.gpus.map((g) => (
      <label key={g.device_index} className="flex items-center gap-2 text-[12px] text-fg-muted">
        <Switch
          checked={!disabledGpus.has(g.device_index)}
          onCheckedChange={(on) => {
            setDisabledGpus((prev) => {
              const next = new Set(prev);
              if (on) next.delete(g.device_index);
              else next.add(g.device_index);
              return next;
            });
          }}
        />
        <span>{g.name} · {g.vram_gb} GB</span>
      </label>
    ))}
    <label className="flex items-center gap-2 text-[12px] text-fg-muted">
      <Switch checked={allowSpill} onCheckedChange={setAllowSpill} />
      <span>Allow RAM spill</span>
    </label>
  </div>
)}
```

- [ ] **Step 3: Typecheck + build**

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both exit 0

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.ts web/src/pages/scan.tsx
git commit -m "feat(web): per-GPU toggle + RAM-spill switch driving /api/recommend"
```

### Task 4: Benchmark launcher dialog (web)

Dialog to run `/api/benchmark` from the browser: pick an installed model, watch per-run tok/s stream in, refetch recs on completion so calibration bites immediately.

**Files:**
- Create: `web/src/components/ui/dialog.tsx` (shadcn wrapper — dep `@radix-ui/react-dialog@^1.1.2` already in package.json)
- Create: `web/src/components/benchmark-dialog.tsx`
- Modify: `web/src/lib/api.ts` (add `benchmark` method), `web/src/pages/scan.tsx` (launch button)

**Interfaces:**
- Consumes: `POST /api/benchmark` SSE — frames `{run, tps, eval_count, ttft_ms}` per iteration then `{done: true, median_tps, runs}` (implemented at `backend/api.py:324`); `api.installed()`; `sse()` client (`web/src/lib/api.ts:34`); `cn` from `@/lib/utils`.
- Produces: `api.benchmark(model, opts?, signal?)` async generator; `<BenchmarkDialog onDone={() => void} />`.

- [ ] **Step 1: Create the dialog wrapper**

`web/src/components/ui/dialog.tsx`:
```tsx
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
        "rounded-lg border border-line bg-panel p-5 shadow-xl focus:outline-none",
        className
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-3 top-3 text-fg-faint hover:text-fg">
        <X className="h-4 w-4" />
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = "DialogContent";

const DialogTitle = DialogPrimitive.Title;
const DialogDescription = DialogPrimitive.Description;

export { Dialog, DialogTrigger, DialogClose, DialogContent, DialogTitle, DialogDescription };
```
(Verify the panel/line color tokens against another ui component — e.g. `card.tsx` — and match whatever bg/border classes it uses.)

- [ ] **Step 2: Add api.benchmark**

In `web/src/lib/api.ts`, inside the `api` object (after `chat`):
```ts
/** Stream a benchmark run. Yields {run,tps,...} frames then {done:true,median_tps,runs}. */
benchmark(model: string, opts: { repeat?: number } = {}, signal?: AbortSignal) {
  return sse("/api/benchmark", { model, repeat: opts.repeat ?? 2 }, signal);
},
```

- [ ] **Step 3: Create BenchmarkDialog**

`web/src/components/benchmark-dialog.tsx`:
```tsx
import { useEffect, useRef, useState } from "react";
import { Gauge } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";

interface RunFrame {
  run?: number;
  tps?: number;
  done?: boolean;
  median_tps?: number;
  error?: string;
}

export function BenchmarkDialog({ onDone }: { onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [repeat, setRepeat] = useState(2);
  const [running, setRunning] = useState(false);
  const [runs, setRuns] = useState<number[]>([]);
  const [median, setMedian] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    api
      .installed()
      .then((ms) => {
        const names = ms.map((m) => m.name);
        setModels(names);
        setModel((cur) => cur || names[0] || "");
      })
      .catch(() => setModels([]));
  }, [open]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function start() {
    if (!model || running) return;
    setRunning(true);
    setRuns([]);
    setMedian(null);
    abortRef.current = new AbortController();
    try {
      for await (const frame of api.benchmark(model, { repeat }, abortRef.current.signal) as AsyncGenerator<RunFrame>) {
        if (frame.error) throw new Error(String(frame.error));
        if (frame.done) {
          setMedian(frame.median_tps ?? null);
          toast.success(`${model}: ${(frame.median_tps ?? 0).toFixed(1)} tok/s (median of ${repeat})`);
          onDone?.();
        } else if (typeof frame.tps === "number") {
          setRuns((prev) => [...prev, frame.tps as number]);
        }
      }
    } catch (e) {
      toast.error(`Benchmark failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!running) setOpen(o); }}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Gauge className="mr-1.5 h-3.5 w-3.5" /> Benchmark
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="text-sm font-semibold">Benchmark a model</DialogTitle>
        <p className="mt-1 text-[12px] text-fg-muted">
          Runs a deterministic generation and logs real tok/s — recommendations recalibrate from it.
        </p>
        <div className="mt-4 flex items-end gap-3">
          <div className="flex-1">
            <label className="mb-1.5 block text-[12px] font-medium text-fg-muted">Model</label>
            <Select value={model} onValueChange={setModel} disabled={running}>
              <SelectTrigger className="h-9 w-full"><SelectValue placeholder="Pick installed model" /></SelectTrigger>
              <SelectContent>
                {models.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-[12px] font-medium text-fg-muted">Runs</label>
            <Select value={String(repeat)} onValueChange={(v) => setRepeat(Number(v))} disabled={running}>
              <SelectTrigger className="h-9 w-[70px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[1, 2, 3, 5].map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
        {(running || runs.length > 0) && (
          <div className="mt-4">
            <Progress value={median !== null ? 100 : Math.min(95, (runs.length / repeat) * 100)} variant="iris" className="h-1.5" />
            <div className="mt-2 flex flex-wrap gap-2 font-mono text-[12px] text-fg-muted">
              {runs.map((t, i) => <span key={i}>run {i + 1}: {t.toFixed(1)} tok/s</span>)}
              {median !== null && <span className="font-semibold text-fg">median: {median.toFixed(1)} tok/s</span>}
            </div>
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          {running ? (
            <Button size="sm" variant="secondary" onClick={() => abortRef.current?.abort()}>Cancel</Button>
          ) : (
            <Button size="sm" onClick={start} disabled={!model}>Run benchmark</Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
```
(Check `Progress` accepts a `variant` prop — scan.tsx uses `variant="success"|"iris"|"warning"`, so `iris` is valid.)

- [ ] **Step 4: Mount it on the Scan page**

In `web/src/pages/scan.tsx`:
1. Import: `import { BenchmarkDialog } from "@/components/benchmark-dialog";`
2. Replace the "Top picks" heading `div` with:
```tsx
<div className="mb-3 mt-6 flex items-center justify-between">
  <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
    <Gauge className="h-4 w-4" /> Top picks
  </div>
  <BenchmarkDialog onDone={() => recs.reload()} />
</div>
```
(`useAsync` exposes `reload` — same method the ErrorState retry uses.)

- [ ] **Step 5: Typecheck + build**

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both exit 0

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ui/dialog.tsx web/src/components/benchmark-dialog.tsx web/src/lib/api.ts web/src/pages/scan.tsx
git commit -m "feat(web): benchmark launcher dialog streaming /api/benchmark, recs refetch on done"
```

### Task 5: Merge the branch

**Files:** none (git only)

- [ ] **Step 1: Full verification gates**

```bash
cd C:\Users\User\repos\model-hub
.venv\Scripts\python.exe -m pytest -q
cd web && npm run typecheck && npm run build
```
Expected: all green/clean.
CLI regression spot-check:
```bash
cd C:\Users\User\repos\model-hub
.venv\Scripts\python.exe cli.py recommend --help
.venv\Scripts\python.exe cli.py benchmark --help
```
Expected: both print help, exit 0.

- [ ] **Step 2: Merge to master**

```bash
git checkout master
git merge --no-ff feat/web-technical-controls -m "merge: web technical controls (calibration surface, GPU what-if filters, benchmark launcher)"
.venv\Scripts\python.exe -m pytest -q
```
Expected: merge clean, suite green on master.

---

## Part B — Plugin seam (core, public)

New branch off master: `git checkout -b feat/plugin-seam`.

### Task 6: `backend/plugins.py` discovery module

**Files:**
- Create: `backend/plugins.py`
- Test: `tests/test_plugins.py`

**Interfaces:**
- Produces:
  - `GROUP = "apt.plugins"`
  - `@dataclass LoadedPlugin(name: str, version: str, obj: object | None, error: str | None = None)` with `ok` property (`error is None`)
  - `discover() -> list[LoadedPlugin]` — loads every entry point in the group; a plugin that raises on load yields `LoadedPlugin(name=<ep.name>, version="?", obj=None, error=str(exc))` instead of propagating.
- Plugin contract (duck-typed, in module docstring): plugin object exposes `name: str`, `version: str`, optional `register_cli(subparsers)`, optional `register_api(app)`.

- [ ] **Step 1: Write failing tests**

`tests/test_plugins.py`:
```python
"""Plugin seam: entry-point discovery with per-plugin error isolation."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import discover, LoadedPlugin


class FakeEntryPoint:
    def __init__(self, name, obj=None, exc=None):
        self.name = name
        self._obj = obj
        self._exc = exc

    def load(self):
        if self._exc:
            raise self._exc
        return self._obj


def _patch_eps(monkeypatch, eps):
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: eps)


def test_discover_loads_wellformed_plugin(monkeypatch):
    plug = SimpleNamespace(name="pro", version="0.1.0")
    _patch_eps(monkeypatch, [FakeEntryPoint("pro", obj=plug)])
    out = discover()
    assert len(out) == 1
    assert out[0].ok
    assert out[0].name == "pro"
    assert out[0].version == "0.1.0"
    assert out[0].obj is plug


def test_discover_isolates_broken_plugin(monkeypatch):
    good = SimpleNamespace(name="good", version="1.0")
    _patch_eps(monkeypatch, [
        FakeEntryPoint("broken", exc=ImportError("boom")),
        FakeEntryPoint("good", obj=good),
    ])
    out = discover()
    assert len(out) == 2
    broken = next(p for p in out if p.name == "broken")
    assert not broken.ok
    assert "boom" in broken.error
    assert next(p for p in out if p.name == "good").ok


def test_discover_defaults_missing_metadata(monkeypatch):
    plug = SimpleNamespace()  # no name/version attrs
    _patch_eps(monkeypatch, [FakeEntryPoint("bare", obj=plug)])
    out = discover()
    assert out[0].name == "bare"       # falls back to entry-point name
    assert out[0].version == "?"
    assert out[0].ok


def test_discover_empty(monkeypatch):
    _patch_eps(monkeypatch, [])
    assert discover() == []
```

- [ ] **Step 2: Run tests — verify fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plugins.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.plugins'`

- [ ] **Step 3: Implement**

`backend/plugins.py`:
```python
"""Open-core plugin seam.

Plugins are Python packages exposing an entry point in the ``apt.plugins``
group. The entry point resolves to a plugin object with:

- ``name: str``            display name (falls back to the entry-point name)
- ``version: str``         plugin version (falls back to "?")
- ``register_cli(subparsers)``  optional — add argparse subcommands
- ``register_api(app)``         optional — add Flask routes

A plugin that raises during load or registration must never break core:
every call is isolated and errors are captured on the LoadedPlugin record.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points

GROUP = "apt.plugins"


def _entry_points():
    """Indirection so tests can substitute fake entry points."""
    return list(entry_points(group=GROUP))


@dataclass
class LoadedPlugin:
    name: str
    version: str
    obj: object | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def discover() -> list[LoadedPlugin]:
    """Load all ``apt.plugins`` entry points, isolating per-plugin failures."""
    out: list[LoadedPlugin] = []
    for ep in _entry_points():
        try:
            obj = ep.load()
        except Exception as exc:  # noqa: BLE001 — a plugin must never break core
            out.append(LoadedPlugin(name=ep.name, version="?", obj=None, error=str(exc)))
            continue
        name = getattr(obj, "name", None) or ep.name
        version = getattr(obj, "version", None) or "?"
        out.append(LoadedPlugin(name=name, version=version, obj=obj))
    return out
```

- [ ] **Step 4: Run tests — verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plugins.py -q`
Expected: 4 passed. Then full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/plugins.py tests/test_plugins.py
git commit -m "feat(core): apt.plugins entry-point discovery with per-plugin error isolation"
```

### Task 7: Mount plugins in the CLI + `apt plugins` command

**Files:**
- Modify: `cli.py` (extract `build_parser()` from `main()`; mount plugins; new `cmd_plugins`)
- Test: `tests/test_cli_plugins.py`

**Interfaces:**
- Consumes: `backend.plugins.discover()` (Task 6).
- Produces: `cli.build_parser() -> ArgumentParser` (parser construction extracted from `main()` — plugin CLI hooks run at the end of it); plugins' `register_cli(sub)` receives the argparse subparsers action and uses `set_defaults(func=...)`; `apt plugins` lists discovered plugins; `main()` dispatches `args.func(args)` when set, before/instead of the existing command-name dispatch for plugin commands.

- [ ] **Step 1: Write failing tests**

`tests/test_cli_plugins.py`:
```python
"""CLI plugin mounting: plugins add subcommands; apt plugins lists them."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def _fake_discover(monkeypatch, plugins):
    monkeypatch.setattr(plugins_mod, "discover", lambda: plugins)


def test_plugin_subcommand_is_mounted(monkeypatch):
    calls = {}

    def register_cli(sub):
        p = sub.add_parser("prototest", help="plugin-added command")
        p.set_defaults(func=lambda args: calls.setdefault("ran", True))

    plug = SimpleNamespace(name="fake", version="9.9", register_cli=register_cli)
    _fake_discover(monkeypatch, [LoadedPlugin("fake", "9.9", plug)])

    import cli
    parser = cli.build_parser()
    args = parser.parse_args(["prototest"])
    args.func(args)
    assert calls["ran"] is True


def test_broken_register_cli_does_not_crash(monkeypatch):
    def register_cli(sub):
        raise RuntimeError("plugin exploded")

    plug = SimpleNamespace(name="bad", version="0.0", register_cli=register_cli)
    _fake_discover(monkeypatch, [LoadedPlugin("bad", "0.0", plug)])

    import cli
    parser = cli.build_parser()  # must not raise
    args = parser.parse_args(["list"])
    assert args is not None


def test_cmd_plugins_lists(monkeypatch, capsys):
    plug = SimpleNamespace(name="fake", version="9.9")
    _fake_discover(monkeypatch, [
        LoadedPlugin("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="ImportError: nope"),
    ])
    import cli
    cli.cmd_plugins(SimpleNamespace())
    out = capsys.readouterr().out
    assert "fake" in out and "9.9" in out
    assert "broken" in out and "error" in out.lower()
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_plugins.py -q`
Expected: FAIL — `cli` has no `build_parser` / `cmd_plugins`.

- [ ] **Step 3: Implement in cli.py**

1. Extract parser construction from `main()` into a top-level `def build_parser():` — everything from `parser = argparse.ArgumentParser(...)` through the last core `add_parser`/argument definition, ending with the plugin mount + `return parser`. `main()` becomes:
```python
def main():
    parser = build_parser()
    # ... existing parse_args + dispatch code, unchanged ...
```
2. At the end of `build_parser()` (before `return parser`; `sub` is the existing subparsers variable):
```python
    # --- plugin seam: mount plugin CLI subcommands (never fatal) ---
    from backend import plugins as _plugins
    for _p in _plugins.discover():
        reg = getattr(_p.obj, "register_cli", None)
        if not _p.ok or reg is None:
            continue
        try:
            reg(sub)
        except Exception as e:  # noqa: BLE001
            eprint(f"[plugin:{_p.name}] register_cli failed: {e}")
    return parser
```
3. Add the core `plugins` command (with the other `cmd_*` functions):
```python
def cmd_plugins(args):
    from backend import plugins as _plugins
    found = _plugins.discover()
    print_header("Plugins")
    if not found:
        print("  No plugins installed. Pro and community plugins mount here.")
        return
    rows = []
    for p in found:
        status = "ok" if p.ok else f"error: {p.error}"
        rows.append([p.name, p.version, status])
    print_table(["Name", "Version", "Status"], rows)
```
And in `build_parser()` with the other subparsers:
```python
    p_plugins = sub.add_parser("plugins", help="List installed APT plugins")
    p_plugins.set_defaults(func=cmd_plugins)
```
4. Dispatch: inspect how `main()` currently routes commands. If it routes via `set_defaults(func=...)` already, nothing more. If it routes via an `if args.command == ...` chain, add — BEFORE that chain:
```python
    if hasattr(args, "func"):
        return args.func(args)
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_plugins.py -q` → 3 passed. Full suite green.
CLI regression: `.venv\Scripts\python.exe cli.py plugins` prints "No plugins installed…"; `cli.py --help` exits 0 and lists `plugins`.

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_cli_plugins.py
git commit -m "feat(cli): mount plugin subcommands via apt.plugins + apt plugins listing"
```

### Task 8: Mount plugins in the Flask API + `/api/plugins`

**Files:**
- Modify: `backend/api.py` (bottom of module, after all core routes)
- Test: `tests/test_api_plugins.py` (reuse the `flask_app` fixture from the existing API tests — if it lives inside `tests/test_api.py`, move it to `tests/conftest.py` so both files share it)

**Interfaces:**
- Consumes: `backend.plugins.discover()`.
- Produces: `GET /api/plugins` → `[{name, version, ok, error}]`; `_mount_plugins(flask_app)` — calls each plugin's `register_api(app)`, isolated per plugin; invoked once at module bottom (`_mount_plugins(app)`).

- [ ] **Step 1: Write failing tests**

`tests/test_api_plugins.py`:
```python
"""API plugin mounting + /api/plugins listing."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def test_api_plugins_endpoint_lists(monkeypatch, flask_app):
    plug = SimpleNamespace(name="fake", version="9.9")
    monkeypatch.setattr(plugins_mod, "discover", lambda: [
        LoadedPlugin("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="nope"),
    ])
    client = flask_app.test_client()
    r = client.get("/api/plugins")
    assert r.status_code == 200
    data = r.get_json()
    assert {p["name"] for p in data} == {"fake", "broken"}
    assert next(p for p in data if p["name"] == "broken")["ok"] is False


def test_register_api_mounts_routes(monkeypatch, flask_app):
    def register_api(app):
        @app.route("/api/pro/ping")
        def _pro_ping():
            return {"pong": True}

    plug = SimpleNamespace(name="fake", version="9.9", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "9.9", plug)])

    from backend.api import _mount_plugins
    _mount_plugins(flask_app)
    client = flask_app.test_client()
    assert client.get("/api/pro/ping").get_json() == {"pong": True}


def test_broken_register_api_is_isolated(monkeypatch, flask_app):
    def register_api(app):
        raise RuntimeError("boom")

    plug = SimpleNamespace(name="bad", version="0.0", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("bad", "0.0", plug)])
    from backend.api import _mount_plugins
    _mount_plugins(flask_app)  # must not raise
```
NOTE: if the `flask_app` fixture yields a test client instead of the app object, adapt: mount onto the underlying `.application` and keep the assertions the same. Match the existing fixture's shape — do not invent a new one.

- [ ] **Step 2: Run — verify fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_plugins.py -q`
Expected: FAIL — no `/api/plugins` route, no `_mount_plugins`.

- [ ] **Step 3: Implement in backend/api.py**

At the bottom of the module (after the last core route definition):
```python
# --- plugin seam -----------------------------------------------------------

@app.route("/api/plugins")
def api_plugins():
    from backend import plugins as _plugins
    return jsonify([
        {"name": p.name, "version": p.version, "ok": p.ok, "error": p.error}
        for p in _plugins.discover()
    ])


def _mount_plugins(flask_app):
    """Call each plugin's register_api(app). Isolated: a broken plugin logs and moves on."""
    from backend import plugins as _plugins
    for p in _plugins.discover():
        reg = getattr(p.obj, "register_api", None)
        if not p.ok or reg is None:
            continue
        try:
            reg(flask_app)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] register_api failed: {e}")


_mount_plugins(app)
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_plugins.py tests/test_api.py -q` → all pass. Full suite green.

- [ ] **Step 5: Commit + merge the seam branch**

```bash
git add backend/api.py tests/test_api_plugins.py tests/conftest.py
git commit -m "feat(api): mount plugin routes via apt.plugins + /api/plugins listing"
git checkout master
git merge --no-ff feat/plugin-seam -m "merge: open-core plugin seam (CLI + API mounting, apt plugins, /api/plugins)"
.venv\Scripts\python.exe -m pytest -q
```
Expected: green on master.

---

## Part C — `apt-pro` (private repo, closed source)

All tasks in `C:\Users\User\repos\apt-pro`. Tests run with model-hub's venv python (editable-installed). **Never push this repo to a public remote.**

### Task 9: Scaffold apt-pro + entry point + `apt pro status`

**Files:**
- Create: `C:\Users\User\repos\apt-pro\pyproject.toml`
- Create: `C:\Users\User\repos\apt-pro\apt_pro\__init__.py`
- Create: `C:\Users\User\repos\apt-pro\apt_pro\plugin.py`
- Create: `C:\Users\User\repos\apt-pro\apt_pro\license.py` (minimal placeholder — Task 10 replaces)
- Create: `C:\Users\User\repos\apt-pro\tests\test_plugin.py`
- Create: `C:\Users\User\repos\apt-pro\.gitignore` (`__pycache__/`, `*.egg-info/`, `.pytest_cache/`, `dist/`, `build/`)

**Interfaces:**
- Produces: entry point `apt.plugins` → `pro = apt_pro.plugin:PLUGIN`; `PLUGIN.register_cli(sub)` adds the `apt pro <sub>` namespace with `status`; `PLUGIN.register_api(app)` no-op; `apt_pro.plugin._SUBCOMMANDS: list[tuple[name, help, configure_fn]]` registry that Tasks 12–13 append to (`configure_fn(parser)` adds args + `set_defaults(func=...)`).

- [ ] **Step 1: Init repo + failing test**

```bash
mkdir C:\Users\User\repos\apt-pro && cd C:\Users\User\repos\apt-pro && git init
```
`tests/test_plugin.py`:
```python
"""Plugin contract: metadata + CLI registration shape."""
import argparse

from apt_pro.plugin import PLUGIN


def _build_sub():
    parser = argparse.ArgumentParser(prog="apt")
    return parser, parser.add_subparsers(dest="command")


def test_plugin_metadata():
    assert PLUGIN.name == "pro"
    assert PLUGIN.version


def test_register_cli_adds_pro_namespace():
    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "status"])
    assert args.command == "pro"
    assert callable(args.func)


def test_pro_status_runs(capsys):
    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "status"])
    args.func(args)
    out = capsys.readouterr().out
    assert "APT Pro" in out
```

- [ ] **Step 2: Run — verify fail**

Run: `cd C:\Users\User\repos\apt-pro && C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q`
Expected: FAIL — `ModuleNotFoundError: apt_pro`

- [ ] **Step 3: Implement scaffold**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "apt-pro"
version = "0.1.0"
description = "APT Pro — Tuning Cockpit plugin (proprietary)"
requires-python = ">=3.10"
license = { text = "Proprietary" }

[project.entry-points."apt.plugins"]
pro = "apt_pro.plugin:PLUGIN"

[tool.setuptools.packages.find]
include = ["apt_pro*"]
```
`apt_pro/__init__.py`:
```python
__version__ = "0.1.0"
```
`apt_pro/license.py` (placeholder so status imports; Task 10 replaces):
```python
"""License gate — Task 10 implements the real stub."""
from __future__ import annotations


def check():
    return None
```
`apt_pro/plugin.py`:
```python
"""APT Pro plugin object — the single entry point core discovers.

Subfeatures register in _SUBCOMMANDS: (name, help, configure_fn) where
configure_fn(parser) adds arguments and sets parser defaults func=...
"""
from __future__ import annotations

from apt_pro import __version__

_SUBCOMMANDS: list[tuple] = []


def _cmd_status(args) -> None:
    from apt_pro.license import check
    grant = check()
    print("APT Pro — Tuning Cockpit")
    print(f"  version : {__version__}")
    if grant:
        print(f"  license : {grant.plan} (expires {grant.expires_human})")
    else:
        print("  license : none — set APT_PRO_DEV=1 (dev) or run: apt pro activate <key>")


class ProPlugin:
    name = "pro"
    version = __version__

    def register_cli(self, subparsers) -> None:
        p_pro = subparsers.add_parser("pro", help="APT Pro — tuning cockpit")
        pro_sub = p_pro.add_subparsers(dest="pro_command", required=True)
        p_status = pro_sub.add_parser("status", help="Show Pro/license status")
        p_status.set_defaults(func=_cmd_status)
        for name, help_text, configure in _SUBCOMMANDS:
            configure(pro_sub.add_parser(name, help=help_text))

    def register_api(self, app) -> None:  # Pro API surface lands in spec Phase 2
        return


PLUGIN = ProPlugin()
```

- [ ] **Step 4: Editable-install + run tests**

```bash
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pip install -e C:\Users\User\repos\apt-pro
cd C:\Users\User\repos\apt-pro && C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q
```
Expected: 3 passed.

- [ ] **Step 5: Prove end-to-end discovery through core**

```bash
cd C:\Users\User\repos\model-hub
.venv\Scripts\python.exe cli.py plugins
.venv\Scripts\python.exe cli.py pro status
.venv\Scripts\python.exe -m pytest -q
```
Expected: `plugins` lists `pro 0.1.0 ok`; `pro status` prints the status block; core suite still green. If a core test asserts on `--help` output or the exact subcommand set, fix that assertion to be superset-tolerant — do not uninstall the plugin to make it pass.

- [ ] **Step 6: Commit (apt-pro repo)**

```bash
cd C:\Users\User\repos\apt-pro && git add -A
git commit -m "feat: apt-pro scaffold — apt.plugins entry point, pro namespace, status command"
```

### Task 10: License gate stub

**Files:**
- Modify: `C:\Users\User\repos\apt-pro\apt_pro\license.py` (replace placeholder)
- Test: `C:\Users\User\repos\apt-pro\tests\test_license.py`

**Interfaces:**
- Produces (the Plan-2 contract — LemonSqueezy replaces internals, signatures stay):
  - `@dataclass Grant(key: str, plan: str, expires_at: float)` with `valid` property (`expires_at > time.time()`) and `expires_human` property (YYYY-MM-DD)
  - `check() -> Grant | None` — order: `APT_PRO_DEV=1` env → synthetic dev grant; else `GRANT_PATH` JSON `{key, plan, expires_at}`; missing/corrupt/expired → `None`; never raises
  - `require(feature: str) -> Grant` — grant or `SystemExit(3)` after printing an upgrade message naming the feature
  - `GRANT_PATH` module constant = `Path.home() / ".model-hub" / "license.json"` (patchable in tests)

- [ ] **Step 1: Write failing tests**

`tests/test_license.py`:
```python
"""License gate stub: dev mode, grant file, expiry, require()."""
import json
import time

import pytest

import apt_pro.license as lic


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("APT_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "license.json")


def _write_grant(path, expires_delta):
    path.write_text(json.dumps({
        "key": "TEST-KEY", "plan": "pro", "expires_at": time.time() + expires_delta,
    }))


def test_no_grant_returns_none():
    assert lic.check() is None


def test_dev_env_grants(monkeypatch):
    monkeypatch.setenv("APT_PRO_DEV", "1")
    grant = lic.check()
    assert grant is not None
    assert grant.plan == "dev"


def test_valid_grant_file():
    _write_grant(lic.GRANT_PATH, 3600)
    grant = lic.check()
    assert grant is not None and grant.key == "TEST-KEY"


def test_expired_grant_is_none():
    _write_grant(lic.GRANT_PATH, -3600)
    assert lic.check() is None


def test_corrupt_grant_is_none():
    lic.GRANT_PATH.write_text("{not json")
    assert lic.check() is None


def test_require_passes_with_grant(monkeypatch):
    monkeypatch.setenv("APT_PRO_DEV", "1")
    assert lic.require("tune").plan == "dev"


def test_require_exits_without_grant(capsys):
    with pytest.raises(SystemExit) as e:
        lic.require("tune")
    assert e.value.code == 3
    assert "tune" in capsys.readouterr().out
```

- [ ] **Step 2: Run — verify fail**

Run: `cd C:\Users\User\repos\apt-pro && C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_license.py -q`
Expected: FAIL — missing `GRANT_PATH`, `Grant`, `require`.

- [ ] **Step 3: Implement**

`apt_pro/license.py` (full replacement):
```python
"""License gate.

v1 = local stub: APT_PRO_DEV=1 or a grant file at ~/.model-hub/license.json
({key, plan, expires_at}). Plan 2 swaps the internals for LemonSqueezy
activation; check()/require() signatures are the stable contract.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GRANT_PATH = Path.home() / ".model-hub" / "license.json"

_UPGRADE_MSG = (
    "\n  '{feature}' is an APT Pro feature.\n"
    "  Get a license: https://apt.example/pro  (dev override: set APT_PRO_DEV=1)\n"
)


@dataclass
class Grant:
    key: str
    plan: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return self.expires_at > time.time()

    @property
    def expires_human(self) -> str:
        return datetime.fromtimestamp(self.expires_at).strftime("%Y-%m-%d")


def check() -> Grant | None:
    """Return the active grant, or None. Never raises."""
    if os.environ.get("APT_PRO_DEV") == "1":
        return Grant(key="dev", plan="dev", expires_at=time.time() + 86400)
    try:
        data = json.loads(GRANT_PATH.read_text())
        grant = Grant(
            key=str(data["key"]),
            plan=str(data.get("plan", "pro")),
            expires_at=float(data["expires_at"]),
        )
    except Exception:  # noqa: BLE001 — missing/corrupt file == unlicensed
        return None
    return grant if grant.valid else None


def require(feature: str) -> Grant:
    """Return the grant or exit(3) with an upgrade message naming the feature."""
    grant = check()
    if grant is None:
        print(_UPGRADE_MSG.format(feature=feature))
        raise SystemExit(3)
    return grant
```
The `https://apt.example/pro` URL is a deliberate placeholder until the landing page exists (Plan 3 replaces it — tracked there).

- [ ] **Step 4: Run — verify pass**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (in apt-pro) — all pass.
Also: `cd C:\Users\User\repos\model-hub && .venv\Scripts\python.exe cli.py pro status` shows "license : none — …".

- [ ] **Step 5: Commit**

```bash
cd C:\Users\User\repos\apt-pro && git add -A
git commit -m "feat: license gate stub (dev env + local grant file, require() exit-3 contract)"
```

### Task 11: Offload config model + candidate generation

**Files:**
- Create: `apt_pro/offload.py`
- Test: `tests/test_offload.py`

**Interfaces:**
- Consumes: Ollama `POST /api/show {"model": name}` response JSON (`model_info` dict has a key ending `.block_count`, e.g. `"llama.block_count": 32`).
- Produces:
  - `@dataclass(frozen=True) OffloadConfig(label: str, num_gpu: int | None, num_ctx: int | None = None)` with `options() -> dict` (only non-None keys)
  - `model_layers(show_json: dict) -> int | None`
  - `candidate_configs(total_layers: int) -> list[OffloadConfig]` — order: auto (num_gpu=None), all(=total), 75%, 50% (ceil), deduped preserving first occurrence.

- [ ] **Step 1: Write failing tests**

`tests/test_offload.py`:
```python
"""Offload config candidates from a model's layer count."""
from apt_pro.offload import OffloadConfig, candidate_configs, model_layers


def test_model_layers_parses_block_count():
    show = {"model_info": {"llama.block_count": 32, "llama.context_length": 8192}}
    assert model_layers(show) == 32


def test_model_layers_any_arch_prefix():
    show = {"model_info": {"qwen3.block_count": 48}}
    assert model_layers(show) == 48


def test_model_layers_missing():
    assert model_layers({"model_info": {}}) is None
    assert model_layers({}) is None


def test_options_only_set_keys():
    assert OffloadConfig("auto", None).options() == {}
    assert OffloadConfig("all", 32).options() == {"num_gpu": 32}
    assert OffloadConfig("x", 16, num_ctx=4096).options() == {"num_gpu": 16, "num_ctx": 4096}


def test_candidates_32_layers():
    cands = candidate_configs(32)
    assert [c.label for c in cands] == ["auto", "all-32", "gpu-24", "gpu-16"]
    assert [c.num_gpu for c in cands] == [None, 32, 24, 16]


def test_candidates_dedup_small_model():
    # 2 layers: all=2, 75%->2 (dup, dropped), 50%->1
    cands = candidate_configs(2)
    assert [c.num_gpu for c in cands] == [None, 2, 1]
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_offload.py -q` (in apt-pro)
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`apt_pro/offload.py`:
```python
"""Offload configuration: which layer splits to try on this model."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OffloadConfig:
    label: str
    num_gpu: int | None          # None = let Ollama decide (auto)
    num_ctx: int | None = None

    def options(self) -> dict:
        out: dict = {}
        if self.num_gpu is not None:
            out["num_gpu"] = self.num_gpu
        if self.num_ctx is not None:
            out["num_ctx"] = self.num_ctx
        return out


def model_layers(show_json: dict) -> int | None:
    """Layer (block) count from an Ollama /api/show response, any architecture."""
    info = show_json.get("model_info") or {}
    for key, val in info.items():
        if key.endswith(".block_count"):
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
    return None


def candidate_configs(total_layers: int) -> list[OffloadConfig]:
    """Sweep candidates: auto, all layers, 75%, 50% — deduped, order preserved."""
    raw: list[tuple[str, int | None]] = [
        ("auto", None),
        (f"all-{total_layers}", total_layers),
        (f"gpu-{math.ceil(total_layers * 0.75)}", math.ceil(total_layers * 0.75)),
        (f"gpu-{math.ceil(total_layers * 0.5)}", math.ceil(total_layers * 0.5)),
    ]
    seen: set[int | None] = set()
    out: list[OffloadConfig] = []
    for label, n in raw:
        if n in seen:
            continue
        seen.add(n)
        out.append(OffloadConfig(label, n))
    return out
```

- [ ] **Step 4: Run — verify pass, commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (in apt-pro) — all pass.
```bash
git add -A && git commit -m "feat: offload config model + layer-count candidate sweep"
```

### Task 12: `apt pro tune <model>` — the sweep

**Files:**
- Create: `apt_pro/tune.py`
- Modify: `apt_pro/plugin.py` (register via `_SUBCOMMANDS`)
- Test: `tests/test_tune.py`

**Interfaces:**
- Consumes: `apt_pro.license.require("tune")`; `apt_pro.offload.{model_layers, candidate_configs, OffloadConfig}`; core's `backend.cookbook.config.get_config` for the Ollama host (core is guaranteed importable — core loaded the plugin; verify the function name in `backend/cookbook/config.py` and adjust the import if it differs); Ollama HTTP via injectable callables.
- Produces:
  - `run_sweep(model, ollama_generate, ollama_show, repeat=2, num_predict=128) -> dict` → `{"model", "layers", "results": [{"label", "num_gpu", "median_tps", "runs"}], "winner": <result>}` — results in config order, winner = highest median_tps
  - injected `ollama_generate(model, prompt, options, num_predict) -> dict` (raw /api/generate JSON) and `ollama_show(model) -> dict`; CLI wires `http_generate`/`http_show`
  - `TUNE_LOG` constant → `~/.model-hub/benchmarks/tune.jsonl`; every run appended with `config_label`/`num_gpu`; **never touches results.jsonl**
  - CLI: `apt pro tune <model> [--repeat N] [--apply]` (`--apply` implemented in Task 13)
  - `configure_parser(parser)` for the `_SUBCOMMANDS` registry

- [ ] **Step 1: Write failing tests**

`tests/test_tune.py`:
```python
"""Tune sweep: candidates benchmarked, winner picked, tune.jsonl written, gated."""
import json

import pytest

import apt_pro.tune as tune_mod
from apt_pro.tune import run_sweep


def _fake_show(model):
    return {"model_info": {"llama.block_count": 4}}


def _gen_factory(tps_by_num_gpu):
    """Fake /api/generate: speed depends on options.num_gpu ('auto' when absent)."""
    def gen(model, prompt, options, num_predict):
        tps = tps_by_num_gpu[options.get("num_gpu", "auto")]
        return {"eval_count": 100, "eval_duration": int(100 / tps * 1e9),
                "total_duration": 1, "load_duration": 0, "prompt_eval_duration": 0,
                "response": "x"}
    return gen


@pytest.fixture(autouse=True)
def tune_log(tmp_path, monkeypatch):
    log = tmp_path / "tune.jsonl"
    monkeypatch.setattr(tune_mod, "TUNE_LOG", log)
    return log


def test_sweep_ranks_winner(tune_log):
    gen = _gen_factory({"auto": 20.0, 4: 50.0, 3: 40.0, 2: 10.0})
    out = run_sweep("m", gen, _fake_show, repeat=1)
    assert out["layers"] == 4
    assert [r["label"] for r in out["results"]] == ["auto", "all-4", "gpu-3", "gpu-2"]
    assert out["winner"]["num_gpu"] == 4
    assert out["winner"]["median_tps"] == pytest.approx(50.0, rel=0.01)


def test_sweep_repeat_takes_median(tune_log):
    seq = iter([10.0, 30.0, 20.0])

    def gen(model, prompt, options, num_predict):
        tps = next(seq)
        return {"eval_count": 100, "eval_duration": int(100 / tps * 1e9),
                "total_duration": 1, "load_duration": 0, "prompt_eval_duration": 0,
                "response": "x"}

    def show(model):
        return {"model_info": {}}  # unknown layers -> auto-only sweep

    out = run_sweep("m", gen, show, repeat=3)
    assert out["layers"] is None
    assert len(out["results"]) == 1
    assert out["results"][0]["median_tps"] == pytest.approx(20.0, rel=0.01)


def test_sweep_writes_tune_log(tune_log):
    gen = _gen_factory({"auto": 20.0, 4: 50.0, 3: 40.0, 2: 10.0})
    run_sweep("m", gen, _fake_show, repeat=1)
    rows = [json.loads(l) for l in tune_log.read_text().splitlines()]
    assert len(rows) == 4
    assert all("config_label" in r for r in rows)
    assert rows[1]["num_gpu"] == 4


def test_cli_tune_is_license_gated(monkeypatch, tmp_path):
    import argparse
    from apt_pro.plugin import PLUGIN
    import apt_pro.license as lic
    monkeypatch.delenv("APT_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "nope.json")

    parser = argparse.ArgumentParser(prog="apt")
    sub = parser.add_subparsers(dest="command")
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "tune", "somemodel"])
    with pytest.raises(SystemExit) as e:
        args.func(args)
    assert e.value.code == 3
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_tune.py -q` (in apt-pro)
Expected: FAIL — `apt_pro.tune` missing.

- [ ] **Step 3: Implement**

`apt_pro/tune.py`:
```python
"""`apt pro tune` — sweep offload configs, benchmark each, pick the winner.

Sweep runs use non-default configs, so they log to tune.jsonl —
NEVER results.jsonl, which feeds the calibration loop.
"""
from __future__ import annotations

import json
import statistics
import time
import urllib.request
from pathlib import Path

from apt_pro.license import require
from apt_pro.offload import candidate_configs, model_layers, OffloadConfig

TUNE_LOG = Path.home() / ".model-hub" / "benchmarks" / "tune.jsonl"
PROMPT = "Write a detailed explanation of how HTTP caching works."


def _log(entry: dict) -> None:
    try:
        TUNE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry["timestamp"] = time.time()
        with open(TUNE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001 — logging must never kill a sweep
        pass


def _tps(result: dict) -> float:
    ec, ed = result.get("eval_count", 0), result.get("eval_duration", 0)
    return ec / (ed / 1e9) if ed > 0 else 0.0


def run_sweep(model: str, ollama_generate, ollama_show, repeat: int = 2,
              num_predict: int = 128) -> dict:
    """Benchmark every candidate config; return ranked results + winner."""
    layers = model_layers(ollama_show(model))
    configs = candidate_configs(layers) if layers else [OffloadConfig("auto", None)]

    results = []
    for cfg in configs:
        tps_runs: list[float] = []
        for _ in range(max(1, repeat)):
            resp = ollama_generate(model, PROMPT, cfg.options(), num_predict)
            tps = round(_tps(resp), 2)
            tps_runs.append(tps)
            _log({"model": model, "config_label": cfg.label, "num_gpu": cfg.num_gpu,
                  "tokens_per_second": tps})
        results.append({
            "label": cfg.label,
            "num_gpu": cfg.num_gpu,
            "median_tps": round(statistics.median(tps_runs), 2),
            "runs": tps_runs,
        })

    winner = max(results, key=lambda r: r["median_tps"])
    return {"model": model, "layers": layers, "results": results, "winner": winner}


# --- real Ollama wiring (CLI path) -----------------------------------------

def _ollama_host() -> str:
    try:
        from backend.cookbook.config import get_config  # core; verify name on implementation
        return get_config().get("ollama_host", "http://localhost:11434")
    except Exception:  # noqa: BLE001
        return "http://localhost:11434"


def _ollama_json(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        _ollama_host().rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_generate(model: str, prompt: str, options: dict, num_predict: int) -> dict:
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_predict": num_predict, **options}}
    return _ollama_json("/api/generate", body)


def http_show(model: str) -> dict:
    return _ollama_json("/api/show", {"model": model}, timeout=30)


# --- CLI --------------------------------------------------------------------

def cmd_tune(args) -> None:
    require("tune")
    print(f"Tuning {args.model} — sweeping offload configs (repeat={args.repeat})…")
    out = run_sweep(args.model, http_generate, http_show, repeat=args.repeat)
    if out["layers"]:
        print(f"  layers: {out['layers']}")
    print(f"  {'config':<10} {'num_gpu':>8} {'median tok/s':>14}")
    for r in out["results"]:
        star = "  <- winner" if r is out["winner"] else ""
        num_gpu = "auto" if r["num_gpu"] is None else r["num_gpu"]
        print(f"  {r['label']:<10} {str(num_gpu):>8} {r['median_tps']:>14.1f}{star}")
    w = out["winner"]
    if w["num_gpu"] is None:
        print("\nOllama's automatic split is already optimal — nothing to apply.")
    else:
        print(f"\nBest: {w['label']} at {w['median_tps']:.1f} tok/s.")
        if getattr(args, "apply", False):
            from apt_pro.apply import apply_config
            name = apply_config(args.model, w["num_gpu"])
            print(f"Created tuned variant: {name}")
        else:
            print(f"Bake it in:  apt pro tune {args.model} --apply")


def configure_parser(parser) -> None:
    parser.add_argument("model", help="Installed Ollama model to tune")
    parser.add_argument("--repeat", type=int, default=2, help="Benchmark runs per config (default 2)")
    parser.add_argument("--apply", action="store_true", help="Create a tuned model variant from the winner")
    parser.set_defaults(func=cmd_tune)
```
Register in `apt_pro/plugin.py` — after `_SUBCOMMANDS = []` add:
```python
from apt_pro import tune as _tune

_SUBCOMMANDS.append(("tune", "Sweep offload configs and find the fastest for this rig", _tune.configure_parser))
```
(Import placed after `_SUBCOMMANDS` to avoid the circular-import trap — `tune` does not import `plugin`.)
NOTE: `--apply` lazily imports `apt_pro.apply` (Task 13) inside `cmd_tune` behind the flag — acceptable this task; Task 13 lands next and carries the `--apply` test.

- [ ] **Step 4: Run — verify pass**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (in apt-pro) — all pass.
Optional live check (needs Ollama + a small model; skip if down):
```powershell
cd C:\Users\User\repos\model-hub
$env:APT_PRO_DEV="1"; .venv\Scripts\python.exe cli.py pro tune llama3.2:3b --repeat 1
```
Expected: sweep table, winner marked, `~/.model-hub/benchmarks/tune.jsonl` gains rows, `results.jsonl` untouched.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: apt pro tune — offload sweep with median ranking, tune.jsonl logging, license gate"
```

### Task 13: `--apply` — bake the winner into a model variant

**Files:**
- Create: `apt_pro/apply.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- Consumes: Ollama `POST /api/create` (modern JSON shape: `{"model": <new>, "from": <base>, "parameters": {...}, "stream": false}`); `apt_pro.tune._ollama_json`.
- Produces: `apply_config(model: str, num_gpu: int, num_ctx: int | None = None, create_fn=None) -> str` — returns `<base>-tuned` where base = model name with `:` → `-` (`qwen3:30b-a3b` → `qwen3-30b-a3b-tuned`); `create_fn(body: dict) -> dict` injectable (default wires `_ollama_json("/api/create", body)`).

- [ ] **Step 1: Write failing tests**

`tests/test_apply.py`:
```python
"""Apply: bake a winning offload config into an Ollama model variant."""
from apt_pro.apply import apply_config


def test_apply_builds_create_request():
    captured = {}

    def fake_create(body):
        captured.update(body)
        return {"status": "success"}

    name = apply_config("qwen3:30b-a3b", 24, create_fn=fake_create)
    assert name == "qwen3-30b-a3b-tuned"
    assert captured["model"] == "qwen3-30b-a3b-tuned"
    assert captured["from"] == "qwen3:30b-a3b"
    assert captured["parameters"] == {"num_gpu": 24}
    assert captured["stream"] is False


def test_apply_includes_ctx_when_given():
    captured = {}
    apply_config("m", 8, num_ctx=4096, create_fn=lambda b: captured.update(b) or {})
    assert captured["parameters"] == {"num_gpu": 8, "num_ctx": 4096}
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_apply.py -q` (in apt-pro)
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`apt_pro/apply.py`:
```python
"""Bake a tuned offload config into a named Ollama model variant."""
from __future__ import annotations


def apply_config(model: str, num_gpu: int, num_ctx: int | None = None,
                 create_fn=None) -> str:
    """Create `<model>-tuned` with the given parameters baked in. Returns the name."""
    if create_fn is None:
        from apt_pro.tune import _ollama_json

        def create_fn(body):
            return _ollama_json("/api/create", body, timeout=120)

    new_name = model.replace(":", "-") + "-tuned"
    parameters: dict = {"num_gpu": num_gpu}
    if num_ctx is not None:
        parameters["num_ctx"] = num_ctx
    create_fn({"model": new_name, "from": model, "parameters": parameters, "stream": False})
    return new_name
```

- [ ] **Step 4: Run — verify pass, commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (in apt-pro) — all pass.
```bash
git add -A && git commit -m "feat: apply winning config as <model>-tuned variant via /api/create"
```

### Task 14: Docs + handoff refresh

**Files:**
- Create: `C:\Users\User\repos\apt-pro\README.md`
- Create: `C:\Users\User\repos\model-hub\docs\PLUGINS.md`
- Modify: `C:\Users\User\repos\model-hub\HANDOFF.md` (stale — rewrite the "Remaining"/"Next steps" sections)

**Interfaces:** none (docs).

- [ ] **Step 1: Write apt-pro README**

`C:\Users\User\repos\apt-pro\README.md`:
```markdown
# APT Pro — Tuning Cockpit (proprietary)

Closed-source plugin for APT. Mounts via the `apt.plugins` entry point.

## Commands
- `apt pro status` — plugin + license state
- `apt pro tune <model> [--repeat N] [--apply]` — sweep offload configs
  (auto / all / 75% / 50% GPU layers), benchmark each, report the fastest;
  `--apply` bakes the winner into `<model>-tuned`

Licensing: `APT_PRO_DEV=1` (dev) or `~/.model-hub/license.json` grant.
Plan 2 wires LemonSqueezy activation behind the same check()/require() contract.

## Dev setup
    C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pip install -e .
    C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q

Tune benchmarks log to `~/.model-hub/benchmarks/tune.jsonl` — never
`results.jsonl` (calibration stays clean).

**This repo must never get a public remote.**
```

- [ ] **Step 2: Write core plugin docs**

`C:\Users\User\repos\model-hub\docs\PLUGINS.md`:
```markdown
# APT Plugins

APT discovers plugins through the `apt.plugins` entry-point group.

## Writing a plugin
`pyproject.toml`:

    [project.entry-points."apt.plugins"]
    myplugin = "my_pkg.plugin:PLUGIN"

`PLUGIN` is any object with:
- `name: str`, `version: str`
- optional `register_cli(subparsers)` — add argparse subcommands (use `set_defaults(func=...)`)
- optional `register_api(app)` — add Flask routes

Errors in a plugin never break APT: load and registration are isolated per
plugin (`backend/plugins.py`). Inspect with `apt plugins` or `GET /api/plugins`.
```

- [ ] **Step 3: Refresh HANDOFF.md**

Replace the stale "Remaining / blocked" and "Next steps" sections of `C:\Users\User\repos\model-hub\HANDOFF.md` with current truth: calibration loop DONE; web technical controls DONE (merged); plugin seam DONE; apt-pro repo live at `C:\Users\User\repos\apt-pro` with tune/apply; NEXT = Plan 2 (LemonSqueezy licensing + calibration insights) then Plan 3 (release engineering + public launch) per `docs/superpowers/specs/2026-07-02-apt-v1-public-launch-design.md`. Keep the hardware/test-command sections, updating the test count.

- [ ] **Step 4: Commit both repos**

```bash
cd C:\Users\User\repos\apt-pro && git add README.md && git commit -m "docs: README (commands, dev setup, no-public-remote rule)"
cd C:\Users\User\repos\model-hub && git add docs/PLUGINS.md HANDOFF.md && git commit -m "docs: plugin authoring guide + HANDOFF refresh"
```

---

## Final verification (whole plan)

- [ ] Core suite green: `cd C:\Users\User\repos\model-hub && .venv\Scripts\python.exe -m pytest -q`
- [ ] Pro suite green: `cd C:\Users\User\repos\apt-pro && C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q`
- [ ] Web clean: `cd web && npm run typecheck && npm run build`
- [ ] `apt plugins` lists pro; `apt pro status` runs; without `APT_PRO_DEV`, `apt pro tune x` exits 3 with the upgrade message
- [ ] Live smoke (Ollama up): benchmark dialog streams in the browser; `apt pro tune llama3.2:3b --repeat 1` prints a sweep table; `results.jsonl` untouched by tune
- [ ] `git -C C:\Users\User\repos\apt-pro remote -v` prints nothing (no public remote)

## Deferred to later plans (explicit)

- Calibration insights (history/regression detection) → Plan 2.
- LemonSqueezy checkout/activation (`apt pro activate <key>`) → Plan 2 (replaces license.py internals; `check()/require()` contract frozen).
- Pro web/dashboard surface → spec Phase 2.
- Core packaging rename (`backend` → proper installable package), PyPI, installers, CI, landing page → Plan 3.
