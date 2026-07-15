// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { getByRole, getByText, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Performance } from "@/pages/performance";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  performanceDiagnostics: vi.fn(),
  performanceProbe: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

const diagnostics = {
  model: "alpha:7b",
  installed_models: ["alpha:7b", "beta:7b"],
  installed_models_reported: true,
  running_models: [],
  running_models_reported: true,
  history: [],
  latest: null,
  diagnosis: { state: "unmeasured", summary: "No usable measurement.", signals: [], actions: [] },
};

describe("Performance Doctor probe identity", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.performanceDiagnostics.mockResolvedValue(diagnostics);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("locks model identity until the in-flight probe settles", async () => {
    let release!: (value: object) => void;
    apiMocks.performanceProbe.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));

    await act(async () => {
      root.render(createElement(Performance, { embedded: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const user = userEvent.setup();
    const run = await waitFor(() => getByRole(container, "button", { name: "Run probe" }));
    await act(async () => { await user.click(run); });

    const modelSelect = getByRole(container, "combobox") as HTMLButtonElement;
    expect(modelSelect.disabled).toBe(true);
    expect(apiMocks.performanceProbe).toHaveBeenCalledWith("alpha:7b");

    await act(async () => {
      release({
        model: "alpha:7b",
        state: "done",
        metrics: { model: "alpha:7b", tokens_per_second: 12, protocol_id: "lac.quick-latency.v1", num_ctx: 4096 },
        diagnosis: { state: "ok", summary: "Measured alpha.", signals: [], actions: [] },
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await waitFor(() => expect(getByText(container, "Measured alpha.")).toBeTruthy());
    expect(modelSelect.disabled).toBe(false);
  });

  it("renders an Ollama probe rejection as a bounded failure state", async () => {
    apiMocks.performanceProbe.mockRejectedValueOnce(new Error("Ollama unavailable"));

    await act(async () => {
      root.render(createElement(Performance, { embedded: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const user = userEvent.setup();
    const run = await waitFor(() => getByRole(container, "button", { name: "Run probe" }));
    await act(async () => { await user.click(run); });

    await waitFor(() => expect(getByText(container, "Ollama unavailable")).toBeTruthy());
    expect((getByRole(container, "combobox") as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not present an unavailable residency report as not resident", async () => {
    apiMocks.performanceDiagnostics.mockResolvedValue({
      ...diagnostics,
      running_models_reported: false,
    });

    await act(async () => {
      root.render(createElement(Performance, { embedded: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await waitFor(() => expect(getByText(container, "residency not reported")).toBeTruthy());
  });
});
