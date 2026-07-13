export type ProPlanKind = "local" | "cloud" | "development" | "unknown";

export interface ProPlanPresentation {
  kind: ProPlanKind;
  label: string;
}

export function proPlanPresentation(plan: string | null | undefined): ProPlanPresentation {
  if (plan === "pro" || plan === "pro_local") {
    return { kind: "local", label: "Local Pro" };
  }
  if (plan === "pro_cloud") {
    return { kind: "cloud", label: "Pro Cloud" };
  }
  if (plan === "dev") {
    return { kind: "development", label: "Development override" };
  }
  return { kind: "unknown", label: "Pro subscription" };
}
