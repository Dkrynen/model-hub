import { useNavigate } from "react-router-dom";
import { Cpu, MemoryStick, HardDrive, RefreshCw, Microchip, Zap } from "lucide-react";
import { PageHeader, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ModelCard } from "@/components/model-card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { pullWithToast } from "@/lib/installer";
import { StudioLauncher } from "@/components/studio-launcher";

export function Dashboard() {
  const navigate = useNavigate();
  const scan = useAsync(() => api.scan());
  const recs = useAsync(() => api.recommend({ use_case: "coding", top_k: 6 }));
  const installed = useAsync(() => api.installed());

  const totalVram = scan.data?.total_vram_gb ?? 0;
  const installedNames = new Set((installed.data ?? []).map((m) => m.name));

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={
          scan.data
            ? `${scan.data.cpu} · ${scan.data.ram_gb.toFixed(0)} GB RAM · ${totalVram} GB VRAM`
            : "Scanning your hardware…"
        }
      >
        <Button variant="secondary" onClick={() => navigate("/scan")}>
          <Zap /> Scan &amp; recommend
        </Button>
      </PageHeader>

      <StudioLauncher />

      {scan.error ? (
        <ErrorState message={`Couldn’t scan hardware: ${scan.error}`} onRetry={scan.reload} />
      ) : scan.loading || !scan.data ? (
        <HardwareSkeleton />
      ) : (
        <HardwareHero info={scan.data} onRescan={scan.reload} />
      )}

      <div className="mb-3 mt-8 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
          Recommended for your GPU
        </h2>
        {recs.data && (
          <span className="text-[12px] text-fg-faint">{recs.data.recommendations.length} matches</span>
        )}
      </div>

      {recs.error ? (
        <ErrorState message={`Recommendations unavailable: ${recs.error}`} onRetry={recs.reload} />
      ) : recs.loading || !recs.data ? (
        <GridSkeleton />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {recs.data.recommendations.map((r) => (
            <ModelCard
              key={r.model_id + r.quant}
              model={{
                name: r.name,
                description:
                  r.run_mode === "gpu"
                    ? "Runs fully on GPU"
                    : r.run_mode === "cpu_offload"
                    ? "Partial CPU offload"
                    : undefined,
                params_b: r.params_b,
                context: r.context,
                vram_gb: r.vram_gb,
                capabilities: r.quant ? [r.quant] : [],
                installed: installedNames.has(r.name) || installedNames.has(r.model_id),
              }}
              vramLabel={`VRAM ${r.quant}`}
              totalVram={totalVram}
              onPrimary={() =>
                pullWithToast(r.ollama_cmd?.replace(/^ollama run\s+/, "") || r.model_id, () =>
                  installed.reload()
                )
              }
            />
          ))}
        </div>
      )}
    </>
  );
}

function HardwareHero({
  info,
  onRescan,
}: {
  info: import("@/lib/types").ScanInfo;
  onRescan: () => void;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="grid grid-cols-2 divide-x divide-y divide-line sm:grid-cols-4 sm:divide-y-0">
        <Stat icon={<Microchip />} label="CPU" value={info.cpu} sub={`${info.cores} cores`} />
        <Stat icon={<MemoryStick />} label="Memory" value={`${info.ram_gb.toFixed(0)} GB`} sub="RAM" />
        <Stat
          icon={<HardDrive />}
          label="VRAM"
          value={`${info.total_vram_gb} GB`}
          sub={info.is_apple_silicon ? "Apple unified" : "total GPU"}
        />
        <Stat
          icon={<Cpu />}
          label="GPUs"
          value={`${info.gpus.length}`}
          sub={info.gpus.map((g) => g.backend).filter((v, i, a) => a.indexOf(v) === i).join(", ") || "—"}
        />
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5">
        <div className="flex flex-wrap gap-1.5">
          {info.gpus.map((g, i) => (
            <Badge key={i} variant="neutral">
              {g.name} · {g.vram_gb} GB
            </Badge>
          ))}
        </div>
        <Button variant="ghost" size="sm" onClick={onRescan}>
          <RefreshCw /> Rescan
        </Button>
      </div>
    </Card>
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
    <div className="flex flex-col gap-1 p-4">
      <div className="flex items-center gap-1.5 text-fg-faint">
        {icon}
        <span className="text-[10px] uppercase tracking-[0.08em]">{label}</span>
      </div>
      <div className="truncate text-[15px] font-semibold" title={value}>
        {value}
      </div>
      {sub && <div className="truncate text-[11px] text-fg-muted">{sub}</div>}
    </div>
  );
}

function HardwareSkeleton() {
  return (
    <Card className="p-0">
      <div className="grid grid-cols-2 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="p-4">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="mt-2 h-5 w-24" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((i) => (
        <Card key={i} className="h-[200px] p-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="mt-3 h-3 w-full" />
          <Skeleton className="mt-2 h-3 w-2/3" />
          <Skeleton className="mt-5 h-1.5 w-full" />
        </Card>
      ))}
    </div>
  );
}
