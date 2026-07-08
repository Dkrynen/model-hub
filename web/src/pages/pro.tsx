import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { PageHeader } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { TuneHero } from "@/components/pro/tune-hero";
import { InsightsPanel } from "@/components/pro/insights-panel";
import { AutopilotPanel } from "@/components/pro/autopilot-panel";
import { BenchmarkPanel } from "@/components/pro/benchmark-panel";
import { ImportPanel } from "@/components/pro/import-panel";
import { AgentCockpitPanel } from "@/components/pro/agent-cockpit-panel";

export function Pro() {
  const status = useAsync(() => api.proStatus());
  const licensed = status.data?.licensed;

  if (status.loading) return <PageHeader title="Pro" subtitle="Loading…" />;

  if (!licensed) {
    return (
      <>
        <PageHeader title="LAC Pro" subtitle="Local coding agents, model tuning, insights, benchmarking, autopilot, and custom imports." />
        <Card className="max-w-2xl p-6">
          <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="h-4 w-4 text-verdant" /> Unlock the Pro cockpit</div>
          <p className="mt-2 text-[13px] text-fg-muted">Run local coding agents through the CLI, tune models to your exact hardware, track measured speed, and import compatible Hugging Face GGUF or safetensors models. Activate Pro to turn it on.</p>
          <Button className="mt-4" asChild><Link to="/settings">Activate Pro</Link></Button>
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader title="LAC Pro"
        subtitle={`Active · ${status.data?.plan ?? "pro"} · ${status.data?.expires_human ?? ""}`} />
      <div className="grid gap-5">
        <AgentCockpitPanel />
        <TuneHero />
        <div className="grid gap-5 lg:grid-cols-2">
          <InsightsPanel /> <AutopilotPanel />
          <BenchmarkPanel /> <ImportPanel />
        </div>
      </div>
    </>
  );
}
