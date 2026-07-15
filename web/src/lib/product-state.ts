export type LocalProEntitlement = {
  state: "active" | "inactive";
  plan: "dev" | "pro_local" | "pro_cloud" | null;
  expires_human: string | null;
  checked: string | null;
};

export type LocalProProductState =
  | { state: "absent" }
  | {
      state: "incompatible" | "load_error";
      plugin_version: string;
      host_api_version: number | null;
    }
  | {
      state: "ready";
      plugin_version: string;
      host_api_version: 1;
      schema_version: 1;
      product: "local_pro";
      entitlement: LocalProEntitlement;
      capabilities: string[];
    };

export type CloudAccount = {
  id: string;
  primary_email: string | null;
  display_name: string | null;
  avatar_url: string | null;
  status: "active" | "deleting" | "deleted";
  created_at: number;
};

export type CloudEntitlement = {
  plan: "pro_local" | "pro_cloud";
  state: "active" | "trialing" | "cancel_at_period_end" | "past_due" | "unpaid" | "revoked";
  effective_at: number;
  access_until: number | null;
  export_until: number | null;
  updated_at: number;
};

export type CloudUsage = {
  monthlyCredits: number;
  weeklyCredits: number;
  shortWindowCredits: number;
  activeJobs: number;
  queuedJobs: number;
  resetAt: { monthly: number; weekly: number; five_hour: number };
};

export type CloudProductState =
  | { state: "not_configured" | "signed_out" | "authorizing"; execution_available: false }
  | {
      state: "unreachable";
      execution_available: false;
      error: { code: "provider_unavailable" | "invalid_response" | "corrupt_store" | "secure_storage_unavailable" };
    }
  | {
      state: "connected";
      execution_available: false;
      account: CloudAccount;
      entitlements: CloudEntitlement[];
      usage: CloudUsage;
    };

export type ProductState = {
  schema_version: 1;
  execution_default: "local";
  local: { state: "ready" };
  local_pro: LocalProProductState;
  cloud: CloudProductState;
};

const CAPABILITY = /^[a-z][a-z0-9_]{0,63}$/;
const CLOUD_FAILURES = new Set([
  "provider_unavailable",
  "invalid_response",
  "corrupt_store",
  "secure_storage_unavailable",
]);
const ENTITLEMENT_STATES = new Set([
  "active", "trialing", "cancel_at_period_end", "past_due", "unpaid", "revoked",
]);

function invalid(): never {
  throw new Error("Invalid product state response");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}

function exact(value: unknown, keys: string[]): Record<string, unknown> {
  const result = record(value);
  const actual = Object.keys(result).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) return invalid();
  return result;
}

function safeInteger(value: unknown, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= maximum;
}

function boundedString(value: unknown, maximum: number, allowEmpty = false): value is string {
  return typeof value === "string" &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0) &&
    !/[\u0000-\u001f\u007f-\u009f]/u.test(value);
}

function nullableString(value: unknown, maximum: number, allowEmpty = false): value is string | null {
  return value === null || boundedString(value, maximum, allowEmpty);
}

function isoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return year > 0 && !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function decodeLocalPro(value: unknown): LocalProProductState {
  const base = record(value);
  if (base.state === "absent") {
    exact(base, ["state"]);
    return { state: "absent" };
  }
  if (base.state === "incompatible" || base.state === "load_error") {
    exact(base, ["state", "plugin_version", "host_api_version"]);
    if (!boundedString(base.plugin_version, 64) ||
        (base.host_api_version !== null && !safeInteger(base.host_api_version, 1_000))) return invalid();
    return base as LocalProProductState;
  }
  if (base.state !== "ready") return invalid();
  exact(base, [
    "state", "plugin_version", "host_api_version", "schema_version", "product", "entitlement", "capabilities",
  ]);
  const entitlement = exact(base.entitlement, ["state", "plan", "expires_human", "checked"]);
  const active = entitlement.state === "active";
  const knownPlan = ["dev", "pro_local", "pro_cloud"].includes(String(entitlement.plan));
  const canonicalExpiry = entitlement.expires_human === "while subscribed" || isoDate(entitlement.expires_human);
  if (
    base.host_api_version !== 1 || base.schema_version !== 1 || base.product !== "local_pro" ||
    !boundedString(base.plugin_version, 64) ||
    !["active", "inactive"].includes(String(entitlement.state)) ||
    (active ? (!knownPlan || !canonicalExpiry) : (
      entitlement.plan !== null || entitlement.expires_human !== null || entitlement.checked !== null
    )) ||
    !(entitlement.checked === null || isoDate(entitlement.checked)) ||
    !Array.isArray(base.capabilities) || base.capabilities.length > 64 ||
    !base.capabilities.every((item) => typeof item === "string" && CAPABILITY.test(item)) ||
    base.capabilities.join("\0") !== [...new Set(base.capabilities)].sort().join("\0")
  ) return invalid();
  return base as LocalProProductState;
}

