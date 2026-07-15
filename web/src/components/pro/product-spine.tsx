import type { LucideIcon } from "lucide-react";
import {
  CheckCircle2,
  Code2,
  DownloadCloud,
  ExternalLink,
  Gauge,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { ProStatus } from "@/lib/types";
import { proPlanPresentation } from "@/lib/pro-entitlements";

export const PRO_WAITLIST_URL = "https://tally.so/r/GxyBx2";

export const PRO_UNLOCKED_FEATURES = [
  "Autopilot tuning and measured speed history",
  "Private and gated Hugging Face GGUF import",
  "Coding-agent cockpit for stronger local models",
  "Benchmark, tune, apply, and retry controls",
];

type Capability = {
  icon: LucideIcon;
  title: string;
  free: string;
  pro: string;
  licensedState: string;
  licensedVariant: BadgeProps["variant"];
  lockedState: string;
  lockedVariant: BadgeProps["variant"];
};

const CAPABILITIES: Capability[] = [
  {
    icon: Gauge,
    title: "Speed proof",
    free: "Estimated and calibrated recommendations.",
    pro: "Benchmarks, speed history, and tuned profiles from this machine.",
    licensedState: "Active",
    licensedVariant: "success",
    lockedState: "Key required",
    lockedVariant: "outline",
  },
  {
    icon: DownloadCloud,
    title: "Model supply",
    free: "Ollama installs plus GGUF compatibility preflight.",
    pro: "Private, gated, and custom Hugging Face GGUF imports with local token storage.",
    licensedState: "Active",
    licensedVariant: "success",
    lockedState: "Key required",
    lockedVariant: "outline",
  },
  {
    icon: Zap,
    title: "Automation",
    free: "Manual install, chat, delete, and diagnostics.",
    pro: "Autopilot sweeps supported installs and keeps measured recommendations fresh.",
    licensedState: "Active",
    licensedVariant: "success",
    lockedState: "Key required",
    lockedVariant: "outline",
  },
  {
    icon: Code2,
    title: "Build workbench",
    free: "Chat plus read-only Plan and Explore agents.",
    pro: "Build Mode is free on your local models; Pro makes them build-ready — per-model tuning and a readiness benchmark.",
    licensedState: "Safety gated",
    licensedVariant: "warning",
    lockedState: "Preview",
    lockedVariant: "warning",
  },
];

export function ProductSpine({
  licensed,
  status,
}: {
  licensed: boolean;
  status?: ProStatus | null;
}) {
  const statusLabel = licensed ? "Activated" : "Key gated";
  const planPresentation = proPlanPresentation(status?.plan);
  const planLabel = licensed ? planPresentation.label : "Local Pro - $36/year";
  const planDetail =
    licensed && planPresentation.kind === "cloud"
      ? "This local receipt includes Local Pro; Cloud account authority remains separate."
      : licensed && planPresentation.kind === "development"
        ? "Development override is active for this source build."
        : licensed && planPresentation.kind === "unknown"
          ? "Subscription details are unavailable for this plan."
          : "Local Pro is planned at $36/year.";
  const purchaseLabel = licensed ? "License active" : "Not available yet";

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-line bg-panel-2 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <Badge variant={licensed ? "success" : "warning"} dot>
              {licensed ? "Paid tools active" : "Paid launch lane"}
            </Badge>
            <h2 className="mt-3 text-lg font-semibold tracking-tight">
              Turn local models into a build workstation
            </h2>
            <p className="mt-2 text-[13px] leading-relaxed text-fg-muted">
              Pro is the monetized layer for people who want LAC to do work: measured speed,
              private model supply, tuning automation, and a coding cockpit for stronger local models.
              Pro Cloud is the planned $20/month higher tier. Account connectivity now lives in the
              same LAC desktop shell; checkout and hosted execution remain gated until the service
              and operational evidence are approved.
            </p>
          </div>
          {!licensed && (
            <Button asChild variant="secondary">
              <a href={PRO_WAITLIST_URL} target="_blank" rel="noreferrer">
                <ExternalLink /> Join waitlist
              </a>
            </Button>
          )}
        </div>
      </div>
      <div className="grid border-b border-line md:grid-cols-3">
        <Metric icon={Sparkles} label="Plan" value={planLabel} detail={planDetail} />
        <Metric icon={KeyRound} label="Delivery" value={statusLabel} detail="License key installs the private plugin locally." />
        <Metric icon={LockKeyhole} label="Purchase" value={purchaseLabel} detail="Account-bound checkout opens only after all launch gates." />
      </div>
      <div className="p-5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck className="h-4 w-4 text-verdant" />
          Free core stays useful. Pro sells leverage.
        </div>
        <div className="mt-4 overflow-x-auto">
          <div className="min-w-[760px] divide-y divide-line rounded border border-line">
            <div className="grid grid-cols-[1fr_1.2fr_1.5fr_120px] bg-panel-2 px-4 py-2 text-[11px] font-semibold uppercase text-fg-muted">
              <div>Capability</div>
              <div>Free</div>
              <div>Local Pro</div>
              <div>Status</div>
            </div>
            {CAPABILITIES.map((capability) => {
              const Icon = capability.icon;
              const state = licensed ? capability.licensedState : capability.lockedState;
              const variant = licensed ? capability.licensedVariant : capability.lockedVariant;
              return (
                <div
                  key={capability.title}
                  className="grid grid-cols-[1fr_1.2fr_1.5fr_120px] items-start gap-3 px-4 py-3 text-[13px]"
                >
                  <div className="flex items-center gap-2 font-medium">
                    <Icon className="h-4 w-4 shrink-0 text-verdant" />
                    {capability.title}
                  </div>
                  <div className="text-fg-muted">{capability.free}</div>
                  <div>{capability.pro}</div>
                  <div>
                    <Badge variant={variant}>{state}</Badge>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="min-w-0 border-line p-4 md:border-r md:last:border-r-0">
      <div className="flex items-center gap-2 text-[12px] font-medium text-fg-muted">
        <Icon className="h-3.5 w-3.5 text-verdant" />
        {label}
      </div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
      <p className="mt-1 text-[12px] leading-relaxed text-fg-muted">{detail}</p>
    </div>
  );
}

export function UnlockList() {
  return (
    <ul className="mt-4 space-y-1.5 text-left text-[13px]">
      {PRO_UNLOCKED_FEATURES.map((feature) => (
        <li key={feature} className="flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-verdant" />
          {feature}
        </li>
      ))}
    </ul>
  );
}
