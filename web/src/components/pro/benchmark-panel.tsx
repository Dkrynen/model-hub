import { useCallback, useEffect, useState } from "react";
import { Timer, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";

interface BenchmarkRun {
  tokens_per_second: number;
  time_to_first_token_ms: number;
  timestamp: number;
}

interface BenchmarkHistoryResponse {
  state?: string;
  runs?: BenchmarkRun[];
}

const POLL_MS = 3000;
const MAX_POLLS = 10; // bounded — 30s of polling, then give up quietly

/** Compact "Nh ago" / "Nd ago" from a Unix epoch-seconds timestamp. */
function relativeTime(epochSeconds: number): string {
  const diffMs = Date.now() - epochSeconds * 1000;
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

export function BenchmarkPanel() {
  const installed = useAsync(() => api.installed());
  const models = installed.data ?? [];

  const [model, setModel] = useState("");
  const [history, setHistory] = useState<BenchmarkHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [pollCount, setPollCount] = useState(0);
  const [baselineTimestamp, setBaselineTimestamp] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // Warm the selected model off the critical path so "Benchmark now" measures
  // its real per-message speed, not the one-time cold load.
  useEffect(() => {
    if (model) api.warm(model);
  }, [model]);

  // Default the select to the first installed model once the list arrives.
  useEffect(() => {
    if (!model && models.length > 0) setModel(models[0].name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models.length]);

  const loadHistory = useCallback(async (m: string) => {
    try {
      const res: BenchmarkHistoryResponse = await api.proBenchmarkHistory(m);
      setHistory(res);
      return res;
    } catch {
      setHistory(null);
      return null;
    }
  }, []);

  // Fresh history whenever the selected model changes.
  useEffect(() => {
    if (!model) return;
    setRunning(false);
    setNote(null);
    setHistoryLoading(true);
    loadHistory(model).finally(() => setHistoryLoading(false));
  }, [model, loadHistory]);

  const polling = running && pollCount < MAX_POLLS;

  useInterval(() => {
    if (!model) return;
    const nextCount = pollCount + 1;
    setPollCount(nextCount);
    loadHistory(model).then((res) => {
      const runs = res?.state === "ok" ? res.runs ?? [] : [];
      const newest = runs[0]?.timestamp ?? null;
      if (newest != null && newest !== baselineTimestamp) {
        setRunning(false);
        setNote(null);
        return;
      }
      if (nextCount >= MAX_POLLS) {
        // Cap reached with no new run visible yet — unstick the controls
        // rather than leaving "Benchmarking…" disabled forever.
        setRunning(false);
        setNote("Still benchmarking — check back in a moment.");
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, polling ? POLL_MS : null);

  function handleModelChange(v: string) {
    setModel(v);
  }

  async function runBenchmark() {
    if (!model || running) return;
    const current = history?.state === "ok" ? history.runs?.[0]?.timestamp ?? null : null;
    setBaselineTimestamp(current);
    setPollCount(0);
    setNote(null);
    setRunning(true);
    try {
      await api.proBenchmark(model);
    } catch {
      setRunning(false);
      setNote("Couldn't start the benchmark — try again.");
    }
  }

  const runs = history?.state === "ok" ? history.runs ?? [] : [];
  const latest = runs[0];

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Timer className="h-4 w-4 text-verdant" /> Benchmark
        </div>
        {installed.loading ? (
          <Skeleton className="h-9 w-64" />
        ) : models.length > 0 ? (
          <div className="flex items-center gap-2">
            <Select value={model} onValueChange={handleModelChange} disabled={running}>
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
            <Button size="sm" onClick={runBenchmark} disabled={!model || running}>
              {running ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Benchmarking…
                </>
              ) : (
                "Benchmark now"
              )}
            </Button>
          </div>
        ) : null}
      </div>

      {note ? <p className="mb-3 text-[12px] text-fg-muted">{note}</p> : null}

      {installed.loading ? (
        <Skeleton className="h-20 w-full" />
      ) : models.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Install a model first, then come back here to benchmark it.</p>
      ) : historyLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : runs.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Run a benchmark to measure this model's speed on your hardware.</p>
      ) : (
        <>
          <div className="mb-4 rounded-lg border border-line bg-panel-2 p-4">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="font-mono text-2xl font-semibold text-verdant">{latest.tokens_per_second.toFixed(1)}</span>
              <span className="text-[13px] text-fg-muted">tok/s</span>
              <span className="text-fg-faint">·</span>
              <span className="font-mono text-lg text-fg">{Math.round(latest.time_to_first_token_ms)}</span>
              <span className="text-[13px] text-fg-muted">ms to first token</span>
            </div>
            <div className="mt-1.5 text-[12px] text-fg-faint">{relativeTime(latest.timestamp)}</div>
          </div>

          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
                <tr>
                  <th className="px-4 py-2 text-right font-semibold">tok/s</th>
                  <th className="px-4 py-2 text-right font-semibold">TTFT (ms)</th>
                  <th className="px-4 py-2 text-right font-semibold">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {runs.slice(0, 5).map((r, i) => (
                  <tr key={`${r.timestamp}-${i}`} className="transition-colors hover:bg-panel-3/40">
                    <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                      {r.tokens_per_second.toFixed(1)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                      {Math.round(r.time_to_first_token_ms)}
                    </td>
                    <td className="px-4 py-3 text-right text-[12px] text-fg-faint">{relativeTime(r.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