function decodeAccount(value: unknown): CloudAccount {
  const account = exact(value, ["id", "primary_email", "display_name", "avatar_url", "status", "created_at"]);
  if (
    typeof account.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(account.id) ||
    !nullableString(account.primary_email, 320) ||
    !nullableString(account.display_name, 120, true) ||
    !nullableString(account.avatar_url, 2_048) ||
    !["active", "deleting", "deleted"].includes(String(account.status)) ||
    !safeInteger(account.created_at)
  ) return invalid();
  if (typeof account.avatar_url === "string") {
    try {
      const url = new URL(account.avatar_url);
      if (url.protocol !== "https:" || url.username || url.password) return invalid();
    } catch {
      return invalid();
    }
  }
  return account as CloudAccount;
}

function decodeEntitlements(value: unknown): CloudEntitlement[] {
  if (!Array.isArray(value) || value.length > 2) return invalid();
  const parsed = value.map((item) => {
    const row = exact(item, ["plan", "state", "effective_at", "access_until", "export_until", "updated_at"]);
    if (
      !["pro_local", "pro_cloud"].includes(String(row.plan)) ||
      !ENTITLEMENT_STATES.has(String(row.state)) ||
      !safeInteger(row.effective_at) ||
      !(row.access_until === null || safeInteger(row.access_until)) ||
      !(row.export_until === null || safeInteger(row.export_until)) ||
      !safeInteger(row.updated_at)
    ) return invalid();
    return row as CloudEntitlement;
  });
  if (new Set(parsed.map((row) => row.plan)).size !== parsed.length) return invalid();
  return parsed;
}

function decodeUsage(value: unknown): CloudUsage {
  const usage = exact(value, [
    "monthlyCredits", "weeklyCredits", "shortWindowCredits", "activeJobs", "queuedJobs", "resetAt",
  ]);
  const resetAt = exact(usage.resetAt, ["monthly", "weekly", "five_hour"]);
  if (
    !safeInteger(usage.monthlyCredits, 5_000) ||
    !safeInteger(usage.weeklyCredits, 2_500) ||
    !safeInteger(usage.shortWindowCredits, 1_000) ||
    !safeInteger(usage.activeJobs, 3) ||
    !safeInteger(usage.queuedJobs, 5) ||
    !safeInteger(resetAt.monthly) || !safeInteger(resetAt.weekly) || !safeInteger(resetAt.five_hour)
  ) return invalid();
  return { ...usage, resetAt } as CloudUsage;
}

function decodeCloud(value: unknown): CloudProductState {
  const base = record(value);
  if (["not_configured", "signed_out", "authorizing"].includes(String(base.state))) {
    exact(base, ["state", "execution_available"]);
    if (base.execution_available !== false) return invalid();
    return base as CloudProductState;
  }
  if (base.state === "unreachable") {
    exact(base, ["state", "execution_available", "error"]);
    const error = exact(base.error, ["code"]);
    if (base.execution_available !== false || !CLOUD_FAILURES.has(String(error.code))) return invalid();
    return base as CloudProductState;
  }
  if (base.state !== "connected") return invalid();
  exact(base, ["state", "execution_available", "account", "entitlements", "usage"]);
  if (base.execution_available !== false) return invalid();
  return {
    state: "connected",
    execution_available: false,
    account: decodeAccount(base.account),
    entitlements: decodeEntitlements(base.entitlements),
    usage: decodeUsage(base.usage),
  };
}

export function decodeProductState(value: unknown): ProductState {
  const result = exact(value, ["schema_version", "execution_default", "local", "local_pro", "cloud"]);
  const local = exact(result.local, ["state"]);
  if (result.schema_version !== 1 || result.execution_default !== "local" || local.state !== "ready") return invalid();
  return {
    schema_version: 1,
    execution_default: "local",
    local: { state: "ready" },
    local_pro: decodeLocalPro(result.local_pro),
    cloud: decodeCloud(result.cloud),
  };
}
