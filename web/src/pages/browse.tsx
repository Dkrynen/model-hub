import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { SlidersHorizontal, Plus } from "lucide-react";
import { PageHeader, ErrorState, EmptyState } from "@/components/page";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ModelCard } from "@/components/model-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { pullWithToast } from "@/lib/installer";

const CAPS = [
  { v: "", l: "All capabilities" },
  { v: "vision", l: "Vision" },
  { v: "tools", l: "Tools" },
  { v: "thinking", l: "Thinking" },
  { v: "embedding", l: "Embedding" },
];
const SORTS = [
  { v: "pulls", l: "Popular" },
  { v: "name", l: "Name" },
  { v: "vram", l: "VRAM (low)" },
  { v: "params", l: "Params (high)" },
];

export function Browse() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [capability, setCapability] = useState("");
  const [sort, setSort] = useState("pulls");
  const [compatible, setCompatible] = useState(false);
  const [limit, setLimit] = useState(36);
  const [newModel, setNewModel] = useState("");

  const lib = useAsync(
    () => api.library({ q, capability, sort, compatible: compatible ? "gpu" : "" }),
    [q, capability, sort, compatible]
  );

  const totalVram = lib.data?.system_vram ?? undefined;
  const models = (lib.data?.models ?? []).slice(0, limit);

  return (
    <>
      <PageHeader title="Browse models" subtitle="The full Ollama library — filtered to your hardware." />

      <Card className="mb-4 flex flex-wrap items-center gap-2 p-2.5">
        <form
          className="relative min-w-[200px] flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(q.trim());
            setParams(q.trim() ? { q: q.trim() } : {});
            setLimit(36);
          }}
        >
          <Input
            placeholder="Search by name or description…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pr-16"
          />
          <Button type="submit" size="sm" className="absolute right-1 top-1 h-7">
            Search
          </Button>
        </form>

        <Select
          value={capability || "all"}
          onValueChange={(v) => {
            setCapability(v === "all" ? "" : v);
            setLimit(36);
          }}
        >
          <SelectTrigger className="h-9 w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CAPS.map((c) => (
              <SelectItem key={c.v || "all"} value={c.v || "all"}>
                {c.l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="h-9 w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORTS.map((s) => (
              <SelectItem key={s.v} value={s.v}>
                {s.l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded border border-line bg-panel-2 px-3 text-[13px] text-fg-muted">
          <SlidersHorizontal className="h-3.5 w-3.5" />
          Fits my GPU
          <Switch checked={compatible} onCheckedChange={setCompatible} />
        </label>
      </Card>

      {/* Pull any model:tag from the Ollama registry — full availability */}
      <Card className="mb-4 flex flex-wrap items-center gap-2 p-2.5">
        <Plus className="ml-1 h-4 w-4 text-fg-muted" />
        <span className="text-[13px] text-fg-muted">Pull any model</span>
        <Input
          placeholder="namespace/model:tag (e.g. hf.co/bartowski/Qwen3-30B-A3B:Q4_K_M)"
          value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newModel.trim()) {
              pullWithToast(newModel.trim(), lib.reload);
              setNewModel("");
            }
          }}
          className="h-9 max-w-[420px] flex-1"
        />
        <Button
          size="sm"
          disabled={!newModel.trim()}
          onClick={() => {
            pullWithToast(newModel.trim(), lib.reload);
            setNewModel("");
          }}
        >
          Pull
        </Button>
      </Card>

      {lib.error ? (
        <ErrorState message={`Couldn’t load library: ${lib.error}`} onRetry={lib.reload} />
      ) : lib.loading ? (
        <GridSkeleton />
      ) : models.length === 0 ? (
        <EmptyState title="No models match" hint="Try a different search or loosen the filters." />
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2 text-[12px] text-fg-faint">
            <Badge variant="neutral">{lib.data?.total ?? 0} models</Badge>
            {totalVram ? <span>· filtering against {totalVram} GB VRAM</span> : null}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {models.map((m) => (
              <ModelCard
                key={m.name}
                model={{
                  name: m.name,
                  description: m.description,
                  params_b: m.params_b,
                  vram_gb: m.vram_q4,
                  capabilities: (m.capabilities ?? []).slice(0, 4),
                }}
                vramLabel="VRAM Q4"
                totalVram={totalVram}
                onPrimary={() => pullWithToast(m.name, lib.reload)}
              />
            ))}
          </div>
          {(lib.data?.models?.length ?? 0) > limit && (
            <div className="mt-5 flex justify-center">
              <Button variant="secondary" onClick={() => setLimit((l) => l + 36)}>
                Show more
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 9 }).map((_, i) => (
        <Card key={i} className="h-[210px] p-4">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="mt-2 h-3 w-full" />
          <Skeleton className="mt-1.5 h-3 w-2/3" />
          <Skeleton className="mt-5 h-1.5 w-full" />
        </Card>
      ))}
    </div>
  );
}
