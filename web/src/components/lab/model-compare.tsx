import { useEffect, useMemo, useRef, useState } from "react";
import { FlaskConical, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, EmptyState } from "@/components/page";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import {
  measurementsShareProtocol,
  type LabComparableMeasurement,
  type LabMetricKey,
} from "@/lib/lab-compare";
import type { OllamaModelProfile, PerformanceMetrics } from "@/lib/types";

interface ProbeState {
  status: "running" | "done" | "error";
  metrics?: PerformanceMetrics;
  error?: string;
}

interface MetricRow {
  key: LabMetricKey;
  label: string;
  format: (value: number | null | undefined) => string;
}

const METRICS: MetricRow[] = [
  { key: "tokens_per_second", label: "Generation rate", format: formatTps },
  { key: "pre_generation_ms", label: "Pre-generation", format: formatMs },
  { key: "load_duration_ms", label: "Load", format: formatMs },
  { key: "prompt_eval_duration_ms", label: "Prompt evaluation", format: formatMs },
  { key: "total_duration_ms", label: "Total", format: formatMs },
];

function formatMs(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "Not measured";
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)} s`;
  return `${Math.round(value)} ms`;
}

function formatTps(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value <= 0) return "Not measured";
  return `${value.toFixed(value >= 100 ? 0 : 1)} tok/s`;
}

function reported(value: string | number | null | undefined): string {
  if (value == null || value === "") return "Not reported";
  return String(value);
}

function modelRows(
  selection: readonly string[],
  probes: Record<string, ProbeState | undefined>,
): LabComparableMeasurement[] {
  return selection.flatMap((model) => {
    const metrics = probes[model]?.metrics;
    if (!metrics) return [];
    return [{
      model,
      protocol_id: metrics.protocol_id,
      num_ctx: metrics.num_ctx,
      tokens_per_second: metrics.tokens_per_second,
      pre_generation_ms: metrics.time_to_first_token_ms,
      load_duration_ms: metrics.load_duration_ms,
      prompt_eval_duration_ms: metrics.prompt_eval_duration_ms,
      total_duration_ms: metrics.total_duration_ms,
    }];
  });
}

export function ModelCompare() {
  const installed = useAsync(() => api.installed());
  const running = useAsync(() => api.ps());
  const [selection, setSelection] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<Record<string, OllamaModelProfile>>({});
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [profilesError, setProfilesError] = useState("");
  const [probes, setProbes] = useState<Record<string, ProbeState | undefined>>({});
  const [probing, setProbing] = useState(false);
  const profileGeneration = useRef(0);
  const probeGeneration = useRef(0);

  const installedNames = useMemo(
    () => [...(installed.data ?? [])]
      .sort((left, right) => left.size_gb - right.size_gb || left.name.localeCompare(right.name))
      .map((model) => model.name),
    [installed.data],
  );
  const selectionKey = selection.join("\u0000");

  useEffect(() => {
    setSelection((current) => {
      const next = current.filter((name) => installedNames.includes(name)).slice(0, 2);
      for (const name of installedNames) {
        if (next.length >= 2) break;
        if (!next.includes(name)) next.push(name);
      }
      return next.length === current.length && next.every((name, index) => name === current[index])
        ? current
        : next;
    });
  }, [installedNames]);

  useEffect(() => {
    const generation = ++profileGeneration.current;
    ++probeGeneration.current;
    setProbes({});
    setProbing(false);
    if (selection.length !== 2) {
      setProfiles({});
      setProfilesError("");
      setProfilesLoading(false);
      return;
    }

    setProfilesLoading(true);
    setProfilesError("");
    void api.modelProfiles(selection)
      .then((response) => {
        if (profileGeneration.current !== generation) return;
        setProfiles(Object.fromEntries(response.profiles.map((profile) => [profile.name, profile])));
      })
      .catch((error) => {
        if (profileGeneration.current !== generation) return;
        setProfiles({});
        setProfilesError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (profileGeneration.current === generation) setProfilesLoading(false);
      });
  // selectionKey intentionally captures the ordered model identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey]);

  const runningNames = new Set((running.data?.models ?? []).map((model) => model.name));
  const measurements = modelRows(selection, probes);
  const protocolComparable = measurementsShareProtocol(measurements);
  const selectedDigests = selection.map((model) => profiles[model]?.digest?.trim() ?? "");
  const profilesLoaded = selection.length === 2
    && selection.every((model) => profiles[model]?.name === model);
  const identityError = profilesLoaded && selectedDigests.some((digest) => !digest)
    ? "Exact manifest identity was not reported for both tags."
    : profilesLoaded && new Set(selectedDigests).size !== selectedDigests.length
      ? "Same model manifest under two tags. Select two distinct model identities."
      : "";
  const profilesReady = profilesLoaded && !identityError;

  const selectModel = (index: number, model: string) => {
    setSelection((current) => {
      const otherIndex = index === 0 ? 1 : 0;
      if (current[otherIndex] === model) {
        const swapped = [...current];
        swapped[index] = model;
        swapped[otherIndex] = current[index];
        return swapped;
      }
      const next = [...current];
      next[index] = model;
      return next.filter(Boolean).slice(0, 2);
    });
  };

  const runComparison = async () => {
    if (selection.length !== 2 || probing) return;
    const generation = ++probeGeneration.current;
    const models = [...selection];
    setProbing(true);

    for (const model of models) {
      if (probeGeneration.current !== generation) break;
      setProbes((current) => ({ ...current, [model]: { status: "running" } }));
      try {
        const response = await api.performanceProbe(model);
        if (probeGeneration.current !== generation) break;
        if (response.state !== "done" || !response.metrics) {
          throw new Error(response.error ?? "The Ollama probe did not return a measurement.");
        }
        setProbes((current) => ({
          ...current,
          [model]: { status: "done", metrics: response.metrics },
        }));
      } catch (error) {
        if (probeGeneration.current !== generation) break;
        setProbes((current) => ({
          ...current,
          [model]: {
            status: "error",
            error: error instanceof Error ? error.message : String(error),
          },
        }));
      }
    }

    if (probeGeneration.current === generation) {
      await running.reload();
      if (probeGeneration.current === generation) setProbing(false);
    }
  };

  if (installed.error) {
    return <ErrorState message={`Could not load installed models: ${installed.error}`} onRetry={installed.reload} />;
  }
  if (installed.loading && !installed.data) return <CompareSkeleton />;
  if (installedNames.length < 2) {
    return (
      <EmptyState
        icon={<FlaskConical className="h-8 w-8" />}
        title="Two configured models required"
        hint="Install one more model in the configured Ollama endpoint to compare exact manifest identity."
      />
    );
  }

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <div className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
          {[0, 1].map((index) => (
            <div key={index} className="block min-w-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              <span id={`lab-model-${index}-label`}>{index === 0 ? "Baseline model" : "Comparison model"}</span>
              <Select
                value={selection[index] ?? ""}
                disabled={probing}
                onValueChange={(value) => selectModel(index, value)}
              >
                <SelectTrigger aria-labelledby={`lab-model-${index}-label`} className="mt-1.5 w-full normal-case tracking-normal">
                  <SelectValue placeholder="Select installed model" />
                </SelectTrigger>
                <SelectContent>
                  {installedNames.map((name) => (
                    <SelectItem key={name} value={name}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
          <Button
            onClick={runComparison}
            disabled={selection.length !== 2 || probing || profilesLoading || Boolean(profilesError) || !profilesReady}
          >
            {probing ? <RefreshCw className="animate-spin" /> : <Play />}
            {probing ? "Running sequentially" : "Run comparison"}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-panel-2/60 px-4 py-3 text-[12px] text-fg-muted">
          <ShieldCheck className="h-4 w-4 text-success" />
          <strong className="text-fg">One diagnostic sample, not a quality benchmark.</strong>
          <span>Models run sequentially to avoid simultaneous generation. Ollama controls residency. Prompts and results are not saved.</span>
        </div>
      </Card>

      {profilesError ? <ErrorState message={`Could not read exact model profiles: ${profilesError}`} /> : null}
      {identityError ? (
        <div role="alert" className="rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-[12px] text-warning">
          {identityError}
        </div>
      ) : null}

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] table-fixed text-left text-[12.5px]">
            <caption className="sr-only">Exact installed model identity and current-session measurements</caption>
            <thead className="border-b border-line bg-panel-2">
              <tr>
                <th scope="col" className="w-[190px] px-4 py-3 text-[10px] uppercase tracking-[0.08em] text-fg-faint">Evidence</th>
                {selection.map((model) => (
                  <th key={model} scope="col" className="px-4 py-3 align-top">
                    <div className="break-all font-mono text-[13px] text-fg">{model}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {running.loading && !running.data ? (
                        <Badge variant="neutral">residency loading</Badge>
                      ) : running.error ? (
                        <Badge variant="neutral">residency not reported</Badge>
                      ) : (
                        <Badge variant={runningNames.has(model) ? "success" : "neutral"} dot>
                          {runningNames.has(model) ? "resident" : "not resident"}
                        </Badge>
                      )}
                      {probes[model]?.status === "running" ? <Badge variant="info">measuring</Badge> : null}
                      {probes[model]?.status === "error" ? <Badge variant="danger">probe failed</Badge> : null}
                    </div>
                    {probes[model]?.error ? (
                      <p role="alert" className="mt-2 break-words text-[11.5px] font-normal text-danger">{probes[model]?.error}</p>
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line/70">
              <ProfileRow label="Manifest digest" values={selection.map((model) => profiles[model]?.digest)} mono />
              <ProfileRow label="Format" values={selection.map((model) => profiles[model]?.format)} />
              <ProfileRow label="Family / architecture" values={selection.map((model) => profiles[model]?.family)} />
              <ProfileRow label="Parameters" values={selection.map((model) => profiles[model]?.parameter_size)} />
              <ProfileRow label="Quantization" values={selection.map((model) => profiles[model]?.quantization_level)} />
              <ProfileRow label="Capability context" values={selection.map((model) => {
                const context = profiles[model]?.context_length;
                return context ? `${context.toLocaleString()} tokens` : null;
              })} />
              <ProfileRow label="Storage" values={selection.map((model) => {
                const size = profiles[model]?.size_gb;
                return size != null ? `${size.toFixed(size < 10 ? 1 : 0)} GB` : null;
              })} />
              <ProfileRow label="Runtime" values={selection.map(() => "Ollama")} />
              <ProfileRow label="Compute backend" values={selection.map(() => null)} />
              {METRICS.map((metric) => (
                <MeasurementRow
                  key={metric.key}
                  metric={metric}
                  selection={selection}
                  measurements={measurements}
                />
              ))}
              <ProfileRow label="Protocol" values={selection.map((model) => probes[model]?.metrics?.protocol_id ?? null)} mono missing="Not measured" />
              <ProfileRow label="Context used" values={selection.map((model) => {
                const context = probes[model]?.metrics?.num_ctx;
                return context ? `${context.toLocaleString()} tokens` : null;
              })} missing="Not measured" />
            </tbody>
          </table>
        </div>
      </Card>

      {measurements.length === 2 && !protocolComparable ? (
        <div role="status" className="rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-[12px] text-warning">
          Not directly comparable: both measurements must share the same non-empty protocol and context configuration.
        </div>
      ) : measurements.length === 2 ? (
        <div role="status" className="rounded-md border border-line bg-panel-2 px-3 py-2 text-[12px] text-fg-muted">
          No ranking: Ollama residency and warm/cold state are not controlled. Values are raw current-session samples.
        </div>
      ) : (
        <div className="text-[11.5px] text-fg-faint">
          Raw measurements appear after both probes finish. Pre-generation is Ollama load plus prompt evaluation, not wall-clock first-token latency.
        </div>
      )}
    </div>
  );
}

function ProfileRow({
  label,
  values,
  mono = false,
  missing = "Not reported",
}: {
  label: string;
  values: readonly (string | number | null | undefined)[];
  mono?: boolean;
  missing?: string;
}) {
  return (
    <tr>
      <th scope="row" className="px-4 py-3 font-medium text-fg-muted">{label}</th>
      {values.map((value, index) => (
        <td key={index} className={`break-all px-4 py-3 align-top ${mono ? "font-mono text-[11px]" : ""}`}>
          {value == null || value === "" ? missing : reported(value)}
        </td>
      ))}
    </tr>
  );
}

function MeasurementRow({
  metric,
  selection,
  measurements,
}: {
  metric: MetricRow;
  selection: readonly string[];
  measurements: readonly LabComparableMeasurement[];
}) {
  return (
    <tr>
      <th scope="row" className="px-4 py-3 font-medium text-fg-muted">{metric.label}</th>
      {selection.map((model) => {
        const measurement = measurements.find((row) => row.model === model);
        return (
          <td key={model} className="px-4 py-3 align-top">
            <span className="font-mono">{metric.format(measurement?.[metric.key])}</span>
          </td>
        );
      })}
    </tr>
  );
}

function CompareSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-[420px] w-full" />
    </div>
  );
}
