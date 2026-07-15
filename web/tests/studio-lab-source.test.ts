import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) => readFileSync(new URL(relative, import.meta.url), "utf8");
const app = read("../src/App.tsx");
const sidebar = read("../src/components/sidebar.tsx");
const dashboard = read("../src/pages/dashboard.tsx");
const installed = read("../src/pages/installed.tsx");
const studio = read("../src/pages/chat.tsx");
const performance = read("../src/pages/performance.tsx");
const lab = read("../src/pages/lab.tsx") + read("../src/components/lab/model-compare.tsx");

describe("Studio and Lab product contract", () => {
  it("uses canonical routes while keeping query-preserving legacy links", () => {
    expect(app).toContain('path="/studio"');
    expect(app).toContain('path="/lab"');
    expect(app).toContain('path="/chat"');
    expect(app).toContain('path="/performance"');
    expect(app).toContain('to="/studio" preserveLocation');
    expect(app).toContain('to="/lab" preserveLocation');
    expect(sidebar).toContain('{ to: "/studio", label: "Studio"');
    expect(sidebar).toContain('{ to: "/lab", label: "Lab"');
    expect(installed).toContain('/studio?model=');
  });

  it("opens prompt drafts in Studio without auto-running them", () => {
    expect(dashboard).toContain("<StudioLauncher />");
    expect(studio).toContain('title="Studio"');
    expect(studio).toContain("What do you want to work on?");
    expect(studio).toContain("onClick={() => setInput(s)}");
    expect(studio).not.toContain("onClick={() => send(s)}");
    expect(studio).toContain('params.has("prompt")');
  });

  it("keeps the hardened Studio internals while putting conversation before output", () => {
    expect(app).toContain('location.pathname === "/studio"');
    expect(studio).toContain("min-[1440px]:grid-cols-[250px_minmax(480px,1fr)_minmax(400px,620px)]");
    expect(studio).toContain('aria-label="Studio panes"');
    expect(studio).toContain("model output — not executed");
    const filesPanel = studio.indexOf('id="studio-files-panel"');
    const runPanel = studio.indexOf('id="studio-chat-panel"');
    const outputPanel = studio.indexOf('id="studio-editor-panel"');
    expect(filesPanel).toBeGreaterThan(-1);
    expect(runPanel).toBeGreaterThan(filesPanel);
    expect(outputPanel).toBeGreaterThan(runPanel);
  });

  it("labels Lab measurements and non-measurements honestly", () => {
    expect(lab).toContain("One diagnostic sample, not a quality benchmark.");
    expect(lab).toContain("Not directly comparable");
    expect(lab).toContain("Not reported");
    expect(lab).toContain("Not measured");
    expect(lab).toContain("Models run sequentially");
    expect(lab).toContain("No ranking:");
    expect(lab).toContain("<caption");
  });

  it("binds Measure results to the model that started the request", () => {
    expect(performance).toContain("probeGeneration");
    expect(performance).toContain("disabled={probing}");
  });
});
