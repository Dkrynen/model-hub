import { describe, expect, it } from "vitest";
import {
  activateTab,
  beginSave,
  changeIdOfTabId,
  closeTab,
  emptyTabs,
  filePathOfTabId,
  hasDirtyFileTabs,
  idleSave,
  languageIdForPath,
  openTab,
  saveBase,
  saveConflicted,
  saveFailed,
  saveSucceeded,
  saveTargetMissing,
  tabId,
  type TabsState,
} from "./workbench-tabs";

const file = (key: string) => ({ kind: "file" as const, key });

describe("tab transitions", () => {
  it("opens and activates a new tab", () => {
    const next = openTab(emptyTabs, file("src/a.py"));
    expect(next.tabs).toEqual([file("src/a.py")]);
    expect(next.active).toBe("file:src/a.py");
  });

  it("re-opening an existing tab activates it without duplicating", () => {
    let state = openTab(emptyTabs, file("a.txt"));
    state = openTab(state, file("b.txt"));
    state = openTab(state, file("a.txt"));
    expect(state.tabs).toHaveLength(2);
    expect(state.active).toBe("file:a.txt");
  });

  it("re-opening the already-active tab returns the same state object", () => {
    const state = openTab(emptyTabs, file("a.txt"));
    expect(openTab(state, file("a.txt"))).toBe(state);
  });

  it("activateTab ignores unknown ids", () => {
    const state = openTab(emptyTabs, file("a.txt"));
    expect(activateTab(state, "file:nope")).toBe(state);
  });

  it("closing the active tab activates the right neighbor, then left, then none", () => {
    let state: TabsState = emptyTabs;
    state = openTab(state, file("a"));
    state = openTab(state, file("b"));
    state = openTab(state, file("c"));
    state = activateTab(state, "file:b");
    state = closeTab(state, "file:b");
    expect(state.active).toBe("file:c");
    state = closeTab(state, "file:c");
    expect(state.active).toBe("file:a");
    state = closeTab(state, "file:a");
    expect(state.active).toBeNull();
    expect(state.tabs).toHaveLength(0);
  });

  it("closing an inactive tab keeps the current active", () => {
    let state = openTab(emptyTabs, file("a"));
    state = openTab(state, file("b"));
    state = closeTab(state, "file:a");
    expect(state.active).toBe("file:b");
    expect(state.tabs).toEqual([file("b")]);
  });

  it("closing an unknown id is a no-op", () => {
    const state = openTab(emptyTabs, file("a"));
    expect(closeTab(state, "file:zzz")).toBe(state);
  });

  it("tabId and filePathOfTabId round-trip file tabs; diff ids yield null path", () => {
    expect(tabId(file("src/x.ts"))).toBe("file:src/x.ts");
    expect(filePathOfTabId("file:src/x.ts")).toBe("src/x.ts");
    expect(filePathOfTabId("diff:abc123")).toBeNull();
  });

  it("hasDirtyFileTabs only counts open file tabs", () => {
    const state = openTab(emptyTabs, file("a.txt"));
    expect(hasDirtyFileTabs(state, new Set(["a.txt"]))).toBe(true);
    expect(hasDirtyFileTabs(state, new Set(["other.txt"]))).toBe(false);
  });

  it("changeIdOfTabId extracts the change id from a diff tab id; null for file ids", () => {
    expect(changeIdOfTabId("diff:abc123")).toBe("abc123");
    expect(changeIdOfTabId("file:src/a.ts")).toBeNull();
    expect(changeIdOfTabId("diff:")).toBe("");
  });
});

describe("save-state machine", () => {
  it("beginSave single-flights: null while already saving", () => {
    const begun = beginSave(idleSave);
    expect(begun).toEqual({ phase: "saving", diskSha256: null });
    expect(beginSave(begun!)).toBeNull();
  });

  it("beginSave is allowed from conflict and missing (retry paths)", () => {
    expect(beginSave(saveConflicted("d".repeat(64)))).not.toBeNull();
    expect(beginSave(saveTargetMissing())).not.toBeNull();
  });

  it("success and failure both return to idle", () => {
    expect(saveSucceeded()).toEqual(idleSave);
    expect(saveFailed()).toEqual(idleSave);
  });

  it("saveBase: idle uses the buffer base, conflict uses the fresh disk sha, missing uses null", () => {
    const bufferSha = "a".repeat(64);
    const diskSha = "b".repeat(64);
    expect(saveBase(idleSave, bufferSha)).toBe(bufferSha);
    expect(saveBase(saveConflicted(diskSha), bufferSha)).toBe(diskSha);
    expect(saveBase(saveTargetMissing(), bufferSha)).toBeNull();
  });
});

describe("languageIdForPath", () => {
  it("maps the supported extensions", () => {
    expect(languageIdForPath("a/b/c.py")).toEqual({ id: "python" });
    expect(languageIdForPath("x.js")).toEqual({ id: "javascript" });
    expect(languageIdForPath("x.jsx")).toEqual({ id: "javascript", jsx: true });
    expect(languageIdForPath("x.ts")).toEqual({ id: "javascript", typescript: true });
    expect(languageIdForPath("x.tsx")).toEqual({ id: "javascript", typescript: true, jsx: true });
    expect(languageIdForPath("x.json")).toEqual({ id: "json" });
    expect(languageIdForPath("README.md")).toEqual({ id: "markdown" });
    expect(languageIdForPath("index.html")).toEqual({ id: "html" });
    expect(languageIdForPath("app.css")).toEqual({ id: "css" });
    expect(languageIdForPath("ci.yml")).toEqual({ id: "yaml" });
    expect(languageIdForPath("ci.yaml")).toEqual({ id: "yaml" });
  });

  it("returns null for unknown or extension-less names", () => {
    expect(languageIdForPath("Makefile")).toBeNull();
    expect(languageIdForPath("weird.xyz")).toBeNull();
    expect(languageIdForPath("noext")).toBeNull();
  });
});
