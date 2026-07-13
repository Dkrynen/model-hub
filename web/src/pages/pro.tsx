import { PageHeader } from "@/components/page";
import { ProActivation } from "@/components/pro-activation";
import { AgentCockpitPanel } from "@/components/pro/agent-cockpit-panel";
import { AutopilotPanel } from "@/components/pro/autopilot-panel";
import { BenchmarkPanel } from "@/components/pro/benchmark-panel";
import { ImportPanel } from "@/components/pro/import-panel";
import { InsightsPanel } from "@/components/pro/insights-panel";
import { ProductSpine } from "@/components/pro/product-spine";
import { TuneHero } from "@/components/pro/tune-hero";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { proPlanPresentation } from "@/lib/pro-entitlements";

export function Pro() {
  const status = useAsync(() => api.proStatus());
  const licensed = Boolean(status.data?.licensed);

  if (status.loading) return <PageHeader title="Pro" subtitle="Loading..." />;

  if (!licensed) {
    return (
      <>
        <PageHeader
          title="LAC Pro"
          subtitle="The paid desktop layer for tuning, private imports, measured speed, and local coding-agent readiness."
        />
        <div className="grid gap-5 xl:grid-cols-[minmax(0,420px)_1fr]">
          <ProActivation />
          <ProductSpine licensed={false} status={status.data} />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="LAC Pro"
        subtitle={`Active - ${proPlanPresentation(status.data?.plan).label} - ${status.data?.expires_human ?? ""}`}
      />
      <div className="grid gap-5">
        <ProductSpine licensed status={status.data} />
        <AgentCockpitPanel />
        <TuneHero />
        <div className="grid gap-5 lg:grid-cols-2">
          <InsightsPanel />
          <AutopilotPanel />
          <BenchmarkPanel />
          <ImportPanel />
        </div>
      </div>
    </>
  );
}
