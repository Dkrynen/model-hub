import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { PageHeader } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { TuneHero } from "@/components/pro/tune-hero";

export function Pro() {
  const status = useAsync(() => api.proStatus());
  const licensed = status.data?.licensed;

  if (status.loading) return <PageHeader title="Pro" subtitle="Loading…" />;

  if (!licensed) {
    return (
      <>
        <PageHeader title="LAC Pro" subtitle="The tuning cockpit — model tuning, insights, benchmarking, autopilot, and custom imports." />
        <Card className="max-w-2xl p-6">
          <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="h-4 w-4 text-verdant" /> Unlock the Pro cockpit</div>
          <p className="mt-2 text-[13px] text-fg-muted">Tune any model to your exact hardware with before→after proof, track measured speed over time, and import any Hugging Face model. Activate Pro to turn it on.</p>
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
        <TuneHero />
        <div className="grid gap-5 lg:grid-cols-2">
          <InsightsPanel /> <AutopilotPanel />
          <BenchmarkPanel /> <ImportPanel />
        </div>
      </div>
    </>
  );
}

function InsightsPanel() {
  return <Card className="p-5 text-[13px] text-fg-muted">Insights panel — coming in the next task…</Card>;
}

function AutopilotPanel() {
  return <Card className="p-5 text-[13px] text-fg-muted">Autopilot panel — coming in the next task…</Card>;
}

function BenchmarkPanel() {
  return <Card className="p-5 text-[13px] text-fg-muted">Benchmark panel — coming in the next task…</Card>;
}

function ImportPanel() {
  return <Card className="p-5 text-[13px] text-fg-muted">Import panel — coming in the next task…</Card>;
}
