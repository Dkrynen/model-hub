import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { stagedFullPath } from "@/lib/agent-workbench";
import type { StagedChangeSummary } from "@/lib/types";

function stagedStatusVariant(
  status: StagedChangeSummary["status"]
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "pending") return "warning";
  if (status === "applied") return "success";
  if (status === "conflict") return "danger";
  if (status === "reverted") return "info";
  return "neutral";
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function StagedQueue({
  changes,
  busy,
  onReview,
  onApply,
  onReject,
  onRevert,
  onApplyAll,
}: {
  changes: StagedChangeSummary[];
  busy: string;
  onReview: (change: StagedChangeSummary) => void;
  onApply: (change: StagedChangeSummary) => void;
  onReject: (change: StagedChangeSummary) => void;
  onRevert: (change: StagedChangeSummary) => void;
  onApplyAll: () => void;
}) {
  const pending = changes.filter((change) => change.status === "pending").length;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          Staged changes
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={pending ? "warning" : "neutral"}>{pending} pending</Badge>
          {pending > 1 && (
            <Button size="sm" variant="ghost" onClick={onApplyAll}>
              Apply all
            </Button>
          )}
        </div>
      </div>
      <div className="space-y-2">
        {changes.slice().reverse().map((change) => {
          const isBusy = Boolean(busy);
          const fullPath = stagedFullPath(change.root, change.path);
          return (
            <div key={change.id} className="rounded border border-line bg-panel-2 p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 break-all text-[12px] font-medium text-fg">{fullPath}</div>
                <Badge variant={stagedStatusVariant(change.status)}>{change.status}</Badge>
              </div>
              <div className="mt-1 break-all text-[11px] text-fg-faint">Run: {change.run_id}</div>
              <div className="mt-0.5 text-[11px] text-fg-faint">{formatBytes(change.new_size)} proposed</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onReview(change)}>
                  Review
                </Button>
                {change.status === "pending" && (
                  <>
                    <Button size="sm" disabled={isBusy} onClick={() => onApply(change)}>
                      Apply to disk
                    </Button>
                    <Button size="sm" variant="danger" disabled={isBusy} onClick={() => onReject(change)}>
                      Reject
                    </Button>
                  </>
                )}
                {change.status === "applied" && (
                  <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onRevert(change)}>
                    Revert
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
