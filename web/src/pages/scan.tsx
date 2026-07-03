import React, { useState } from "react";
import { Cpu, MemoryStick, HardDrive, Microchip, Gauge, ChevronDown, ChevronRight, Layers } from "lucide-react";
import { PageHeader, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { BenchmarkDialog } from "@/components/benchmark-dialog";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { fmtParams, fmtContext } from "@/lib/utils";
import { pullWithToast } from "@/lib/installer";

const USE_CASES = [
  { v: "coding", l: "Coding" },
  { v: "chat", l: "Chat" },
  { v: "reasoning", l: "Reasoning" },
  { v: "vision", l: "Vision" },
  { v: "writing", l: "Writing" },
  { v: "general", l: "General" },
];

export function Scan() {
  const scan = useAsync(() => api.scan());
  const [useCase, setUseCase] = useState("coding");
  const [manualVram, setManualVram] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [disabledGpus, setDisabledGpus] = useState<Set<number>>(new Set());
  const [allowSpill, setAllowSpill] = useState(true);

  const detectedVram = scan.data?.total_vram_gb ?? 0;
  const effectiveVram = manualVram > 0 ? manualVram : detectedVram;

  const gpuMask =
    scan.data && disabledGpus.size > 0
      ? scan.data.gpus.map((g) => g.device_index).filter((i) => !disabledGpus.has(i))
      : undefined;

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

  return (
    <>
      <PageHeader
        title="Scan & recommend"
        subtitle="Hardware profile and models ranked to fit it."
      />

      {/* Hardware */}
      {scan.error ? (
        <ErrorState message={`Couldn’t scan hardware: ${scan.error}`} onRetry={scan.reload} />
      ) : scan.loading || !scan.data ? (
        <Card className="p-4">
          <Skeleton className="h-5 w-40" />
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        </Card>
      ) : (
        <Card className="p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Cpu className="h-4 w-4 text-iris" /> System
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat icon={<Microchip />} label="CPU" value={scan.data.cpu} sub={`${scan.data.cores} cores`} />
            <Stat icon={<MemoryStick />} label="RAM" value={`${scan.data.ram_gb.toFixed(0)} GB`} />
            <Stat icon={<HardDrive />} label="Total VRAM" value={`${scan.data.total_vram_gb} GB`} />
            <Stat
              icon={<Cpu />}
              label="GPU"
              value={scan.data.gpus[0]?.name ?? "—"}
              sub={scan.data.gpus[0]?.backend}
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {scan.data.gpus.map((g, i) => (
              <Badge key={i} variant="neutral">
                {g.name} · {g.vram_gb} GB · {g.backend}
              </Badge>
            ))}
          </div>
        </Card>
      )}

      {/* Controls */}
      <Card className="mt-4 flex flex-wrap items-end gap-4 p-4">
        <div>
          <label className="mb-1.5 block text-[12px] font-medium text-fg-muted">Use case</label>
          <Select value={useCase} onValueChange={setUseCase}>
            <SelectTrigger className="h-9 w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {USE_CASES.map((u) => (
                <SelectItem key={u.v} value={u.v}>
                  {u.l}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1">
          <label className="mb-1.5 flex items-center justify-between text-[12px] font-medium text-fg-muted">
            <span>Override VRAM (GB)</span>
            <span className="font-mono text-fg">{manualVram || detectedVram} GB</span>
          </label>
          <input
            type="range"
            min={0}
            max={256}
            step={1}
            value={manualVram}
            onChange={(e) => setManualVram(Number(e.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-pill bg-panel-3 accent-iris"
          />
          <div className="mt-1 flex justify-between text-[10px] text-fg-faint">
            <span>0</span><span>64</span><span>128</span><span>192</span><span>256</span>
          </div>
          {manualVram > 0 && (
            <button
              className="mt-1 text-[11px] text-fg-faint hover:text-fg"
              onClick={() => setManualVram(0)}
            >
              reset to detected
            </button>
          )}
        </div>
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
      </Card>

      {/* Recommendations table */}
      <div className="mb-3 mt-6 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
          <Gauge className="h-4 w-4" /> Top picks
        </div>
        <BenchmarkDialog onDone={() => recs.reload()} />
      </div>

      {recs.error ? (
        <ErrorState message={`Recommendations unavailable: ${recs.error}`} onRetry={recs.reload} />
      ) : recs.loading || !recs.data ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="hidden px-4 py-2 text-left font-semibold md:table-cell">Scores</th>
                <th className="px-4 py-2 text-right font-semibold">VRAM</th>
                <th className="px-4 py-2 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
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
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Stat({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-panel-2 p-3">
      <div className="flex items-center gap-1.5 text-fg-faint">
        {icon}
        <span className="text-[10px] uppercase tracking-[0.08em]">{label}</span>
      </div>
      <div className="mt-1 truncate text-[14px] font-semibold" title={value}>
        {value}
      </div>
      {sub && <div className="truncate text-[11px] text-fg-muted">{sub}</div>}
    </div>
  );
}

function ScoreBar({ label, v }: { label: string; v: number }) {
  // Scores from the API are already 0–100 — do NOT re-scale.
  const pct = Math.max(0, Math.min(100, Math.round(v ?? 0)));
  return (
    <div className="flex items-center gap-2">
      <span className="w-12 shrink-0 text-[10.5px] text-fg-faint">{label}</span>
      <Progress value={pct} variant={pct >= 75 ? "success" : pct >= 50 ? "iris" : "warning"} className="h-1.5" />
      <span className="w-7 shrink-0 text-right font-mono text-[10px] text-fg-faint">{pct}</span>
    </div>
  );
}

const SOURCE_META: Record<
  "measured" | "calibrated" | "estimated",
  { variant: "success" | "info" | "neutral"; label: string }
> = {
  measured: { variant: "success", label: "measured" },
  calibrated: { variant: "info", label: "calibrated" },
  estimated: { variant: "neutral", label: "estimated" },
};

function SourceBadge({ source, band }: { source: "measured" | "calibrated" | "estimated"; band: number }) {
  const meta = SOURCE_META[source];
  const tip =
    source === "measured"
      ? "Real tok/s from your benchmarks"
      : source === "calibrated"
      ? `Adjusted by your machine's regime factor (±${Math.round(band)}%)`
      : `Theoretical estimate (±${Math.round(band)}%)`;
  return (
    <Badge variant={meta.variant} dot title={tip}>
      {meta.label}
      {source !== "estimated" && (
        <span className="font-mono text-[9px] opacity-70">±{Math.round(band)}%</span>
      )}
    </Badge>
  );
}
