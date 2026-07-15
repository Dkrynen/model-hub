import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const sidebarSource = readFileSync(new URL("../src/components/sidebar.tsx", import.meta.url), "utf8");
const topbarSource = readFileSync(new URL("../src/components/topbar.tsx", import.meta.url), "utf8");

describe("responsive application shell", () => {
  it("keeps narrow content inside the viewport", () => {
    expect(appSource).toContain("min-w-0 flex-1 overflow-x-hidden overflow-y-auto");
    expect(topbarSource).toContain("relative min-w-0 max-w-md flex-1");
  });

  it("keeps every navigation link reachable in short mobile drawers", () => {
    expect(sidebarSource).toContain("min-h-0 flex-1 overflow-y-auto");
  });
});
