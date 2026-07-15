export type StudioMode = "ask" | "plan" | "explore" | "build";

export const MAX_STUDIO_PROMPT_LENGTH = 4_000;
const STUDIO_MODES = new Set<StudioMode>(["ask", "plan", "explore", "build"]);

export interface StudioLaunchDraft {
  mode: StudioMode;
  prompt: string;
  model: string;
}

export interface StudioLaunchNavigation {
  path: "/studio";
  state: { studioLaunch: StudioLaunchDraft };
}

function bounded(value: string | null, maxLength: number): string {
  return (value ?? "").trim().slice(0, maxLength);
}

export function normalizeStudioMode(value: string | null): StudioMode {
  return STUDIO_MODES.has(value as StudioMode) ? value as StudioMode : "plan";
}

function stateDraft(state: unknown): Partial<Record<keyof StudioLaunchDraft, string>> {
  if (!state || typeof state !== "object") return {};
  const candidate = (state as { studioLaunch?: unknown }).studioLaunch;
  if (!candidate || typeof candidate !== "object") return {};
  const raw = candidate as Record<string, unknown>;
  return {
    mode: typeof raw.mode === "string" ? raw.mode : undefined,
    prompt: typeof raw.prompt === "string" ? raw.prompt : undefined,
    model: typeof raw.model === "string" ? raw.model : undefined,
  };
}

export function parseStudioLaunch(params: URLSearchParams, state?: unknown): StudioLaunchDraft {
  const draft = stateDraft(state);
  return {
    mode: normalizeStudioMode(draft.mode ?? params.get("mode")),
    prompt: bounded(draft.prompt ?? params.get("prompt"), MAX_STUDIO_PROMPT_LENGTH),
    model: bounded(draft.model ?? params.get("model"), 300),
  };
}

export function studioLaunchNavigation({
  mode,
  prompt,
  model = "",
}: {
  mode: StudioMode;
  prompt: string;
  model?: string;
}): StudioLaunchNavigation {
  const normalizedPrompt = bounded(prompt, MAX_STUDIO_PROMPT_LENGTH);
  const normalizedModel = bounded(model, 300);
  return {
    path: "/studio",
    state: {
      studioLaunch: {
        mode: normalizeStudioMode(mode),
        prompt: normalizedPrompt,
        model: normalizedModel,
      },
    },
  };
}
