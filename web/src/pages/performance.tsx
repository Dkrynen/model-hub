import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Activity, Clock3, Gauge, Play, RefreshCw, Zap } from "lucide-react";
import { PageHeader, ErrorState, EmptyState } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import type { PerformanceDiagnosis, PerformanceMetrics, PerformanceProbeResponse, PerformanceSignal } from "@/lib/types";

function fmtMs(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return "-";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)} s`;
  return `${Math.round(value)} ms`;
}

function fmtTps(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return "-";
  return `${value.toFixed(value >= 100 ? 0 : 1)} tok/s`;
}

function severityVariant(severity: PerformanceSignal["severity"]): BadgeProps["variant"] {
  if (severity === "success") return "success";
  if (severity === "danger") return "danger";
  if (severity === "warning") return "warning";
  return "info";
}

function stateVariant(state: PerformanceDiagnosis["state"]): BadgeProps["variant"] {
  if (state === "ok") return "success";
  if (state === "slow") return "danger";
  if (state === "watch") return "warning";
  return "neutral";
}

export function Performance({ embedded = false }: { embedded?: boolean }) {
  const [model, setModel] = useState("");
  const [probe, setProbe] = useState<PerformanceProbeResponse | null>(null);
  const [probing, setProbing] = useState(false);
  const probeGeneration = useRef(0);
  const diagnostics = useAsync(() => api.performanceDiagnostics(model || undefined), [model]);

  useEffect(() => {
    const first = diagnostics.data?.installed_models?.[0];
    if (!model && first) setModel(first);
  }, [diagnostics.data?.installed_models, model]);

  const metrics = probe?.metrics ?? diagnostics.data?.latest ?? null;
  const diagnosis = probe?.diagnosis ?? diagnostics.data?.diagnosis ?? null;
  const installed = diagnostics.data?.installed_models ?? [];
  const installedReported = diagnostics.data?.installed_models_reported === true;
  const running = diagnostics.data?.running_models ?? [];
  const runningReported = diagnostics.data?.running_models_reported === true;

  async function runProbe() {
    if (!model) return;
    const generation = ++probeGeneration.current;
    const requestedModel = model;
    setProbing(true);
    setProbe(null);
    try {
      const result = await api.performanceProbe(requestedModel);
      if (probeGeneration.current !== generation) return;
      setProbe(result);
      void diagnostics.reload();
    } catch (error) {
      if (probeGeneration.current !== generation) return;
      setProbe({
        model: requestedModel,
        state: "failed",
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      if (probeGeneration.current === generation) setProbing(false);
    }
  }

  return (
    <>
      {embedded ? (
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-[16px] font-semibold">Performance Doctor</h2>
            <p className="mt-1 text-[12.5px] text-fg-muted">Ollama warmup, pre-generation, and generation health.</p>
          </div>
          <Button size="sm" variant="secondary" onClick={diagnostics.reload}>
            <RefreshCw /> Refresh
          </Button>
        </div>
      ) : (
        <PageHeader title="Performance Doctor" subtitle="Ollama warmup, pre-generation, and generation health.">
          <Button size="sm" variant="secondary" onClick={diagnostics.reload}>
            <RefreshCw /> Refresh
          </Button>
        </PageHeader>
      )}

      <Card className="mb-4 flex flex-wrap items-center gap-2 p-2.5">
        <Activity className="ml-1 h-4 w-4 text-fg-muted" />
        <Select
          value={model}
          disabled={probing}
          onValueChange={(value) => {
            ++probeGeneration.current;
            setModel(value);
            setProbe(null);
          }}
        >
          <SelectTrigger className="h-9 min-w-[260px] flex-1">
            <SelectValue placeholder="Select installed model" />
          </SelectTrigger>
          <SelectContent>
            {installed.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={runProbe} disabled={!model || probing}>
          <Play /> {probing ? "Running" : "Run probe"}
        </Button>
        {diagnostics.loading && !diagnostics.data ? (
          <Badge variant="neutral">residency loading</Badge>
        ) : diagnostics.error || !runningReported ? (
          <Badge variant="neutral">residency not reported</Badge>
        ) : model && running.includes(model) ? (
          <Badge variant="success">resident</Badge>
        ) : (
          <Badge variant="neutral">not resident</Badge>
        )}
      </Card>

      {diagnostics.error ? (
        <ErrorState message={`Could not load diagnostics: ${diagnostics.error}`} onRetry={diagnostics.reload} />
      ) : diagnostics.loading ? (
        <PerformanceSkeleton />
      ) : !installedReported ? (
        <ErrorState message="Ollama model inventory is unavailable." onRetry={diagnostics.reload} />
      ) : installed.length === 0 ? (
        <EmptyState title="No installed models" hint="Install a model before measuring latency." />
      ) : probe?.state === "failed" ? (
        <ErrorState message={probe.error ?? "Probe failed"} onRetry={runProbe} />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-4">
            <MetricCard icon={<Clock3 />} label="Pre-generation" value={fmtMs(metrics?.time_to_first_token_ms)} />
            <MetricCard icon={<Zap />} label="Generation" value={fmtTps(metrics?.tokens_per_second)} />
            <MetricCard icon={<Gauge />} label="Load" value={fmtMs(metrics?.load_duration_ms)} />
            <MetricCard icon={<Activity />} label="Prompt prefill" value={fmtMs(metrics?.prompt_eval_duration_ms)} />
          </div>

          <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-[1.2fr_0.8fr]">
            <Card className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">Diagnosis</h3>
                {diagnosis ? <Badge variant={stateVariant(diagnosis.state)}>{diagnosis.state}</Badge> : null}
                {probe?.metrics ? <Badge variant="accent">live probe</Badge> : diagnostics.data?.latest ? <Badge variant="neutral">history</Badge> : null}
              </div>
              <p className="mt-2 text-[13px] text-fg-muted">{diagnosis?.summary ?? "No Ollama measurement yet."}</p>
              <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
                {(diagnosis?.signals ?? []).map((signal) => (
                  <div key={`${signal.kind}-${signal.label}`} className="rounded border border-line bg-panel-2 p-3">
                    <Badge variant={severityVariant(signal.severity)}>{signal.severity}</Badge>
                    <div className="mt-2 text-[13px] font-medium text-fg">{signal.label}</div>
                    <div className="mt-1 font-mono text-[12px] text-fg-muted">
                      {signal.value_ms != null ? fmtMs(signal.value_ms) : fmtTps(signal.tokens_per_second)}
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-4">
              <h3 className="text-sm font-semibold">Actions</h3>
              <div className="mt-3 space-y-2">
                {(diagnosis?.actions ?? []).map((action) => (
                  <div key={`${action.kind}-${action.label}`} className="rounded border border-line bg-panel-2 px-3 py-2 text-[13px] text-fg-muted">
                    {action.label}
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <Card className="p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Recent Measurements</h3>
              <Badge variant="neutral">{diagnostics.data?.history.length ?? 0} records</Badge>
            </div>
            {(diagnostics.data?.history.length ?? 0) === 0 ? (
              <div className="text-[13px] text-fg-muted">No saved Pro benchmark history for this model.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] text-left text-[12.5px]">
                  <caption className="sr-only">Saved Pro benchmark history for the selected model</caption>
                  <thead className="border-b border-line text-[10px] uppercase tracking-[0.08em] text-fg-faint">
                    <tr>
                      <th className="pb-2 font-medium">Source</th>
                      <th className="pb-2 font-medium">Pre-generation</th>
                      <th className="pb-2 font-medium">Tokens/sec</th>
                      <th className="pb-2 font-medium">Load</th>
                      <th className="pb-2 font-medium">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diagnostics.data?.history.map((row, index) => (
                      <tr key={`${row.timestamp ?? index}-${row.model}`} className="border-b border-line/60 last:border-0">
                        <td className="py-2 font-mono text-fg-muted">{row.source ?? "pro benchmark"}</td>
                        <td className="py-2 font-mono">{fmtMs(row.time_to_first_token_ms)}</td>
                        <td className="py-2 font-mono">{fmtTps(row.tokens_per_second)}</td>
                        <td className="py-2 font-mono">{fmtMs(row.load_duration_ms)}</td>
                        <td className="py-2 font-mono">{fmtMs(row.total_duration_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}

function MetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-fg-muted">
        <span className="[&_svg]:h-4 [&_svg]:w-4">{icon}</span>
        <span className="text-[11px] uppercase tracking-[0.08em]">{label}</span>
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold text-fg">{value}</div>
    </Card>
  );
}

function PerformanceSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Card key={i} className="h-[112px] p-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-4 h-7 w-20" />
        </Card>
      ))}
    </div>
  );
}
