// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { getByLabelText, getByRole } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { LegacyRouteRedirect } from "@/components/legacy-route-redirect";
import { StudioLauncher } from "@/components/studio-launcher";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function LocationProbe() {
  const location = useLocation();
  return createElement("div", null,
    createElement("output", { "aria-label": "location" }, `${location.pathname}${location.search}${location.hash}`),
    createElement("output", { "aria-label": "location state" }, JSON.stringify(location.state)),
  );
}

describe("Studio draft navigation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("opens an editable Studio draft only after the user supplies a prompt", async () => {
    const user = userEvent.setup();
    act(() => root.render(createElement(
      MemoryRouter,
      { initialEntries: ["/"], future: { v7_startTransition: true, v7_relativeSplatPath: true } },
      createElement(Routes, null,
        createElement(Route, { path: "/", element: createElement(StudioLauncher) }),
        createElement(Route, { path: "/studio", element: createElement(LocationProbe) }),
      ),
    )));

    const open = getByRole(container, "button", { name: "Open draft" }) as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    await act(async () => {
      await user.type(getByLabelText(container, "What do you want to work on?"), "Inspect auth & plan");
      await user.click(getByRole(container, "button", { name: "Build" }));
    });
    expect(open.disabled).toBe(false);
    await act(async () => { await user.click(open); });

    expect(getByLabelText(container, "location").textContent).toBe("/studio");
    expect(getByLabelText(container, "location state").textContent).toContain('"prompt":"Inspect auth & plan"');
  });

  it("preserves legacy route query and hash while replacing the history entry", () => {
    act(() => root.render(createElement(
      MemoryRouter,
      {
        initialEntries: ["/chat?model=tiny%3A1b#run"],
        future: { v7_startTransition: true, v7_relativeSplatPath: true },
      },
      createElement(Routes, null,
        createElement(Route, {
          path: "/chat",
          element: createElement(LegacyRouteRedirect, { to: "/studio", preserveLocation: true }),
        }),
        createElement(Route, { path: "/studio", element: createElement(LocationProbe) }),
      ),
    )));

    expect(getByLabelText(container, "location").textContent).toBe("/studio?model=tiny%3A1b#run");
  });
});
