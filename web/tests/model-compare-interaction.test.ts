// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { getAllByRole, getAllByText, getByRole, getByText, queryByText, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCompare } from "@/components/lab/model-compare";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  installed: vi.fn(),
  modelProfiles: vi.fn(),
  performanceProbe: vi.fn(),
  ps: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

function measurement(model: string, tps: number, preGeneration: number) {
  return {
    model,
    state: "done" as const,
    metrics: {
      model,
      protocol_id: "lac.quick-latency.v1",
      num_ctx: 6144,
      tokens_per_second: tps,
      time_to_first_token_ms: preGeneration,
      load_duration_ms: 20,
      prompt_eval_duration_ms: preGeneration - 20,
      total_duration_ms: 500,
    },
  };
}

describe("Lab model comparison", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.installed.mockResolvedValue([
      { name: "beta:7b", size_gb: 5.1, modified: "", digest_short: "b" },
      { name: "alpha:7b", size_gb: 4.2, modified: "", digest_short: "a" },
    ]);
    apiMocks.ps.mockResolvedValue({ running: true, models: [{ name: "alpha:7b", size_gb: 4.2, digest_short: "a" }] });
    apiMocks.modelProfiles.mockResolvedValue({
      profiles: [
        { name: "alpha:7b", size_gb: 4.2, modified: "", digest: "sha256:aaaa", digest_short: "a", format: "gguf", family: "alpha", families: ["alpha"], parameter_size: "7B", quantization_level: "Q4_K_M", context_length: 8192 },
        { name: "beta:7b", size_gb: 5.1, modified: "", digest: "sha256:bbbb", digest_short: "b", format: "gguf", family: "beta", families: ["beta"], parameter_size: "7B", quantization_level: "Q5_K_M", context_length: 16384 },
      ],
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function renderCompare() {
    await act(async () => {
      root.render(createElement(ModelCompare));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }

  it("runs models sequentially and exposes raw samples without ranking uncontrolled residency", async () => {
    let releaseFirst!: (value: ReturnType<typeof measurement>) => void;
    const first = new Promise<ReturnType<typeof measurement>>((resolve) => { releaseFirst = resolve; });
    apiMocks.performanceProbe
      .mockImplementationOnce(() => first)
      .mockResolvedValueOnce(measurement("beta:7b", 20, 80));

    await renderCompare();
    const user = userEvent.setup();
    const run = await waitFor(() => {
      const button = getByRole(container, "button", { name: "Run comparison" }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      return button;
    });

    await act(async () => { await user.click(run); });
    expect(apiMocks.performanceProbe).toHaveBeenCalledTimes(1);
    expect(apiMocks.performanceProbe).toHaveBeenNthCalledWith(1, "alpha:7b");
    expect(
      getAllByRole(container, "combobox").every((select) => (select as HTMLButtonElement).disabled),
    ).toBe(true);

    await act(async () => {
      releaseFirst(measurement("alpha:7b", 10, 120));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(apiMocks.performanceProbe).toHaveBeenCalledTimes(2));
    expect(apiMocks.performanceProbe).toHaveBeenNthCalledWith(2, "beta:7b");

    await waitFor(() => {
      expect(getByText(container, "20.0 tok/s")).toBeTruthy();
      expect(queryByText(container, "Best")).toBeNull();
      expect(queryByText(container, "Second")).toBeNull();
      expect(getByText(container, /No ranking: Ollama residency and warm\/cold state are not controlled/)).toBeTruthy();
    });
    await waitFor(() => expect(apiMocks.ps).toHaveBeenCalledTimes(2));
    expect(getByText(container, "sha256:aaaa")).toBeTruthy();
    expect(getByText(container, "One diagnostic sample, not a quality benchmark.")).toBeTruthy();
    expect(getByText(container, /Models run sequentially to avoid simultaneous generation/)).toBeTruthy();
  });

  it("preserves a successful column when the other local probe fails", async () => {
    apiMocks.performanceProbe
      .mockResolvedValueOnce(measurement("alpha:7b", 10, 120))
      .mockRejectedValueOnce(new Error("beta runtime unavailable"));

    await renderCompare();
    const user = userEvent.setup();
    const run = await waitFor(() => {
      const button = getByRole(container, "button", { name: "Run comparison" }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      return button;
    });
    await act(async () => {
      await user.click(run);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await waitFor(() => {
      expect(getByText(container, "10.0 tok/s")).toBeTruthy();
      expect(getByText(container, "beta runtime unavailable")).toBeTruthy();
    });
    expect(getAllByText(container, "Not measured").length).toBeGreaterThan(0);
  });

  it("reports unknown residency when the local residency endpoint is unavailable", async () => {
    apiMocks.ps.mockRejectedValueOnce(new Error("residency unavailable"));

    await renderCompare();

    await waitFor(() => {
      expect(getAllByText(container, "residency not reported")).toHaveLength(2);
    });
  });

  it("fails closed when two tags resolve to the same or missing manifest identity", async () => {
    apiMocks.modelProfiles.mockResolvedValueOnce({
      profiles: [
        { name: "alpha:7b", size_gb: 4.2, modified: "", digest: "sha256:same", digest_short: "same", format: "gguf", family: "alpha", families: ["alpha"], parameter_size: "7B", quantization_level: "Q4_K_M", context_length: 8192 },
        { name: "beta:7b", size_gb: 5.1, modified: "", digest: "sha256:same", digest_short: "same", format: "gguf", family: "beta", families: ["beta"], parameter_size: "7B", quantization_level: "Q5_K_M", context_length: 16384 },
      ],
    });

    await renderCompare();

    await waitFor(() => {
      const run = getByRole(container, "button", { name: "Run comparison" }) as HTMLButtonElement;
      expect(run.disabled).toBe(true);
      expect(getByText(container, /same model manifest under two tags/i)).toBeTruthy();
    });
    expect(apiMocks.performanceProbe).not.toHaveBeenCalled();
  });

  it("fails closed when either selected tag has no manifest digest", async () => {
    apiMocks.modelProfiles.mockResolvedValueOnce({
      profiles: [
        { name: "alpha:7b", size_gb: 4.2, modified: "", digest: "sha256:alpha", digest_short: "alpha", format: "gguf", family: "alpha", families: ["alpha"], parameter_size: "7B", quantization_level: "Q4_K_M", context_length: 8192 },
        { name: "beta:7b", size_gb: 5.1, modified: "", digest: "", digest_short: "", format: "gguf", family: "beta", families: ["beta"], parameter_size: "7B", quantization_level: "Q5_K_M", context_length: 16384 },
      ],
    });

    await renderCompare();

    await waitFor(() => {
      const run = getByRole(container, "button", { name: "Run comparison" }) as HTMLButtonElement;
      expect(run.disabled).toBe(true);
      expect(getByText(container, /manifest identity was not reported/i)).toBeTruthy();
    });
  });
});
