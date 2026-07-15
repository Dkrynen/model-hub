import { useState } from "react";
import {
  Cloud,
  CloudOff,
  Cpu,
  HardDrive,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { useAsync, useInterval } from "@/lib/hooks";
import type {
  CloudProductState,
  CloudUsage,
  LocalProProductState,
} from "@/lib/product-state";

type LaneProps = {
  icon: typeof Cpu;
  title: string;
  detail: string;
  status: string;
  variant: BadgeProps["variant"];
};

function ProductLane({ icon: Icon, title, detail, status, variant }: LaneProps) {
  return (
    <div className="min-w-0 px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-[9rem] flex-1 items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-panel-2 text-verdant">
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">{title}</div>
            <div className="mt-0.5 truncate text-[12px] text-fg-muted">{detail}</div>
          </div>
        </div>
        <Badge className="shrink-0 whitespace-nowrap" variant={variant} dot>{status}</Badge>
      </div>
    </div>
  );
}

function localProLane(state: LocalProProductState | undefined): Omit<LaneProps, "icon" | "title"> {
  if (!state || state.state === "absent") {
    return { detail: "Private desktop plugin", status: "Not installed", variant: "outline" };
  }
  if (state.state !== "ready") {
    return state.state === "incompatible"
      ? { detail: `Plugin ${state.plugin_version}`, status: "Update required", variant: "warning" }
      : { detail: `Plugin ${state.plugin_version}`, status: "Unavailable", variant: "danger" };
  }
  const active = state.entitlement.state === "active";
  return {
    detail: active ? planLabel(state.entitlement.plan) : `Plugin ${state.plugin_version}`,
    status: active ? "Active" : "Key required",
    variant: active ? "success" : "warning",
  };
}

function cloudLane(state: CloudProductState | undefined): Omit<LaneProps, "icon" | "title"> {
  if (!state || state.state === "not_configured") {
    return { detail: "Hosted service", status: "Not configured", variant: "outline" };
  }
  if (state.state !== "connected") {
    if (state.state === "signed_out") {
      return { detail: "Hosted service", status: "Signed out", variant: "outline" };
    }
    if (state.state === "authorizing") {
      return { detail: "Browser authorization", status: "Connecting", variant: "info" };
    }
    return { detail: "Hosted service", status: "Unavailable", variant: "danger" };
  }
  const cloudPlan = state.entitlements.find((item) => item.plan === "pro_cloud");
  return {
    detail: state.account.primary_email ?? state.account.display_name ?? "Cloud account",
    status: cloudPlan ? entitlementLabel(cloudPlan.state) : "Connected",
    variant: cloudPlan?.state === "active" || cloudPlan?.state === "trialing" ? "success" : "info",
  };
}

function planLabel(plan: string | null): string {
  if (plan === "pro_local") return "Local Pro";
  if (plan === "pro_cloud") return "Pro Cloud receipt";
  if (plan === "dev") return "Development access";
  return "Local Pro";
}

function entitlementLabel(state: string): string {
  return state.split("_").map((part) => part[0]?.toUpperCase() + part.slice(1)).join(" ");
}

function formatReset(timestamp: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function UsageMeter({
  label,
  used,
  limit,
  resetAt,
}: {
  label: string;
  used: number;
  limit: number;
  resetAt: number;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-3 text-[12px]">
        <span className="font-medium">{label}</span>
        <span className="font-mono text-fg-muted">{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <Progress value={(used / limit) * 100} className="h-1.5" />
      <div className="mt-1.5 text-[11px] text-fg-faint">Resets {formatReset(resetAt)}</div>
    </div>
  );
}

function CloudUsagePanel({ usage }: { usage: CloudUsage }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Cloud allowance</h2>
          <p className="mt-1 text-[12px] text-fg-muted">
            {usage.activeJobs} active, {usage.queuedJobs} queued
          </p>
        </div>
        <Badge variant="warning">Execution gated</Badge>
      </div>
      <div className="mt-5 grid gap-5 lg:grid-cols-3">
        <UsageMeter label="Monthly" used={usage.monthlyCredits} limit={5_000} resetAt={usage.resetAt.monthly} />
        <UsageMeter label="Weekly" used={usage.weeklyCredits} limit={2_500} resetAt={usage.resetAt.weekly} />
        <UsageMeter label="Five hour" used={usage.shortWindowCredits} limit={1_000} resetAt={usage.resetAt.five_hour} />
      </div>
    </Card>
  );
}

export function Account() {
  const product = useAsync(() => api.productState());
  const [action, setAction] = useState<string | null>(null);
  const unresolvedLane: Omit<LaneProps, "icon" | "title"> = product.error
    ? { detail: "Product state unavailable", status: "Unavailable", variant: "danger" }
    : { detail: "Loading product state", status: "Checking", variant: "outline" };
  const pro = product.data ? localProLane(product.data.local_pro) : unresolvedLane;
  const cloud = product.data ? cloudLane(product.data.cloud) : unresolvedLane;

  useInterval(() => product.reload(), product.data?.cloud.state === "authorizing" ? 1_500 : null);

  const beginSignIn = async (provider: "google" | "github") => {
    setAction(provider);
    try {
      await api.cloudAuthStart(provider);
      await product.reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Cloud sign-in could not start");
    } finally {
      setAction(null);
    }
  };

  const signOut = async () => {
    setAction("logout");
    try {
      await api.cloudLogout();
      await product.reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sign out failed");
    } finally {
      setAction(null);
    }
  };

  const cloudState = product.data?.cloud;
  const actionPending = action !== null || product.loading;

  return (
    <>
      <PageHeader title="Account" subtitle="Plans, identity, and usage">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Refresh account state"
          title="Refresh account state"
          disabled={product.loading}
          onClick={product.reload}
        >
          <RefreshCw className={product.loading ? "animate-spin" : ""} />
        </Button>
      </PageHeader>

      <div className="grid min-w-0 max-w-full grid-cols-[minmax(0,1fr)] divide-y divide-line overflow-hidden rounded border border-line bg-panel lg:grid-cols-3 lg:divide-x lg:divide-y-0">
        <ProductLane icon={HardDrive} title="Local" detail="On-device execution" status="Ready" variant="success" />
        <ProductLane icon={Sparkles} title="Local Pro" {...pro} />
        <ProductLane icon={Cloud} title="Cloud" {...cloud} />
      </div>

      {product.error && (
        <div className="mt-5 rounded border border-danger/40 bg-danger-soft px-4 py-3 text-[13px] text-danger">
          {product.error}
        </div>
      )}

      <div className="mt-5 grid gap-5">
        <Card className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                {cloudState?.state === "connected" ? (
                  <ShieldCheck className="h-4 w-4 text-success" />
                ) : (
                  <CloudOff className="h-4 w-4 text-fg-muted" />
                )}
                <h2 className="text-sm font-semibold">Cloud account</h2>
              </div>
              {cloudState?.state === "connected" ? (
                <div className="mt-3">
                  <div className="text-sm font-medium">
                    {cloudState.account.display_name || cloudState.account.primary_email || "LAC account"}
                  </div>
                  {cloudState.account.primary_email && cloudState.account.display_name && (
                    <div className="mt-0.5 text-[12px] text-fg-muted">{cloudState.account.primary_email}</div>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {cloudState.entitlements.length === 0 && <Badge variant="outline">No paid plan</Badge>}
                    {cloudState.entitlements.map((item) => (
                      <Badge
                        key={item.plan}
                        variant={item.state === "active" || item.state === "trialing" ? "success" : "warning"}
                      >
                        {item.plan === "pro_cloud" ? "Pro Cloud" : "Local Pro"}: {entitlementLabel(item.state)}
                      </Badge>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mt-2 text-[12px] text-fg-muted">
                  {cloudState?.state === "authorizing" && "Waiting for browser authorization"}
                  {cloudState?.state === "unreachable" && "Account service unavailable"}
                  {cloudState?.state === "not_configured" && "Service endpoint not configured"}
                  {!cloudState && (product.loading ? "Checking cloud session" : "Cloud session unavailable")}
                  {cloudState?.state === "signed_out" && "No cloud session"}
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              {cloudState?.state === "connected" && (
                <Button
                  variant="secondary"
                  disabled={actionPending}
                  aria-busy={action === "logout"}
                  onClick={signOut}
                >
                  {action === "logout" ? <RefreshCw className="animate-spin" /> : <LogOut />} Sign out
                </Button>
              )}
              {cloudState?.state === "signed_out" && (
                <>
                  <Button
                    variant="secondary"
                    disabled={actionPending}
                    aria-busy={action === "google"}
                    onClick={() => beginSignIn("google")}
                  >
                    {action === "google" ? <RefreshCw className="animate-spin" /> : <LogIn />} Google
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={actionPending}
                    aria-busy={action === "github"}
                    onClick={() => beginSignIn("github")}
                  >
                    {action === "github" ? <RefreshCw className="animate-spin" /> : <LogIn />} GitHub
                  </Button>
                </>
              )}
              {cloudState?.state === "unreachable" && (
                <>
                  <Button variant="secondary" disabled={product.loading} onClick={product.reload}>
                    <RefreshCw /> Retry
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={actionPending}
                    aria-busy={action === "logout"}
                    onClick={signOut}
                  >
                    {action === "logout" ? <RefreshCw className="animate-spin" /> : <LogOut />} Sign out
                  </Button>
                </>
              )}
            </div>
          </div>
        </Card>

        {cloudState?.state === "connected" && <CloudUsagePanel usage={cloudState.usage} />}
      </div>
    </>
  );
}
