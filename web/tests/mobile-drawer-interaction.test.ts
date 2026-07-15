// @vitest-environment jsdom

import { createElement, Fragment, useRef, useState } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { getByRole, getAllByRole, within } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/theme", () => ({
  useTheme: () => ({ theme: "dark", toggle: vi.fn() }),
}));

vi.mock("@/lib/hooks", () => ({
  useAsync: () => ({ data: null, reload: vi.fn() }),
  useInterval: vi.fn(),
}));

function installMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

async function interact(action: () => Promise<unknown>) {
  await act(async () => {
    await action();
  });
}

function ShellHarness() {
  const [open, setOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  return createElement(
    Fragment,
    null,
    createElement(Sidebar, {
      mobileOpen: open,
      onClose: () => setOpen(false),
      menuButtonRef,
    }),
    createElement(Topbar, {
      mobileOpen: open,
      onMenu: () => setOpen(true),
      menuButtonRef,
    }),
  );
}

function TestRouter() {
  return createElement(MemoryRouter, {
    future: { v7_startTransition: true, v7_relativeSplatPath: true },
  }, createElement(ShellHarness));
}

describe("mobile navigation drawer interactions", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installMatchMedia(false);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    act(() => {
      root.render(createElement(TestRouter));
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("removes the closed mobile drawer from keyboard and accessibility navigation", async () => {
    const user = userEvent.setup();
    const aside = document.querySelector("aside");
    const trigger = getByRole(document.body, "button", { name: "Open navigation" });

    expect(aside).not.toBeNull();
    expect(aside?.hasAttribute("inert")).toBe(true);
    expect(aside?.getAttribute("aria-hidden")).toBe("true");

    await user.tab();
    expect(document.activeElement).toBe(trigger);
  });

  it("moves focus inside, traps Tab in both directions, and restores focus on Escape", async () => {
    const user = userEvent.setup();
    const trigger = getByRole(document.body, "button", { name: "Open navigation" });

    await interact(() => user.click(trigger));

    const aside = document.querySelector("aside");
    expect(aside).not.toBeNull();
    expect(aside?.hasAttribute("inert")).toBe(false);
    expect(aside?.hasAttribute("aria-hidden")).toBe(false);
    expect(aside?.contains(document.activeElement)).toBe(true);

    const firstLink = aside?.querySelector<HTMLAnchorElement>('a[href="/"]');
    const lastLink = within(aside as HTMLElement).getByRole("link", { name: "Settings" });
    expect(firstLink).not.toBeNull();

    lastLink.focus();
    await user.tab();
    expect(document.activeElement).toBe(firstLink);

    firstLink?.focus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(document.activeElement).toBe(lastLink);

    await interact(() => user.keyboard("{Escape}"));
    expect(document.activeElement).toBe(trigger);
    expect(aside?.hasAttribute("inert")).toBe(true);
    expect(aside?.getAttribute("aria-hidden")).toBe("true");
  });

  it("keeps overlay-click dismissal and restores focus to the menu trigger", async () => {
    const user = userEvent.setup();
    const trigger = getByRole(document.body, "button", { name: "Open navigation" });
    await interact(() => user.click(trigger));

    const aside = document.querySelector("aside");
    const overlay = getAllByRole(document.body, "button", { name: "Close navigation" })
      .find((button) => !aside?.contains(button));
    expect(overlay).toBeDefined();

    await interact(() => user.click(overlay as HTMLButtonElement));
    expect(aside?.hasAttribute("inert")).toBe(true);
    expect(document.activeElement).toBe(trigger);
  });
});

describe("desktop navigation", () => {
  it("keeps the sidebar exposed and keyboard reachable when the drawer state is closed", async () => {
    installMatchMedia(true);
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const user = userEvent.setup();

    act(() => {
      root.render(createElement(TestRouter));
    });

    const aside = document.querySelector("aside");
    expect(aside).not.toBeNull();
    expect(aside?.hasAttribute("inert")).toBe(false);
    expect(aside?.hasAttribute("aria-hidden")).toBe(false);

    await user.tab();
    expect(aside?.contains(document.activeElement)).toBe(true);

    act(() => root.unmount());
    container.remove();
  });
});
