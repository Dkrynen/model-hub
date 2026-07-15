import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Boxes, Play, Trash2, Plus, Activity, CircleDot } from "lucide-react";
import { toast } from "sonner";
import { PageHeader, ErrorState, EmptyState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";
import { fmtBytes, cn } from "@/lib/utils";
import { pullWithToast } from "@/lib/installer";

export function Installed() {
  const navigate = useNavigate();
  const installed = useAsync(() => api.installed());
  const ps = useAsync(() => api.ps());
  useInterval(() => ps.reload(), 5000);

  const [newModel, setNewModel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const onDelete = async (model: string) => {
    if (!window.confirm(`Delete ${model}? This frees its disk space.`)) return;
    setBusy(model);
    try {
      const r = await api.delete(model);
      if (r.error) throw new Error(r.error);
      toast.success(`Deleted ${model}`);
      installed.reload();
      ps.reload();
    } catch (e) {
      toast.error("Delete failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const models = installed.data ?? [];

  return (
    <>
      <PageHeader title="Installed" subtitle="Models present in your local Ollama library." />

      {/* Running */}
      <Card className="mb-5 p-4">
        <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
          <Activity className="h-4 w-4 text-success" /> Running now
        </div>
        {ps.loading && !ps.data ? (
          <Skeleton className="h-8 w-48" />
        ) : ps.data?.models.length ? (
          <div className="flex flex-wrap gap-2">
            {ps.data.models.map((m) => (
              <Badge key={m.name} variant="success" dot>
                {m.name}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-fg-muted">No models loaded in memory.</p>
        )}
      </Card>

      {/* Pull new */}
      <Card className="mb-5 flex flex-wrap items-center gap-2 p-3">
        <Plus className="ml-1 h-4 w-4 text-fg-muted" />
        <Input
          placeholder="model:tag (e.g. llama3.2:3b)"
          value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newModel.trim()) {
              pullWithToast(newModel.trim(), installed.reload);
              setNewModel("");
            }
          }}
          className="h-9 max-w-[260px]"
        />
        <Button
          size="sm"
          disabled={!newModel.trim()}
          onClick={() => {
            pullWithToast(newModel.trim(), installed.reload);
            setNewModel("");
          }}
        >
          Install
        </Button>
      </Card>

      {/* Installed list */}
      {installed.error ? (
        <ErrorState message={`Couldn’t list models: ${installed.error}`} onRetry={installed.reload} />
      ) : installed.loading ? (
        <ListSkeleton />
      ) : models.length === 0 ? (
        <EmptyState
          icon={<Boxes className="h-8 w-8" />}
          title="No models installed"
          hint="Browse the library or pull a model above to get started."
        >
          <Button variant="secondary" onClick={() => navigate("/browse")}>
            Browse models
          </Button>
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-right font-semibold">Size</th>
                <th className="hidden px-4 py-2 text-left font-semibold sm:table-cell">Modified</th>
                <th className="px-4 py-2 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {models.map((m) => (
                <tr key={m.name} className="transition-colors hover:bg-panel-3/40">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <CircleDot className="h-3.5 w-3.5 text-fg-faint" />
                      <span className="font-mono text-[13px]">{m.name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-[13px] text-fg-muted">
                    {fmtBytes(m.size_gb)}
                  </td>
                  <td className="hidden px-4 py-2.5 text-[12.5px] text-fg-muted sm:table-cell">
                    {m.modified ? new Date(m.modified).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => navigate(`/studio?model=${encodeURIComponent(m.name)}`)}
                      >
                        <Play /> Studio
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className={cn("text-danger hover:bg-danger-soft hover:text-danger")}
                        disabled={busy === m.name}
                        onClick={() => onDelete(m.name)}
                      >
                        <Trash2 /> Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}
