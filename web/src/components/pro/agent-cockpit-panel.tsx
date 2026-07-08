import { useState } from "react";
import { Bot, Check, Copy, Terminal, Zap } from "lucide-react";
import { toast } from "sonner";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type AgentInfo = {
  name: string;
  description?: string;
  tools?: string[];
  permissions?: { read?: boolean; write?: boolean; bash?: boolean; web?: boolean };
};

type AgentCockpit = {
  state: string;
  workspace?: string;
  ollama_host?: string;
  models?: string[];
  recommended_models?: string[];
  agents?: AgentInfo[];
  cli?: { available?: boolean; command?: string; agent_switch?: string };
  next_actions?: string[];
  model_error?: string;
  agent_error?: string;
};

export function AgentCockpitPanel() {
  const status = useAsync<AgentCockpit>(() => api.proAgentCockpit());
  const [copied, setCopied] = useState(false);

  const data = status.data;
  const command = data?.cli?.command ?? "";
  const recommended = data?.recommended_models ?? [];
  const agents = data?.agents ?? [];

  const copyCommand = async () => {
    if (!command) return;
    await navigator.clipboard.writeText(command);
    setCopied(true);
    toast.success("Launch command copied");
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-line bg-panel-2/60 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Bot className="h-4 w-4 text-verdant" />
              Local coding agent
            </div>
            <p className="mt-1 text-[13px] text-fg-muted">
              Pro connects your installed Ollama models to LAC's CLI agent tools.
            </p>
          </div>
          <Badge variant={data?.state === "ok" ? "success" : "neutral"} dot>
            {data?.state === "ok" ? "ready path" : "checking"}
          </Badge>
        </div>
      </div>

      {status.loading ? (
        <div className="space-y-3 p-5">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : data?.state === "not_licensed" ? (
        <div className="p-5 text-[13px] text-fg-muted">Activate Pro to open the local agent cockpit.</div>
      ) : (
        <div className="grid gap-4 p-5">
          <div className="grid gap-3 md:grid-cols-3">
            <Metric label="Workspace" value={data?.workspace || "default"} />
            <Metric label="Ollama" value={data?.ollama_host || "localhost"} />
            <Metric label="Local models" value={String(data?.models?.length ?? 0)} />
          </div>

          <div className="rounded-lg border border-line bg-panel-2/50 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Terminal className="h-4 w-4 text-fg-muted" />
                CLI launch
              </div>
              <Button size="sm" variant="secondary" onClick={copyCommand} disabled={!command}>
                {copied ? <Check className="mr-2 h-3.5 w-3.5" /> : <Copy className="mr-2 h-3.5 w-3.5" />}
                Copy
              </Button>
            </div>
            <code className="block overflow-x-auto rounded-md bg-bg px-3 py-2 font-mono text-[12px] text-fg">
              {command || "No CLI command available yet"}
            </code>
            <p className="mt-2 text-[12px] text-fg-muted">
              In the TUI, use <code className="font-mono">{data?.cli?.agent_switch || "/agent build"}</code> for file,
              shell, and verification tools with permissions.
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <Zap className="h-4 w-4 text-verdant" />
                Best local coding models
              </div>
              <div className="space-y-2">
                {recommended.length ? recommended.map((m) => (
                  <div key={m} className="rounded-md border border-line bg-panel-2 px-3 py-2 font-mono text-[13px]">
                    {m}
                  </div>
                )) : (
                  <p className="rounded-md border border-line bg-panel-2 px-3 py-2 text-[13px] text-fg-muted">
                    Install qwen2.5-coder, deepseek-coder, or another tool/coding model.
                  </p>
                )}
              </div>
            </div>

            <div>
              <div className="mb-2 text-sm font-semibold">Agent modes</div>
              <div className="space-y-2">
                {agents.map((a) => (
                  <div key={a.name} className="rounded-md border border-line bg-panel-2 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[13px] font-semibold">{a.name}</span>
                      <span className="text-[11px] text-fg-muted">{(a.tools ?? []).join(", ") || "no tools"}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {(["read", "write", "bash", "web"] as const).map((k) => (
                        <span
                          key={k}
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[11px]",
                            a.permissions?.[k] ? "border-verdant/40 text-verdant" : "border-line text-fg-muted"
                          )}
                        >
                          {k}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {data?.next_actions?.length ? (
            <div className="rounded-lg border border-line bg-panel-2/50 p-4">
              <div className="mb-2 text-sm font-semibold">Next move</div>
              <ul className="space-y-1 text-[13px] text-fg-muted">
                {data.next_actions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel-2 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-fg-faint">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}
