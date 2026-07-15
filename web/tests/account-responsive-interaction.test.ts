// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { getByText, within } from "@testing-library/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Account } from "@/pages/account";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const { productState } = vi.hoisted(() => ({
  productState: {
    schema_version: 1,
    execution_default: "local",
    local: { state: "ready" },
    local_pro: {
      state: "ready",
      plugin_version: "1.0.0",
      host_api_version: 1,
      schema_version: 1,
      product: "local_pro",
      entitlement: {
        state: "active",
        plan: "pro_local",
        expires_human: "2027-07-15",
        checked: "2026-07-15",
      },
      capabilities: [],
    },
    cloud: {
      state: "connected",
      execution_available: false,
      account: {
        id: "acct_123",
        primary_email: "duan@example.com",
        display_name: "Duan Krynen",
        avatar_url: null,
        status: "active",
        created_at: 1_752_537_600_000,
      },
      entitlements: [{
        plan: "pro_cloud",
        state: "active",
        effective_at: 1_752_537_600_000,
        access_until: null,
        export_until: null,
        updated_at: 1_752_537_600_000,
      }],
      usage: {
        monthlyCredits: 100,
        weeklyCredits: 50,
        shortWindowCredits: 10,
        activeJobs: 0,
        queuedJobs: 0,
        resetAt: {
          monthly: 1_755_216_000_000,
          weekly: 1_753_142_400_000,
          five_hour: 1_752_555_600_000,
        },
      },
    },
  },
}));

vi.mock("@/lib/hooks", () => ({
  useAsync: () => ({
    data: productState,
    error: null,
    loading: false,
    reload: vi.fn(),
  }),
  useInterval: vi.fn(),
}));

function responsiveGridColumns(element: HTMLElement, viewportWidth: number): number {
  const breakpoints: Record<string, number> = { sm: 640, md: 768, lg: 1024, xl: 1280 };
  let columns = 1;

  for (const token of element.className.split(/\s+/u)) {
    const match = /^(?:(sm|md|lg|xl):)?grid-cols-(\d+)$/u.exec(token);
    if (!match) continue;
    const [, breakpoint, count] = match;
    if (!breakpoint || viewportWidth >= breakpoints[breakpoint]) columns = Number(count);
  }
  return columns;
}

describe("Account responsive product lanes", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("keeps all lane status content in a single column at the 768px sidebar breakpoint", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 768 });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    act(() => root.render(createElement(Account)));

    const localTitle = getByText(container, "Local", { selector: ".font-semibold" });
    const lanes = localTitle.closest(".grid");
    expect(lanes).not.toBeNull();
    expect(responsiveGridColumns(lanes as HTMLElement, window.innerWidth)).toBe(1);

    const renderedLanes = within(lanes as HTMLElement);
    expect(renderedLanes.getByText("Ready")).toBeTruthy();
    expect(renderedLanes.getByText("Local Pro", { selector: ".font-semibold" })).toBeTruthy();
    expect(renderedLanes.getByText("Cloud", { selector: ".font-semibold" })).toBeTruthy();
    expect(renderedLanes.getAllByText("Active")).toHaveLength(2);

    act(() => root.unmount());
  });
});
