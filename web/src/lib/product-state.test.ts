import { describe, expect, it } from "vitest";
import { decodeProductState } from "./product-state";

const base = {
  schema_version: 1,
  execution_default: "local",
  local: { state: "ready" },
  local_pro: { state: "absent" },
  cloud: { state: "signed_out", execution_available: false },
};

describe("product state v1", () => {
  it("keeps local execution authoritative while Cloud is signed out", () => {
    expect(decodeProductState(base)).toEqual(base);
  });

  it("does not infer a Cloud session from a local pro_cloud receipt", () => {
    const value = {
      ...base,
      local_pro: {
        state: "ready",
        plugin_version: "1.0.0",
        host_api_version: 1,
        schema_version: 1,
        product: "local_pro",
        entitlement: {
          state: "active",
          plan: "pro_cloud",
          expires_human: "while subscribed",
          checked: "2026-07-15",
        },
        capabilities: ["agent_cockpit"],
      },
    };
    expect(decodeProductState(value).cloud.state).toBe("signed_out");
  });

  it("rejects sensitive or unversioned extension fields", () => {
    expect(() => decodeProductState({ ...base, local_pro: { state: "absent", machine: "secret" } }))
      .toThrow(/Invalid product state/);
    expect(() => decodeProductState({ ...base, schema_version: 2 }))
      .toThrow(/Invalid product state/);
  });

  it("bounds plugin metadata in every local Pro state", () => {
    expect(decodeProductState({
      ...base,
      local_pro: { state: "incompatible", plugin_version: "?", host_api_version: 1 },
    }).local_pro).toEqual({ state: "incompatible", plugin_version: "?", host_api_version: 1 });
    expect(() => decodeProductState({
      ...base,
      local_pro: { state: "incompatible", plugin_version: `1.0\u0085`, host_api_version: 1 },
    })).toThrow(/Invalid product state/);
    expect(() => decodeProductState({
      ...base,
      local_pro: { state: "incompatible", plugin_version: "v".repeat(65), host_api_version: 1 },
    })).toThrow(/Invalid product state/);
  });

  it("rejects paid-access claims without a known plan and canonical expiry", () => {
    const localPro = {
      state: "ready",
      plugin_version: "1.0.0",
      host_api_version: 1,
      schema_version: 1,
      product: "local_pro",
      entitlement: {
        state: "active",
        plan: null,
        expires_human: "whenever",
        checked: "2026-07-15",
      },
      capabilities: ["agent_cockpit"],
    };

    expect(() => decodeProductState({ ...base, local_pro: localPro }))
      .toThrow(/Invalid product state/);
    expect(() => decodeProductState({
      ...base,
      local_pro: {
        ...localPro,
        entitlement: {
          state: "inactive",
          plan: null,
          expires_human: "2027-01-01",
          checked: null,
        },
      },
    })).toThrow(/Invalid product state/);
  });
});
