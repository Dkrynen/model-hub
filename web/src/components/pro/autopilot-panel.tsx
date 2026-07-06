import { Zap } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

interface AutopilotEntry {
  model: string;
  state: string;
  tokens_per_second?: number;
  updated_at: number;
}

interface AutopilotResponse {
  state: string;
  entries?: AutopilotEntry[];
}

const STATE_LABELS: Record<string, string> = {
  done: "optimized",
  running: "optimizing…",
  failed_silent: "skipped",
  idle: "—",
};

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

export function AutopilotPanel() {
  const log = useAsync(() => api.proAutopilotLog());
  const data = log.data as AutopilotResponse | null;
  const entries = data?.state === "ok" ? data.entries ?? [] : [];

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <Zap className="h-4 w-4 text-verdant" /> Autopilot
      </div>

      {log.loading ? (
        <Skeleton className="h-24 w-full" />
      ) : entries.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Autopilot runs automatically after each model install.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">tok/s</th>
                <th className="px-4 py-2 text-right font-semibold">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {entries.map((e, i) => (
                <tr key={`${e.model}-${i}`} className="transition-colors hover:bg-panel-3/40">
                  <td className="px-4 py-3 font-mono text-[13px] font-medium">{e.model}</td>
                  <td className="px-4 py-3 text-[13px] text-fg-muted">
                    {STATE_LABELS[e.state] ?? e.state}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                    {e.tokens_per_second != null ? e.tokens_per_second.toFixed(1) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right text-[12px] text-fg-faint">
                    {relativeTime(e.updated_at)}
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
