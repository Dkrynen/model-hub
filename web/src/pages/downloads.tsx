import { Download as DownloadIcon, Clock } from "lucide-react";
import { PageHeader, EmptyState, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

export function Downloads() {
  const dl = useAsync(() => api.downloads());
  const rows = (dl.data ?? []).slice().reverse();

  return (
    <>
      <PageHeader title="Downloads" subtitle="History of models pulled through LAC." />

      {dl.error ? (
        <ErrorState message={`Couldn't load download history: ${dl.error}`} onRetry={dl.reload} />
      ) : dl.loading ? (
        <Card className="p-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="my-2 h-10 w-full" />
          ))}
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<DownloadIcon className="h-8 w-8" />}
          title="No downloads yet"
          hint="Install a model from Browse or the Dashboard and it'll appear here."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((r, i) => {
                const ok = String(r.status || "").toLowerCase().includes("ok") || r.status === "success";
                const bad = String(r.status || "").toLowerCase().includes("error") || r.status === "failed";
                return (
                  <tr key={i} className="transition-colors hover:bg-panel-3/40">
                    <td className="px-4 py-2.5 font-mono text-[13px]">{r.model || "—"}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={ok ? "success" : bad ? "danger" : "neutral"} dot>
                        {r.status || "—"}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-right text-[12.5px] text-fg-muted">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {r.timestamp ? new Date(r.timestamp).toLocaleString() : "—"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
