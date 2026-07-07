import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Gauge, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// Mirrors lac_pro.cockpit.read_tune_status's `done` shape (Task R1). Kept
// local rather than in lib/types.ts per this task's scope (frontend-only).
interface TuneConfigResult {
  label: string;
  num_gpu: number | null;
  median_tps: number;
  runs: number[];
}
type TuneStatus =
  | { state: "idle" }
  | { state: "running"; started_at?: string }
  | {
      state: "done";
      layers: number | null;
      results: TuneConfigResult[];
      winner: TuneConfigResult;
      baseline_tps: number | null;
    }
  | { state: "failed"; message: string }
  | { state: "not_licensed" };

type ApplyResult =
  | { state: "applied"; tuned_model: string }
  | { state: "failed"; message: string }
  | { state: "not_licensed" };

/** Plain-English label for one sweep config row. */
function rowLabel(r: TuneConfigResult, layers: number | null): string {
  if (r.num_gpu === null) return "auto (Ollama decides)";
  if (layers != null && r.num_gpu === layers) return `all-${layers} · full GPU offload`;
  return `${r.num_gpu} layers · partial offload`;
}

/** Per-run tok/s spread as a % of the median (guards against a zero median). */
function spreadPct(r: TuneConfigResult): number {
  if (!r.runs.length || !r.median_tps) return 0;
  const max = Math.max(...r.runs);
  const min = Math.min(...r.runs);
  return Math.round(((max - min) / r.median_tps) * 100);
}

