import { Activity } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface InsightRow {
  model: string;
  runs: number;
  baseline_tps: number;
  recent_tps: number;
  delta_pct: number;
  regression: boolean;
}

interface InsightsResponse {
  state: string;
  rows?: InsightRow[];
}

export function InsightsPanel() {
  const insights = useAsync(() => api.proInsights());
  const data = insights.data as InsightsResponse | null;
  const rows = data?.state === "ok" ? data.rows ?? [] : [];

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <Activity className="h-4 w-4 text-verdant" /> Insights
      </div>

      {insights.loading ? (
        <Skeleton className="h-24 w-full" />
      ) : rows.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Benchmark a few models to build speed history.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-right font-semibold">Baseline</th>
                <th className="px-4 py-2 text-right font-semibold">Recent</th>
                <th className="px-4 py-2 text-right font-semibold">Δ%</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((r) => (
                <tr key={r.model} className="transition-colors hover:bg-panel-3/40">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[13px] font-medium">{r.model}</span>
                      {r.regression && <Badge variant="danger">regression</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                    {r.baseline_tps.toFixed(1)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                    {r.recent_tps.toFixed(1)}
                  </td>
                  <td
                    className={cn(
                      "px-4 py-3 text-right font-mono text-[13px]",
                      r.delta_pct < 0 ? "text-warning" : "text-success"
                    )}
                  >
                    {r.delta_pct >= 0 ? "+" : ""}
                    {r.delta_pct.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
