import { describe, expect, it } from "vitest";

import {
  MAX_STUDIO_PROMPT_LENGTH,
  parseStudioLaunch,
  studioLaunchNavigation,
} from "@/lib/studio-launch";

describe("Studio launch contract", () => {
  it("keeps a bounded draft in router state instead of the URL", () => {
    const navigation = studioLaunchNavigation({ mode: "build", prompt: "  Inspect auth & plan  " });

    expect(navigation).toEqual({
      path: "/studio",
      state: {
        studioLaunch: {
          mode: "build",
          prompt: "Inspect auth & plan",
          model: "",
        },
      },
    });
    expect(navigation.path).not.toContain("prompt=");
  });

  it("falls back to Plan and preserves only a bounded draft", () => {
    const params = new URLSearchParams({
      mode: "execute",
      prompt: `  ${"x".repeat(MAX_STUDIO_PROMPT_LENGTH + 40)}  `,
      model: "qwen2.5-coder:7b",
    });

    expect(parseStudioLaunch(params)).toEqual({
      mode: "plan",
      prompt: "x".repeat(MAX_STUDIO_PROMPT_LENGTH),
      model: "qwen2.5-coder:7b",
    });
  });

  it("drops blank prompt and model values", () => {
    expect(parseStudioLaunch(new URLSearchParams("mode=ask&prompt=+++&model=++"))).toEqual({
      mode: "ask",
      prompt: "",
      model: "",
    });
  });

  it("prefers bounded router state while retaining legacy query parsing", () => {
    expect(parseStudioLaunch(
      new URLSearchParams("mode=ask&prompt=legacy&model=old"),
      { studioLaunch: { mode: "build", prompt: " private draft ", model: "qwen:tiny" } },
    )).toEqual({
      mode: "build",
      prompt: "private draft",
      model: "qwen:tiny",
    });
  });
});
