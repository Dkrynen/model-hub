// Pure tab/dirty/save-state logic for the Workbench editor surface.
// No React, no DOM, no fetch — unit-tested with vitest.

export type TabKind = "file" | "diff";

export interface WorkbenchTab {
  kind: TabKind;
  /** file tabs: project-relative path; diff tabs (plan 3): staged change id */
  key: string;
}

export interface TabsState {
  tabs: WorkbenchTab[];
  active: string | null;
}

export const emptyTabs: TabsState = { tabs: [], active: null };

export function tabId(tab: WorkbenchTab): string {
  return `${tab.kind}:${tab.key}`;
}

export function findTab(state: TabsState, id: string): WorkbenchTab | undefined {
  return state.tabs.find((tab) => tabId(tab) === id);
}

export function openTab(state: TabsState, tab: WorkbenchTab): TabsState {
  const id = tabId(tab);
  if (findTab(state, id)) {
    return state.active === id ? state : { ...state, active: id };
  }
  return { tabs: [...state.tabs, tab], active: id };
}

export function activateTab(state: TabsState, id: string): TabsState {
  if (!findTab(state, id) || state.active === id) return state;
  return { ...state, active: id };
}

export function closeTab(state: TabsState, id: string): TabsState {
  const index = state.tabs.findIndex((tab) => tabId(tab) === id);
  if (index === -1) return state;
  const tabs = state.tabs.filter((_, i) => i !== index);
  if (state.active !== id) return { tabs, active: state.active };
  const neighbor = tabs[index] ?? tabs[index - 1] ?? null;
  return { tabs, active: neighbor ? tabId(neighbor) : null };
}

export function filePathOfTabId(id: string): string | null {
  return id.startsWith("file:") ? id.slice("file:".length) : null;
}

export function hasDirtyFileTabs(state: TabsState, dirty: ReadonlySet<string>): boolean {
  return state.tabs.some((tab) => tab.kind === "file" && dirty.has(tab.key));
}

// --- save-state machine (per path) ----------------------------------------
// Single-flight per path: beginSave returns null while a save is in flight.
// This closes the concurrent same-path response-misattribution race carried
// from plan 1's final review by construction (one in-flight save per path).

export type SavePhase = "idle" | "saving" | "conflict" | "missing";

export interface SaveState {
  phase: SavePhase;
  /** conflict only: sha256 of the disk content the merge view shows */
  diskSha256: string | null;
}

export const idleSave: SaveState = { phase: "idle", diskSha256: null };

export function beginSave(state: SaveState): SaveState | null {
  if (state.phase === "saving") return null;
  return { phase: "saving", diskSha256: null };
}

export function saveSucceeded(): SaveState {
  return idleSave;
}

export function saveFailed(): SaveState {
  return idleSave;
}

export function saveConflicted(diskSha256: string | null): SaveState {
  return { phase: "conflict", diskSha256 };
}

/** Target vanished from disk: the next save recreates it (base null). */
export function saveTargetMissing(): SaveState {
  return { phase: "missing", diskSha256: null };
}

/** The base_sha256 the next save must send given the current save state. */
export function saveBase(state: SaveState, bufferBaseSha: string | null): string | null {
  if (state.phase === "missing") return null;
  if (state.phase === "conflict") return state.diskSha256;
  return bufferBaseSha;
}

// --- language routing ------------------------------------------------------

export interface LanguageChoice {
  id: "python" | "javascript" | "json" | "markdown" | "html" | "css" | "yaml";
  typescript?: boolean;
  jsx?: boolean;
}

export function languageIdForPath(path: string): LanguageChoice | null {
  const name = path.slice(path.lastIndexOf("/") + 1).toLowerCase();
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = name.slice(dot + 1);
  switch (ext) {
    case "py":
    case "pyw":
      return { id: "python" };
    case "js":
    case "mjs":
    case "cjs":
      return { id: "javascript" };
    case "jsx":
      return { id: "javascript", jsx: true };
    case "ts":
      return { id: "javascript", typescript: true };
    case "tsx":
      return { id: "javascript", typescript: true, jsx: true };
    case "json":
      return { id: "json" };
    case "md":
    case "markdown":
      return { id: "markdown" };
    case "html":
    case "htm":
      return { id: "html" };
    case "css":
      return { id: "css" };
    case "yml":
    case "yaml":
      return { id: "yaml" };
    default:
      return null;
  }
}