export function TuneHero() {
  const installed = useAsync(() => api.installed());
  const models = installed.data ?? [];

  const [model, setModel] = useState("");
  const [status, setStatus] = useState<TuneStatus>({ state: "idle" });
  const [started, setStarted] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [applyState, setApplyState] = useState<Record<string, ApplyResult | "pending">>({});

  // Default the select to the first installed model once the list arrives.
  useEffect(() => {
    if (!model && models.length > 0) setModel(models[0].name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models.length]);

  // Warm the selected model off the critical path so the sweep measures its
  // real speed, not the one-time cold load.
  useEffect(() => {
    if (model) api.warm(model);
  }, [model]);

  // Expand the winner row by default whenever a sweep completes.
  useEffect(() => {
    if (status.state === "done") setExpanded(status.winner.label);
  }, [status]);

  const polling = started && (status.state === "idle" || status.state === "running");

  useInterval(() => {
    if (!model) return;
    api.proTuneStatus(model).then((s: TuneStatus) => setStatus(s));
  }, polling ? 2000 : null);

  function handleModelChange(v: string) {
    setModel(v);
    setStatus({ state: "idle" });
    setStarted(false);
    setExpanded(null);
    setApplyState({});
  }

  async function runSweep() {
    if (!model) return;
    setExpanded(null);
    setApplyState({});
    setStarted(true);
    setStatus({ state: "running" });
    try {
      const res: { accepted?: boolean; state?: string } = await api.proTune(model);
      if (res?.state === "not_licensed") {
        setStarted(false);
        setStatus({ state: "not_licensed" });
        return;
      }
    } catch (e) {
      setStarted(false);
      setStatus({ state: "failed", message: e instanceof Error ? e.message : String(e) });
      return;
    }
    // Kick an immediate status read so the UI doesn't sit idle for a full 2s
    // before the interval below picks it up.
    try {
      const s: TuneStatus = await api.proTuneStatus(model);
      setStatus(s);
    } catch {
      /* interval retries */
    }
  }

  async function applyRow(row: TuneConfigResult) {
    if (row.num_gpu == null) return;
    const key = row.label;
    setApplyState((prev) => ({ ...prev, [key]: "pending" }));
    try {
      const res: ApplyResult = await api.proTuneApply(model, row.num_gpu, undefined);
      setApplyState((prev) => ({ ...prev, [key]: res }));
    } catch (e) {
      setApplyState((prev) => ({
        ...prev,
        [key]: { state: "failed", message: e instanceof Error ? e.message : String(e) },
      }));
    }
  }

  const maxTps = status.state === "done" ? Math.max(...status.results.map((r) => r.median_tps), 1) : 1;
  const hasDelta = status.state === "done" && status.baseline_tps != null && status.baseline_tps > 0;
  const deltaPct =
    status.state === "done" && hasDelta
      ? Math.round(((status.winner.median_tps - (status.baseline_tps as number)) / (status.baseline_tps as number)) * 100)
      : null;

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Gauge className="h-4 w-4 text-verdant" /> Tune
        </div>
        {installed.loading ? (
          <Skeleton className="h-9 w-64" />
        ) : models.length > 0 ? (
          <div className="flex items-center gap-2">
            <Select value={model} onValueChange={handleModelChange} disabled={status.state === "running"}>
              <SelectTrigger className="h-9 w-[220px]">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m.name} value={m.name}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" onClick={runSweep} disabled={!model || status.state === "running"}>
              {status.state === "running" ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…
                </>
              ) : (
                "Run sweep"
              )}
            </Button>
          </div>
        ) : null}
      </div>

      {installed.loading ? (
        <Skeleton className="h-20 w-full" />
      ) : models.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Install a model first, then come back here to tune it.</p>
      ) : status.state === "idle" ? (
        <p className="text-[13px] text-fg-muted">
          Pick a model and run a sweep to benchmark GPU-offload configs on your exact hardware.
        </p>
      ) : status.state === "not_licensed" ? (
        <p className="text-[13px] text-fg-muted">LAC Pro license required to tune models.</p>
      ) : status.state === "running" ? (
        <div className="flex items-center gap-2 text-[13px] text-fg-muted">
          <Loader2 className="h-4 w-4 animate-spin text-verdant" />
          Benchmarking offload configs on your hardware…
        </div>
      ) : status.state === "failed" ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger-soft bg-danger-soft p-3">
          <span className="text-[13px] text-danger">{status.message}</span>
          <Button size="sm" variant="secondary" onClick={runSweep}>
            Retry
          </Button>
        </div>
      ) : (
        <>
          {/* Before → after hero */}
          <div className="mb-5 rounded-lg border border-line bg-panel-2 p-4">
            <div className="flex flex-wrap items-baseline gap-3">
              {hasDelta && (
                <>
                  <span className="font-mono text-lg text-fg-muted">{(status.baseline_tps as number).toFixed(1)}</span>
                  <span className="text-fg-faint">→</span>
                </>
              )}
              <span className="font-mono text-2xl font-semibold text-verdant">
                {status.winner.median_tps.toFixed(1)}
              </span>
              <span className="text-[13px] text-fg-muted">tok/s</span>
              {deltaPct != null && (
                <Badge variant="accent">
                  {deltaPct >= 0 ? "+" : ""}
                  {deltaPct}%
                </Badge>
              )}
            </div>
            <div className="mt-1.5 text-[12px] text-fg-muted">
              Winner: <span className="text-fg">{rowLabel(status.winner, status.layers)}</span>
            </div>
          </div>

          {/* Per-config table */}
          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
                <tr>
                  <th className="px-4 py-2 text-left font-semibold">Config</th>
                  <th className="px-4 py-2 text-left font-semibold">Throughput</th>
                  <th className="px-4 py-2 text-right font-semibold">tok/s</th>
                  <th className="px-4 py-2 text-right font-semibold"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {status.results.map((r) => {
                  const isWinner = r.label === status.winner.label && r.num_gpu === status.winner.num_gpu;
                  const isOpen = expanded === r.label;
                  const pct = Math.max(0, Math.min(100, Math.round((r.median_tps / maxTps) * 100)));
                  const outcome = applyState[r.label];
                  return (
                    <React.Fragment key={r.label}>
                      <tr className={cn("transition-colors", isWinner ? "bg-verdant-soft" : "hover:bg-panel-3/40")}>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <button
                              aria-label={isOpen ? "Hide detail" : "Show detail"}
                              onClick={() => setExpanded(isOpen ? null : r.label)}
                              className="text-fg-faint hover:text-fg"
                            >
                              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            </button>
                            <span className={cn("text-[13px] font-medium", isWinner ? "text-verdant" : "text-fg")}>
                              {rowLabel(r, status.layers)}
                            </span>
                            {isWinner && <Badge variant="accent">winner</Badge>}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="h-1.5 w-full max-w-[180px] overflow-hidden rounded-pill bg-panel-3">
                            <div
                              className={cn("h-full rounded-pill", isWinner ? "bg-verdant" : "bg-fg-muted")}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                          {r.median_tps.toFixed(1)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {r.num_gpu === null ? (
                            <span className="text-[11px] text-fg-faint">Ollama's automatic split is already optimal</span>
                          ) : (
                            <div className="flex flex-col items-end gap-1">
                              <Button
                                size="sm"
                                variant={isWinner ? "primary" : "secondary"}
                                disabled={outcome === "pending"}
                                onClick={() => applyRow(r)}
                              >
                                {outcome === "pending" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Apply"}
                              </Button>
                              {outcome && outcome !== "pending" && outcome.state === "applied" && (
                                <span className="max-w-[220px] text-right text-[11px] text-success">
                                  Created <code className="font-mono">{outcome.tuned_model}</code> — pick it in Chat/Installed
                                </span>
                              )}
                              {outcome && outcome !== "pending" && outcome.state === "failed" && (
                                <span className="max-w-[220px] text-right text-[11px] text-danger">{outcome.message}</span>
                              )}
                              {outcome && outcome !== "pending" && outcome.state === "not_licensed" && (
                                <span className="max-w-[220px] text-right text-[11px] text-danger">
                                  LAC Pro license required.
                                </span>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-panel-2/60">
                          <td colSpan={4} className="px-4 py-3">
                            <div className="flex flex-wrap items-center gap-4 text-[12px] text-fg-muted">
                              <span>
                                num_gpu: <span className="font-mono text-fg">{r.num_gpu ?? "auto"}</span>
                              </span>
                              <span>
                                runs:{" "}
                                <span className="font-mono text-fg">{r.runs.map((v) => v.toFixed(1)).join(" · ")}</span>
                              </span>
                              <span>
                                spread: <span className="font-mono text-fg">{spreadPct(r)}%</span>
                              </span>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
