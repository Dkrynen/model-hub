// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { getByRole, getByText, queryByText, within } from "@testing-library/dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Account } from "@/pages/account";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  cloudAuthStart: vi.fn(),
  cloudLogout: vi.fn(),
  productState: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

const signedOutProduct = {
  schema_version: 1,
  execution_default: "local",
  local: { state: "ready" },
  local_pro: { state: "absent" },
  cloud: { state: "signed_out", execution_available: false },
};

const connectedProduct = {
  ...signedOutProduct,
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
    entitlements: [],
    usage: {
      monthlyCredits: 0,
      weeklyCredits: 0,
      shortWindowCredits: 0,
      activeJobs: 0,
      queuedJobs: 0,
      resetAt: {
        monthly: 1_755_216_000_000,
        weekly: 1_753_142_400_000,
        five_hour: 1_752_555_600_000,
      },
    },
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function mountAccount(): { container: HTMLElement; root: Root } {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  act(() => root.render(createElement(Account)));
  return { container, root };
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Account product-state lifecycle", () => {
  let root: Root | null = null;

  beforeEach(() => {
    apiMocks.cloudAuthStart.mockReset();
    apiMocks.cloudLogout.mockReset();
    apiMocks.productState.mockReset();
    apiMocks.cloudAuthStart.mockResolvedValue({ authorization_url: "https://example.test/auth" });
    apiMocks.cloudLogout.mockResolvedValue({});
  });

  afterEach(() => {
    if (root) act(() => root?.unmount());
    root = null;
    document.body.replaceChildren();
  });

  it("shows neutral lane states while the initial product request is unresolved", () => {
    apiMocks.productState.mockReturnValue(deferred<typeof signedOutProduct>().promise);
    const mounted = mountAccount();
    root = mounted.root;

    const lanes = getByText(mounted.container, "Local", { selector: ".font-semibold" })
      .closest(".grid") as HTMLElement;
    expect(within(lanes).getAllByText("Checking")).toHaveLength(2);
    expect(queryByText(mounted.container, "Not installed")).toBeNull();
    expect(queryByText(mounted.container, "Not configured")).toBeNull();
    expect(within(mounted.container).getByText("Checking cloud session")).toBeTruthy();
  });

  it("keeps both sign-in actions disabled until their product-state refresh resolves", async () => {
    const refresh = deferred<typeof signedOutProduct>();
    apiMocks.productState
      .mockResolvedValueOnce(signedOutProduct)
      .mockReturnValueOnce(refresh.promise);
    const mounted = mountAccount();
    root = mounted.root;
    await flushAsyncWork();

    const google = getByRole(mounted.container, "button", { name: "Google" }) as HTMLButtonElement;
    const github = getByRole(mounted.container, "button", { name: "GitHub" }) as HTMLButtonElement;
    await act(async () => {
      google.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.cloudAuthStart).toHaveBeenCalledTimes(1);
    expect(apiMocks.productState).toHaveBeenCalledTimes(2);
    expect(google.disabled).toBe(true);
    expect(github.disabled).toBe(true);
    expect(google.getAttribute("aria-busy")).toBe("true");
    google.click();
    expect(apiMocks.cloudAuthStart).toHaveBeenCalledTimes(1);

    await act(async () => {
      refresh.resolve(signedOutProduct);
      await refresh.promise;
    });
    expect(google.disabled).toBe(false);
    expect(github.disabled).toBe(false);
  });

  it("keeps sign-out disabled until the signed-out product state is loaded", async () => {
    const refresh = deferred<typeof signedOutProduct>();
    apiMocks.productState
      .mockResolvedValueOnce(connectedProduct)
      .mockReturnValueOnce(refresh.promise);
    const mounted = mountAccount();
    root = mounted.root;
    await flushAsyncWork();

    const signOut = getByRole(mounted.container, "button", { name: "Sign out" }) as HTMLButtonElement;
    await act(async () => {
      signOut.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.cloudLogout).toHaveBeenCalledTimes(1);
    expect(apiMocks.productState).toHaveBeenCalledTimes(2);
    expect(signOut.disabled).toBe(true);
    expect(signOut.getAttribute("aria-busy")).toBe("true");
    signOut.click();
    expect(apiMocks.cloudLogout).toHaveBeenCalledTimes(1);

    await act(async () => {
      refresh.resolve(signedOutProduct);
      await refresh.promise;
    });
    expect(queryByText(mounted.container, "Duan Krynen")).toBeNull();
    expect(getByRole(mounted.container, "button", { name: "Google" })).toBeTruthy();
  });
});
