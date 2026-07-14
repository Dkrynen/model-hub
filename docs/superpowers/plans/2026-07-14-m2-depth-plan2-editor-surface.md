# M2 Depth Plan 2 — Editor Surface (CodeMirror 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Build Mode a real editing surface: a Cursor-shape 3-column layout (files+staged left, CodeMirror editor center, chat right) with editable file tabs, Ctrl+S save through Plan 1's edit-and-stage route, a merge-view save-conflict flow, and vitest entering the repo via a pure tab/save-state module.

**Architecture:** All CM6 code lives behind one `React.lazy` chunk (`editor-pane.tsx` and its imports) so `/chat`'s initial chunk is unchanged. Page-level state stays in `chat.tsx` via a `useEditorTabs(projectId)` hook whose pure transitions live in `web/src/lib/workbench-tabs.ts` (vitest-tested, no DOM). The editor shows **disk truth only** (existing `GET /api/projects/<id>/file`); saves go through `POST /api/projects/<id>/file/save` (Plan 1) and are single-flighted per path, which closes Plan 1's carried concurrent-save race by construction.

**Tech Stack:** React 18.3 + Vite 8 + Tailwind 3.4 (existing), CodeMirror 6 (`codemirror`, `@codemirror/merge`, language packs — all local ESM, no workers, no CDN), vitest (new, pure-module tests only), Flask/pytest (one small backend fix).

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`, branch `master` — **local-only, NEVER push (patent hold)**.
- Commits are auto-signed by repo-local config. Never `--no-verify`.
- Spec (do not copy into this public repo): workspace repo `docs/superpowers/specs/2026-07-14-lac-m2-diff-editor-design.md`. This plan implements spec §10 item 2 (+ the two Low findings carried from Plan 1's final review). Diff tabs for *staged review*, staged-queue extraction, SSE refresh, copy ride-along, and release gates are **Plan 3 — do not build them here**.
- **No external URLs anywhere in the editor stack** — every CM6 import is bundled ESM. No CDN loaders, no `fetch` to non-`/api` origins.
- The editor renders **disk truth only** — never the agent's staged overlay.
- Breakpoint is the custom **`min-[960px]:`** variant (pywebview `min_size=(1024,700)` is the OUTER window; `lg:`/`xl:` would stack at minimum size). Below 960px: top-level `[Files | Editor | Chat]` tabs.
- Python tests: `.venv\Scripts\python.exe -m pytest <file> -v` from repo root. Web gates in `web/`: `npm run typecheck` (bare — never pipe, it masks the exit code), `npm run build`, and (new) `npm run test` (vitest).
- Line anchors were verified 2026-07-14 against working-tree master @ `2dfe966`; re-check ±20 lines if a hunk doesn't match.
- Adjudicated deviations from the spec, carried for the final review (do NOT re-litigate mid-task):
  1. **Session-switch dirty guard skipped** — editor tabs are project-scoped and survive session switches unchanged, so there is nothing to lose; guards land on tab close and project/workspace switch (where tabs reset).
  2. **Binary tree rows are not pre-marked** — the listing endpoint gives no encoding hint, so "visible but not openable" is realized as: the row opens a tab that shows the "binary or non-previewable" notice (spec §6 row).
  3. **Staged queue moves to the left rail as the existing `StagedChangesPanel` component unchanged** (collapsible section). Its extraction to `staged-queue.tsx` with diff-tab Review is Plan 3.

---

### Task 1: Backend carry-over — lone-surrogate save content → 415, not 500

Plan 1's final review confirmed: JSON like `"content": "\ud800"` parses to a Python str with a lone surrogate, passes the previewable-text check, then raises an uncaught `UnicodeEncodeError` at the size-check `content.encode("utf-8")` → Flask 500. Fail-closed but wrong signal; fix to 415 before any staging.

**Files:**
- Modify: `backend/api.py` — the size check inside `api_project_file_save` (search for `MAX_STAGED_BYTES` in the route; the route starts near `:2030`).
- Test: `tests/test_project_file_save.py` (append)

**Interfaces:**
- Produces: save route returns `415 {"code": "project_file_not_previewable"}` for content that cannot be UTF-8-encoded. Consumed by Task 5's hook (it already maps 415 → "not editable" toast).

- [ ] **Step 1: Write the failing test** (append to `tests/test_project_file_save.py`)

```python
def test_save_lone_surrogate_content_415(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    resp = _save(client, pid, "a.txt", "bad \ud800 text", None)
    assert resp.status_code == 415, resp.get_json()
    assert resp.get_json()["code"] == "project_file_not_previewable"
    assert not (tmp_path / "a.txt").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_file_save.py::test_save_lone_surrogate_content_415 -v`
Expected: FAIL — 500 (uncaught `UnicodeEncodeError`), NOT 415.

- [ ] **Step 3: Implement**

In `api_project_file_save`, replace the size-check block:

```python
    if len(content.encode("utf-8")) > MAX_STAGED_BYTES:
```

with:

```python
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        return jsonify({
            "error": "content is not supported as previewable text",
            "code": "project_file_not_previewable",
        }), 415
    if content_size > MAX_STAGED_BYTES:
```

(Keep the existing 413 body under the `if` unchanged.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_file_save.py -v`
Expected: all PASS (the whole file, not just the new test).

- [ ] **Step 5: Commit**

```bash
git add backend/api.py tests/test_project_file_save.py
git commit -m "fix(api): lone-surrogate save content returns 415 instead of 500"
```

---

### Task 2: Dependencies — CodeMirror 6 + vitest

**Files:**
- Modify: `web/package.json`, `web/package-lock.json` (via npm only — never hand-edit the lock)

**Interfaces:**
- Produces: importable `codemirror`, `@codemirror/state|view|language|commands|merge`, `@codemirror/lang-{python,javascript,json,markdown,html,css,yaml}`, `@lezer/highlight`, and dev-dep `vitest`. Consumed by Tasks 3–5.

- [ ] **Step 1: Install runtime deps** (run in `web/`)

```bash
npm install codemirror@^6 @codemirror/state@^6 @codemirror/view@^6 @codemirror/language@^6 @codemirror/commands@^6 @codemirror/merge@^6 @codemirror/lang-python@^6 @codemirror/lang-javascript@^6 @codemirror/lang-json@^6 @codemirror/lang-markdown@^6 @codemirror/lang-html@^6 @codemirror/lang-css@^6 @codemirror/lang-yaml@^6 @lezer/highlight@^1
```

- [ ] **Step 2: Install vitest**

```bash
npm install -D vitest@^3
```

- [ ] **Step 3: Gates** (in `web/`)

Run: `npm run typecheck` then `npm run build`
Expected: both exit 0 (no code changed; deps resolve cleanly).

- [ ] **Step 4: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(web): add CodeMirror 6 + vitest dependencies"
```

---

### Task 3: Pure tab/save-state module + vitest harness

**Files:**
- Create: `web/src/lib/workbench-tabs.ts`
- Create: `web/src/lib/workbench-tabs.test.ts`
- Create: `web/vitest.config.ts`
- Modify: `web/package.json` — `"test": "vitest run"` (replaces the dead `node --test tests/*.test.ts`; `web/tests/` does not exist)

**Interfaces:**
- Produces (Tasks 5–7 code against these exact names):
  - `type TabKind = "file" | "diff"`, `interface WorkbenchTab { kind: TabKind; key: string }`, `interface TabsState { tabs: WorkbenchTab[]; active: string | null }`
  - `emptyTabs`, `tabId(tab)`, `findTab(state, id)`, `openTab(state, tab)`, `activateTab(state, id)`, `closeTab(state, id)`, `filePathOfTabId(id)`, `hasDirtyFileTabs(state, dirty)`
  - `type SavePhase = "idle" | "saving" | "conflict" | "missing"`, `interface SaveState { phase: SavePhase; diskSha256: string | null }`, `idleSave`, `beginSave(state)` (returns `null` while saving = **single-flight per path**, closing Plan 1's carried concurrent-save finding), `saveSucceeded()`, `saveFailed()`, `saveConflicted(diskSha256)`, `saveTargetMissing()`, `saveBase(state, bufferBaseSha)`
  - `interface LanguageChoice { id: ...; typescript?: boolean; jsx?: boolean }`, `languageIdForPath(path)`
- The `"diff"` tab kind is machinery for Plan 3 — Plan 2 only ever creates `"file"` tabs.

- [ ] **Step 1: Create `web/vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
```

- [ ] **Step 2: Update the test script** in `web/package.json`:

```json
    "test": "vitest run",
```

- [ ] **Step 3: Write the failing tests** — `web/src/lib/workbench-tabs.test.ts`

```typescript
import { describe, expect, it } from "vitest";
import {
  activateTab,
  beginSave,
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
```

- [ ] **Step 4: Run to verify failure**

Run in `web/`: `npm run test`
Expected: FAIL — cannot resolve `./workbench-tabs`.

- [ ] **Step 5: Implement `web/src/lib/workbench-tabs.ts`**

```typescript
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
```

- [ ] **Step 6: Run to verify pass + gates**

Run in `web/`: `npm run test` — all PASS. Then `npm run typecheck` — exit 0.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/workbench-tabs.ts web/src/lib/workbench-tabs.test.ts web/vitest.config.ts web/package.json
git commit -m "feat(web): workbench tab/save pure module + vitest harness"
```

---

### Task 4: CM6 theme + code editor wrapper

**Files:**
- Create: `web/src/components/workbench/cm-theme.ts`
- Create: `web/src/components/workbench/code-editor.tsx`

**Interfaces:**
- Consumes: `languageIdForPath` from Task 3; CSS design tokens (`var(--surface)` etc., see `web/src/index.css:8-52`).
- Produces: `lacEditorTheme`, `lacSyntaxHighlighting` (cm-theme.ts); `CodeEditor` component with props `{ path: string; doc: string; docVersion: number; onChange: (doc: string) => void; onSave: () => void }`. Consumed by Task 5.
- NOTE: these files are not imported by anything until Task 5 — `npm run build` won't bundle them yet; `npm run typecheck` still checks them.

- [ ] **Step 1: Create `web/src/components/workbench/cm-theme.ts`**

```typescript
// Undergrowth editor theme bound to the existing CSS design tokens
// (src/index.css) so light/dark palette flips apply automatically.
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

export const lacEditorTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "var(--surface)",
      color: "var(--text)",
      fontSize: "12.5px",
      height: "100%",
    },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55" },
    ".cm-content": { caretColor: "var(--accent)" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: "var(--accent-soft)" },
    ".cm-gutters": {
      backgroundColor: "var(--surface)",
      color: "var(--text-faint)",
      border: "none",
      borderRight: "1px solid var(--border)",
    },
    ".cm-activeLine": { backgroundColor: "rgba(228, 232, 226, 0.04)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text-muted)" },
    ".cm-matchingBracket": { backgroundColor: "var(--accent-soft)", outline: "none" },
    "&.cm-focused": { outline: "none" },
  },
  { dark: true }
);

export const lacHighlightStyle = HighlightStyle.define([
  { tag: [t.keyword, t.moduleKeyword, t.controlKeyword, t.operatorKeyword], color: "var(--accent)" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "var(--success)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--info)" },
  { tag: [t.comment], color: "var(--text-faint)", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "var(--accent-hover)" },
  { tag: [t.typeName, t.className, t.namespace, t.tagName], color: "var(--warning)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--text)" },
  { tag: [t.operator, t.punctuation, t.bracket], color: "var(--text-muted)" },
  { tag: t.heading, color: "var(--text)", fontWeight: "600" },
  { tag: [t.link, t.url], color: "var(--info)", textDecoration: "underline" },
  { tag: t.invalid, color: "var(--danger)" },
]);

export const lacSyntaxHighlighting = syntaxHighlighting(lacHighlightStyle);
```

- [ ] **Step 2: Create `web/src/components/workbench/code-editor.tsx`**

```tsx
// CodeMirror 6 wrapper: uncontrolled view, controlled identity.
// The view is (re)created when the tab identity (path) or an external
// reload (docVersion) changes; keystrokes flow OUT through onChange.
import { useEffect, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching, indentOnInput } from "@codemirror/language";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { yaml } from "@codemirror/lang-yaml";
import { languageIdForPath } from "@/lib/workbench-tabs";
import { lacEditorTheme, lacSyntaxHighlighting } from "./cm-theme";

function languageExtension(path: string): Extension {
  const choice = languageIdForPath(path);
  if (!choice) return [];
  switch (choice.id) {
    case "python":
      return python();
    case "javascript":
      return javascript({ typescript: choice.typescript, jsx: choice.jsx });
    case "json":
      return json();
    case "markdown":
      return markdown();
    case "html":
      return html();
    case "css":
      return css();
    case "yaml":
      return yaml();
  }
}

interface CodeEditorProps {
  path: string;
  doc: string;
  /** bump to force the view to reload `doc` from outside (create, save-again) */
  docVersion: number;
  onChange: (doc: string) => void;
  onSave: () => void;
}

export function CodeEditor({ path, doc, docVersion, onChange, onSave }: CodeEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;
  const docRef = useRef(doc);
  docRef.current = doc;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: docRef.current,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightSpecialChars(),
          highlightActiveLine(),
          history(),
          bracketMatching(),
          indentOnInput(),
          languageExtension(path),
          lacEditorTheme,
          lacSyntaxHighlighting,
          keymap.of([
            // WebView2 swallows Ctrl+S into the host page otherwise;
            // preventDefault is load-bearing — verify in the packaged app.
            { key: "Mod-s", preventDefault: true, run: () => { onSaveRef.current(); return true; } },
            ...defaultKeymap,
            ...historyKeymap,
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onChangeRef.current(update.state.doc.toString());
          }),
        ],
      }),
      parent: host,
    });
    return () => view.destroy();
    // doc intentionally NOT a dependency: content changes flow out via
    // onChange; docVersion is the explicit external-reload signal.
  }, [path, docVersion]);

  return <div ref={hostRef} className="h-full min-h-0 overflow-hidden" />;
}
```

- [ ] **Step 3: Gates**

Run in `web/`: `npm run typecheck` then `npm run build`
Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/workbench/cm-theme.ts web/src/components/workbench/code-editor.tsx
git commit -m "feat(web): CodeMirror editor wrapper + Undergrowth theme"
```

---

### Task 5: Save-conflict merge view + editor-tabs hook + editor pane

**Files:**
- Create: `web/src/components/workbench/diff-view.tsx` (save-conflict mode only; staged-review mode is Plan 3)
- Create: `web/src/components/workbench/use-editor-tabs.ts`
- Create: `web/src/components/workbench/editor-pane.tsx` (**default export** — Task 6 lazy-loads it)

**Interfaces:**
- Consumes: Task 3 pure module; Task 4 `CodeEditor` + theme; `api.projectFile` (`web/src/lib/api.ts:355`), `api.saveProjectFile` (`:462`); `normalizeProjectFilePath` (`web/src/lib/project-files.ts:56`).
- Produces:
  - `useEditorTabs(projectId: string)` returning `{ tabs, buffers, dirty, hasDirty, openFile(path, options?: { create?: boolean }), activate(id), close(id), updateDoc(path, doc), save(path), saveAgain(path, content), keepEditing(path, content), reset() }`
  - `interface FileBuffer { phase: "loading" | "ready" | "error"; doc: string; baseSha: string | null; docVersion: number; error: { status: number | null; message: string } | null; save: SaveState; conflict: { diskContent: string; diskSha256: string | null } | null }`
  - `EditorPane` (default export) with props `{ tabs, buffers, dirty, emptyState, onActivate, onClose, onChangeDoc, onSave, onSaveAgain, onKeepEditing }`
  - `SaveConflictView` with props `{ path, diskContent, bufferContent, busy, onSaveAgain(content), onKeepEditing(content) }`
- Error behavior implements the spec §6 table rows for open 413/415/404/409 and save 409/413/415/conflict/deleted-under-buffer.

- [ ] **Step 1: Create `web/src/components/workbench/diff-view.tsx`**

```tsx
// Merge view for the save-conflict flow: disk truth (left, read-only) vs the
// user's buffer (right, editable). Staged-review mode arrives in plan 3.
import { useEffect, useRef } from "react";
import { MergeView } from "@codemirror/merge";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import { Button } from "@/components/ui/button";
import { lacEditorTheme, lacSyntaxHighlighting } from "./cm-theme";

interface SaveConflictViewProps {
  path: string;
  diskContent: string;
  bufferContent: string;
  busy: boolean;
  onSaveAgain: (editedContent: string) => void;
  onKeepEditing: (editedContent: string) => void;
}

export function SaveConflictView({
  path,
  diskContent,
  bufferContent,
  busy,
  onSaveAgain,
  onKeepEditing,
}: SaveConflictViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mergeRef = useRef<MergeView | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const shared = [lineNumbers(), lacEditorTheme, lacSyntaxHighlighting];
    const view = new MergeView({
      a: {
        doc: diskContent,
        extensions: [...shared, EditorState.readOnly.of(true), EditorView.editable.of(false)],
      },
      b: { doc: bufferContent, extensions: shared },
      parent: host,
    });
    mergeRef.current = view;
    return () => {
      mergeRef.current = null;
      view.destroy();
    };
  }, [diskContent, bufferContent, path]);

  const currentRight = () => mergeRef.current?.b.state.doc.toString() ?? bufferContent;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 truncate text-[12px] text-warning">
          Disk changed since this file was loaded — left is disk, right is your edit.
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onKeepEditing(currentRight())}>
            Keep editing
          </Button>
          <Button size="sm" disabled={busy} onClick={() => onSaveAgain(currentRight())}>
            {busy ? "Saving…" : "Save again"}
          </Button>
        </div>
      </div>
      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto" />
    </div>
  );
}
```

- [ ] **Step 2: Create `web/src/components/workbench/use-editor-tabs.ts`**

```typescript
// Page-level editor state: open tabs, dirty buffers, per-path save flow.
// Pure transitions live in @/lib/workbench-tabs; this hook owns the
// side-effects (fetch, save, toasts) with the project/sequence guards the
// workbench already uses everywhere.
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import { normalizeProjectFilePath } from "@/lib/project-files";
import {
  type SaveState,
  type TabsState,
  activateTab,
  beginSave,
  closeTab,
  emptyTabs,
  filePathOfTabId,
  idleSave,
  openTab,
  saveBase,
  saveConflicted,
  saveFailed,
  saveSucceeded,
  saveTargetMissing,
} from "@/lib/workbench-tabs";

export interface FileBuffer {
  phase: "loading" | "ready" | "error";
  doc: string;
  baseSha: string | null;
  docVersion: number;
  error: { status: number | null; message: string } | null;
  save: SaveState;
  conflict: { diskContent: string; diskSha256: string | null } | null;
}

const freshBuffer = (): FileBuffer => ({
  phase: "ready",
  doc: "",
  baseSha: null,
  docVersion: 0,
  error: null,
  save: idleSave,
  conflict: null,
});

function openErrorMessage(status: number | null): string {
  if (status === 413) return "This file is too large to open (1 MB limit).";
  if (status === 415) return "This file is binary or not previewable text.";
  if (status === 404) return "This file is no longer available.";
  if (status === 409) {
    return "This project registration is no longer valid because its folder identity changed. Restore the original registered folder, then refresh.";
  }
  if (status === 403) return "This project item is protected and cannot be opened.";
  return "This file could not be opened.";
}

function saveFailureToast(error: unknown): { title: string; description: string } {
  if (error instanceof ApiError) {
    if (error.status === 413) {
      return { title: "File too large to save", description: "Editor saves are capped at 2 MB." };
    }
    if (error.status === 415) {
      return { title: "File is not editable", description: "The target on disk is binary or not previewable text." };
    }
    if (error.status === 409) {
      return {
        title: "Save blocked",
        description: "This project registration is no longer valid; re-pick the project and try again.",
      };
    }
    return { title: "Save failed", description: error.message };
  }
  return { title: "Save failed", description: error instanceof Error ? error.message : String(error) };
}

function isSaveConflictBody(body: unknown): body is { code: "save_conflict"; disk_sha256: string | null } {
  return Boolean(
    body &&
    typeof body === "object" &&
    (body as { code?: unknown }).code === "save_conflict"
  );
}

export function useEditorTabs(projectId: string) {
  const [tabs, setTabs] = useState<TabsState>(emptyTabs);
  const [buffers, setBuffers] = useState<Map<string, FileBuffer>>(new Map());
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const buffersRef = useRef(buffers);
  buffersRef.current = buffers;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const projectIdRef = useRef(projectId);
  const sequenceRef = useRef(0);

  const patchBuffer = useCallback((path: string, patch: Partial<FileBuffer>) => {
    setBuffers((current) => {
      const existing = current.get(path);
      if (!existing) return current;
      const next = new Map(current);
      next.set(path, { ...existing, ...patch });
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    sequenceRef.current += 1;
    setTabs(emptyTabs);
    setBuffers(new Map());
    setDirty(new Set());
  }, []);

  useEffect(() => {
    if (projectIdRef.current !== projectId) {
      projectIdRef.current = projectId;
      reset();
    }
  }, [projectId, reset]);

  useEffect(() => () => {
    sequenceRef.current += 1;
  }, []);

  const isCurrent = useCallback(
    (sequence: number, pid: string) =>
      sequenceRef.current === sequence && projectIdRef.current === pid,
    []
  );

  const loadBuffer = useCallback(
    async (path: string) => {
      const sequence = sequenceRef.current;
      const pid = projectIdRef.current;
      try {
        const detail = await api.projectFile(pid, path);
        if (!isCurrent(sequence, pid)) return;
        setBuffers((current) => {
          const existing = current.get(path);
          if (!existing) return current;
          const next = new Map(current);
          next.set(path, {
            ...existing,
            phase: "ready",
            doc: detail.content,
            baseSha: detail.sha256,
            docVersion: existing.docVersion + 1,
            error: null,
          });
          return next;
        });
      } catch (error) {
        if (!isCurrent(sequence, pid)) return;
        const status = error instanceof ApiError ? error.status : null;
        patchBuffer(path, { phase: "error", error: { status, message: openErrorMessage(status) } });
      }
    },
    [isCurrent, patchBuffer]
  );

  const openFile = useCallback(
    (path: string, options: { create?: boolean } = {}) => {
      let relative: string;
      try {
        relative = normalizeProjectFilePath(path, false);
      } catch {
        toast.error("That file path is not valid");
        return;
      }
      setTabs((current) => openTab(current, { kind: "file", key: relative }));
      if (buffersRef.current.has(relative)) return;
      if (options.create) {
        setBuffers((current) => new Map(current).set(relative, freshBuffer()));
        setDirty((current) => new Set(current).add(relative));
        return;
      }
      setBuffers((current) =>
        new Map(current).set(relative, { ...freshBuffer(), phase: "loading" })
      );
      void loadBuffer(relative);
    },
    [loadBuffer]
  );

  const activate = useCallback((id: string) => {
    setTabs((current) => activateTab(current, id));
  }, []);

  const close = useCallback((id: string) => {
    const path = filePathOfTabId(id);
    if (
      path &&
      dirtyRef.current.has(path) &&
      !window.confirm(`Discard unsaved changes to ${path}?`)
    ) {
      return;
    }
    setTabs((current) => closeTab(current, id));
    if (path) {
      setBuffers((current) => {
        if (!current.has(path)) return current;
        const next = new Map(current);
        next.delete(path);
        return next;
      });
      setDirty((current) => {
        if (!current.has(path)) return current;
        const next = new Set(current);
        next.delete(path);
        return next;
      });
    }
  }, []);

  const updateDoc = useCallback(
    (path: string, doc: string) => {
      patchBuffer(path, { doc });
      setDirty((current) => (current.has(path) ? current : new Set(current).add(path)));
    },
    [patchBuffer]
  );

  const save = useCallback(
    async (path: string, override?: { content: string; baseSha: string | null }) => {
      const buffer = buffersRef.current.get(path);
      if (!buffer || buffer.phase !== "ready") return;
      const begun = beginSave(buffer.save);
      if (!begun) return; // single-flight per path
      const sequence = sequenceRef.current;
      const pid = projectIdRef.current;
      const content = override ? override.content : buffer.doc;
      const base = override ? override.baseSha : saveBase(buffer.save, buffer.baseSha);
      patchBuffer(path, {
        save: begun,
        ...(override
          ? { doc: content, docVersion: buffer.docVersion + 1, conflict: null }
          : {}),
      });
      try {
        const result = await api.saveProjectFile(pid, { path, content, base_sha256: base });
        if (!isCurrent(sequence, pid)) return;
        patchBuffer(path, { baseSha: result.sha256, save: saveSucceeded(), conflict: null });
        setDirty((current) => {
          const next = new Set(current);
          next.delete(path);
          return next;
        });
        toast.success("Saved", { description: path });
      } catch (error) {
        if (!isCurrent(sequence, pid)) return;
        if (error instanceof ApiError && error.status === 409 && isSaveConflictBody(error.body)) {
          const diskSha = error.body.disk_sha256;
          if (diskSha === null) {
            // We sent a base but the file is gone: next Save recreates it.
            patchBuffer(path, { save: saveTargetMissing(), conflict: null });
            toast.warning("File is gone from disk", { description: "Save again to recreate it." });
            return;
          }
          try {
            const disk = await api.projectFile(pid, path);
            if (!isCurrent(sequence, pid)) return;
            patchBuffer(path, {
              save: saveConflicted(disk.sha256),
              conflict: { diskContent: disk.content, diskSha256: disk.sha256 },
            });
          } catch (fetchError) {
            if (!isCurrent(sequence, pid)) return;
            if (fetchError instanceof ApiError && fetchError.status === 404) {
              patchBuffer(path, { save: saveTargetMissing(), conflict: null });
              toast.warning("File is gone from disk", { description: "Save again to recreate it." });
            } else {
              patchBuffer(path, { save: saveFailed() });
              toast.error("Save conflict", {
                description: "Disk changed and the fresh copy could not be loaded. Try again.",
              });
            }
          }
          return;
        }
        patchBuffer(path, { save: saveFailed() });
        const failure = saveFailureToast(error);
        toast.error(failure.title, { description: failure.description });
      }
    },
    [isCurrent, patchBuffer]
  );

  const saveAgain = useCallback(
    (path: string, editedContent: string) => {
      const buffer = buffersRef.current.get(path);
      void save(path, {
        content: editedContent,
        baseSha: buffer?.conflict?.diskSha256 ?? null,
      });
    },
    [save]
  );

  const keepEditing = useCallback(
    (path: string, editedContent: string) => {
      const buffer = buffersRef.current.get(path);
      if (!buffer) return;
      patchBuffer(path, {
        doc: editedContent,
        docVersion: buffer.docVersion + 1,
        conflict: null,
        save: idleSave,
      });
    },
    [patchBuffer]
  );

  return {
    tabs,
    buffers,
    dirty,
    hasDirty: dirty.size > 0,
    openFile,
    activate,
    close,
    updateDoc,
    save: (path: string) => void save(path),
    saveAgain,
    keepEditing,
    reset,
  };
}
```

- [ ] **Step 3: Create `web/src/components/workbench/editor-pane.tsx`** (default export for `React.lazy`)

```tsx
// Center column of the workbench: tab strip + active buffer.
// Loaded via React.lazy so CodeMirror stays out of /chat's initial chunk.
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type TabsState, filePathOfTabId, tabId } from "@/lib/workbench-tabs";
import { CodeEditor } from "./code-editor";
import { SaveConflictView } from "./diff-view";
import type { FileBuffer } from "./use-editor-tabs";

interface EditorPaneProps {
  tabs: TabsState;
  buffers: ReadonlyMap<string, FileBuffer>;
  dirty: ReadonlySet<string>;
  emptyState: ReactNode;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onChangeDoc: (path: string, doc: string) => void;
  onSave: (path: string) => void;
  onSaveAgain: (path: string, content: string) => void;
  onKeepEditing: (path: string, content: string) => void;
}

export default function EditorPane({
  tabs,
  buffers,
  dirty,
  emptyState,
  onActivate,
  onClose,
  onChangeDoc,
  onSave,
  onSaveAgain,
  onKeepEditing,
}: EditorPaneProps) {
  const activePath = tabs.active ? filePathOfTabId(tabs.active) : null;
  const buffer = activePath ? buffers.get(activePath) : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {tabs.tabs.length > 0 && (
        <div
          role="tablist"
          aria-label="Open editor tabs"
          className="flex items-center gap-0.5 overflow-x-auto border-b border-line px-1.5 py-1"
        >
          {tabs.tabs.map((tab) => {
            const id = tabId(tab);
            const active = tabs.active === id;
            const label = tab.key.slice(tab.key.lastIndexOf("/") + 1);
            const isDirty = tab.kind === "file" && dirty.has(tab.key);
            return (
              <div
                key={id}
                className={cn(
                  "flex shrink-0 items-center rounded",
                  active ? "bg-panel-3" : "hover:bg-panel-2"
                )}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  title={tab.key}
                  className={cn(
                    "max-w-[180px] truncate px-2 py-1 text-[12px]",
                    active ? "text-fg" : "text-fg-muted"
                  )}
                  onClick={() => onActivate(id)}
                >
                  {isDirty && (
                    <span
                      aria-label="Unsaved changes"
                      className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-warning align-middle"
                    />
                  )}
                  {label}
                </button>
                <button
                  type="button"
                  aria-label={`Close ${label}`}
                  className="rounded p-0.5 text-fg-faint hover:text-fg"
                  onClick={() => onClose(id)}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="min-h-0 flex-1">
        {!activePath || !buffer ? (
          <div className="flex h-full items-center justify-center p-4">{emptyState}</div>
        ) : buffer.phase === "loading" ? (
          <div role="status" aria-live="polite" className="p-4 text-[12.5px] text-fg-muted">
            Loading {activePath}…
          </div>
        ) : buffer.phase === "error" ? (
          <div
            role="alert"
            className="m-4 rounded border border-warning/30 bg-warning-soft p-3 text-[12.5px] text-warning"
          >
            {buffer.error?.message ?? "This file could not be opened."}
          </div>
        ) : buffer.conflict ? (
          <SaveConflictView
            path={activePath}
            diskContent={buffer.conflict.diskContent}
            bufferContent={buffer.doc}
            busy={buffer.save.phase === "saving"}
            onSaveAgain={(content) => onSaveAgain(activePath, content)}
            onKeepEditing={(content) => onKeepEditing(activePath, content)}
          />
        ) : (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              <CodeEditor
                path={activePath}
                doc={buffer.doc}
                docVersion={buffer.docVersion}
                onChange={(doc) => onChangeDoc(activePath, doc)}
                onSave={() => onSave(activePath)}
              />
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-line px-3 py-1.5">
              <div className="min-w-0 truncate text-[11px] text-fg-faint">
                {activePath}
                {buffer.save.phase === "missing" && " — gone from disk; Save recreates it"}
              </div>
              <Button
                size="sm"
                disabled={buffer.save.phase === "saving" || !dirty.has(activePath)}
                onClick={() => onSave(activePath)}
              >
                {buffer.save.phase === "saving" ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Gates**

Run in `web/`: `npm run typecheck` then `npm run build` then `npm run test`
Expected: all exit 0 (pure-module tests still green; new components typecheck; nothing imports them yet so the bundle is unchanged).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/workbench/diff-view.tsx web/src/components/workbench/use-editor-tabs.ts web/src/components/workbench/editor-pane.tsx
git commit -m "feat(web): editor pane, save-conflict merge view, editor-tabs hook"
```

---

### Task 6: chat.tsx layout restructure — Cursor shape at `min-[960px]:`

Target: **left rail 260px** (ContextPicker → Files/Threads navigator → staged section), **center = lazy EditorPane**, **right rail 380px = chat** (model+mode header, transcript, approval, sandbox status, composer, collapsible Run details). Below 960px: `[Files | Editor | Chat]` top-level tabs. This task MOVES existing blocks — inner JSX of moved blocks stays byte-identical unless a change is called out.

**Files:**
- Modify: `web/src/pages/chat.tsx` (anchors as of `2dfe966`)

**Interfaces:**
- Consumes: Task 5 hook + `EditorPane` default export.
- Produces: `pendingStagedPaths: Set<string>` memo and the left-rail Files region where Task 7 mounts the tree; `openFileInEditor(path, options?)` handler Task 7 wires to.

- [ ] **Step 1: Imports + module-level lazy component**

At the top of `chat.tsx`:
- Extend the react import (`:1`) to `import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";`
- Add: `import { useEditorTabs } from "@/components/workbench/use-editor-tabs";`
- Below the import block add:

```tsx
const EditorPane = lazy(() => import("@/components/workbench/editor-pane"));
```

- [ ] **Step 2: New page state + handlers** (inside `Chat()`, after the `navigatorView` state at `:218`)

```tsx
  const [mobilePane, setMobilePane] = useState<"files" | "editor" | "chat">("chat");
  const [stagedOpen, setStagedOpen] = useState(true);
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const editor = useEditorTabs(selectedProjectId);

  const openFileInEditor = (path: string, options?: { create?: boolean }) => {
    editor.openFile(path, options);
    setMobilePane("editor");
  };

  const pendingStagedPaths = useMemo(
    () =>
      new Set(
        stagedChanges
          .filter((change) => change.status === "pending")
          .map((change) => change.path)
      ),
    [stagedChanges]
  );
```

(`openFileInEditor` is consumed in Task 7; if `tsc` flags it as unused at the end of THIS task, prefix-suppress is forbidden — instead mount it early by passing it where Task 7's step 2 shows, or accept it if the tsconfig does not enable noUnusedLocals. Check `web/tsconfig.json` before deciding; if `noUnusedLocals` is on, fold Task 7's Step 2 swap into this task's Step 5 Files region directly.)

- [ ] **Step 3: Dirty guards on context switches**

At the very top of `switchWorkspace` (`:602`) and `switchProject` (`:613`), before anything else:

```tsx
    if (editor.hasDirty && !window.confirm("Discard unsaved editor changes?")) return;
```

(The hook resets itself when `selectedProjectId` changes; no other wiring needed. Session switches deliberately have NO guard — editor tabs are project-scoped and survive them; deviation 1 in Global Constraints.)

- [ ] **Step 4: Narrow-viewport pane tabs**

Directly under the `<PageHeader …>…</PageHeader>` block (`:1242-1249`), insert:

```tsx
      <div role="tablist" aria-label="Workbench panes" className="mb-3 flex gap-1 min-[960px]:hidden">
        {([["files", "Files"], ["editor", "Editor"], ["chat", "Chat"]] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={mobilePane === id}
            className={cn(
              "flex-1 rounded border px-2 py-1.5 text-[12.5px] font-semibold transition-colors",
              mobilePane === id
                ? "border-verdant bg-verdant-soft/40 text-fg"
                : "border-line bg-panel text-fg-muted hover:text-fg"
            )}
            onClick={() => setMobilePane(id)}
          >
            {label}
          </button>
        ))}
      </div>
```

- [ ] **Step 5: Rebuild the grid** — replace the outer grid div (`:1251`) and re-home the three columns.

New grid wrapper:

```tsx
      <div className="grid min-h-[520px] grid-cols-1 gap-3 min-[960px]:h-[calc(100vh-150px)] min-[960px]:grid-cols-[260px_minmax(0,1fr)_380px]">
```

(The pane-tab row is hidden ≥960px so the 150px header allowance is unchanged; if the rendered page shows a vertical scrollbar at 1024×700 in dev, adjust the calc value until it doesn't and note it in the report.)

**LEFT rail** (visibility: `mobilePane === "files" ? "flex" : "hidden"` + `min-[960px]:flex`):

```tsx
        <aside
          className={cn(
            mobilePane === "files" ? "flex" : "hidden",
            "h-[520px] min-h-[320px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[960px]:flex min-[960px]:h-auto min-[960px]:min-h-0"
          )}
        >
          {/* MOVED UNCHANGED: ContextPicker block (:1253-1266) */}

          {/* KEPT UNCHANGED: navigator tabs block (:1284-1312) */}
          {/* KEPT UNCHANGED for this task: Files/Threads regions (:1314-1366) —
              ProjectFilesPanel stays until Task 7 replaces it with FileTree */}

          {(stagedChanges.length > 0 || selectedChange) && (
            <div className="flex max-h-[45%] flex-col border-t border-line">
              <button
                type="button"
                aria-expanded={stagedOpen}
                className="flex items-center justify-between px-3 py-2 text-left"
                onClick={() => setStagedOpen((open) => !open)}
              >
                <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                  Staged changes
                </span>
                <Badge variant={pendingStagedPaths.size ? "warning" : "neutral"}>
                  {pendingStagedPaths.size} pending
                </Badge>
              </button>
              {stagedOpen && (
                <div className="min-h-0 overflow-y-auto px-3 pb-3">
                  {/* MOVED UNCHANGED: <StagedChangesPanel …/> invocation from :1501-1510 */}
                </div>
              )}
            </div>
          )}
        </aside>
```

NOTE: the sandbox-status block (`:1268-1282`) does NOT stay here — it moves to the right rail (Step 6). The `StagedChangesPanel` component itself (`:1790-1880`) is untouched; only its render site moves. Its own header already shows a "N pending" badge — acceptable duplication for this plan; Plan 3's extraction cleans it up.

**CENTER column** (editor):

```tsx
        <section
          className={cn(
            mobilePane === "editor" ? "flex" : "hidden",
            "min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[960px]:flex min-[960px]:min-h-0"
          )}
        >
          <Suspense
            fallback={<div className="p-4 text-[12.5px] text-fg-muted">Loading editor…</div>}
          >
            <EditorPane
              tabs={editor.tabs}
              buffers={editor.buffers}
              dirty={editor.dirty}
              emptyState={
                <div className="flex flex-col items-center text-center">
                  <Sparkles className="mb-3 h-7 w-7 text-verdant" />
                  <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => send(s)}
                        disabled={sessionLoading || projectMissing}
                        className="min-h-[54px] rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[13px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              }
              onActivate={editor.activate}
              onClose={editor.close}
              onChangeDoc={editor.updateDoc}
              onSave={editor.save}
              onSaveAgain={editor.saveAgain}
              onKeepEditing={editor.keepEditing}
            />
          </Suspense>
        </section>
```

(The old center-section empty state `:1412-1427` is superseded by this `emptyState` prop; the old transcript render moves right in Step 6. Delete the old center `<section>` entirely once its pieces are re-homed.)

- [ ] **Step 6: RIGHT rail (chat column) — structure**

```tsx
        <aside
          className={cn(
            mobilePane === "chat" ? "flex" : "hidden",
            "min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[960px]:flex min-[960px]:min-h-0"
          )}
        >
          <div className="border-b border-line px-3 py-2">
            {/* MOVED UNCHANGED: model Select + warming/resident badges block (:1371-1393),
                keeping its outer <div className="flex min-w-0 flex-1 items-center gap-2"> */}
            <div className="mt-2 flex items-center gap-1 rounded border border-line bg-panel-2 p-0.5">
              {/* MOVED UNCHANGED: MODES.map ModeButton loop body (:1396-1407) */}
            </div>
          </div>

          {/* MOVED: transcript scroller (:1411-1435) — keep ref={scrollRef} and
              className="min-h-0 flex-1 overflow-y-auto p-4". Two changes:
              (a) the messages.length === 0 branch renders this placeholder instead
                  of the old suggestion grid:
                    <div className="flex h-full items-center justify-center text-center text-[12.5px] text-fg-muted">
                      Start a run — suggestions live in the editor pane.
                    </div>
              (b) drop the mx-auto max-w-4xl wrapper class on the messages branch
                  (the rail is 380px) — keep space-y-5. */}

          {approval.pending && (
            <div className="border-t border-line p-3">
              {/* MOVED UNCHANGED: ApprovalCard invocation (:1492-1495) */}
            </div>
          )}

          {mode === "build" && selectedProject && (
            <div
              id="workbench-sandbox-status"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              className="border-t border-line px-3 pb-3"
            >
              {/* MOVED UNCHANGED: SandboxStatusPanel invocation (:1276-1280) */}
            </div>
          )}

          {/* MOVED UNCHANGED: composer form block (:1437-1471) — keep its
              "border-t border-line p-3" wrapper */}

          <div className="border-t border-line">
            <button
              type="button"
              aria-expanded={runDetailsOpen}
              className="flex w-full items-center justify-between px-3 py-2 text-left"
              onClick={() => setRunDetailsOpen((open) => !open)}
            >
              <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                Run details
              </span>
              <Badge variant="neutral">{events.length} events</Badge>
            </button>
            {runDetailsOpen && (
              <div className="max-h-[40vh] space-y-3 overflow-y-auto px-3 pb-3">
                {/* MOVED: Run stat tiles grid (:1484-1487) — keep the two StatTiles,
                    drop the old "Run" header row (:1476-1483) */}
                {/* MOVED UNCHANGED: System label + textarea (:1515-1524) */}
                {/* MOVED UNCHANGED: Events label + list (:1528-1541) */}
                {/* MOVED UNCHANGED: Stats label + tiles (:1545-1555) */}
              </div>
            )}
          </div>
        </aside>
```

Wiring notes for the implementer:
- Every moved block keeps its exact handlers/state names (`send`, `stop`, `input`, `system`, `events`, `lastStats`, `approval`, `sandboxCheck`…). Nothing about run/session logic changes in this task.
- Delete the now-empty old center `<section>` and old right `<aside>` shells after re-homing. `StatTile`, `RunEvent`, `formatMs` etc. stay in-file and in use.
- Border seams: moved blocks that had `border-b` may need `border-t` instead (as shown) so seams read correctly in their new order.
- The old mode-badge (`:1480-1482`, `Badge` showing `modeLabel(mode)`) is superseded by the mode toggle now living in the same rail — drop it.

- [ ] **Step 7: Gates**

Run in `web/`: `npm run typecheck` then `npm run build` then `npm run test`
Expected: all exit 0.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/chat.tsx
git commit -m "feat(web): cursor-shape 960px workbench layout - editor center, chat right"
```

---

### Task 7: File tree — lazy expandable tree with staged badges + New file

Replaces the read-only `ProjectFilesPanel` in the left rail's Files region. Keeps the proven guards: sequence counter bumped on project change/unmount, per-request currency checks before commit (pattern from `project-files-panel.tsx:67-131`).

**Files:**
- Create: `web/src/components/workbench/file-tree.tsx`
- Modify: `web/src/pages/chat.tsx` — swap `ProjectFilesPanel` → `FileTree` in the Files region; remove the old import
- Delete: `web/src/components/workbench/project-files-panel.tsx` (chat.tsx was its only consumer — verify with a grep before deleting; `web/src/lib/project-files.ts` STAYS, `api.ts` imports it)

**Interfaces:**
- Consumes: `api.projectFiles(projectId, path, signal)` (`api.ts:341`), `projectFileChildPath` (`project-files.ts:72`), `normalizeProjectFilePath` (`project-files.ts:56`), Task 6's `openFileInEditor` + `pendingStagedPaths`.
- Produces: `FileTree` with props `{ projectId: string; pendingPaths: ReadonlySet<string>; onOpenFile: (path: string, options?: { create?: boolean }) => void }`.

- [ ] **Step 1: Create `web/src/components/workbench/file-tree.tsx`**

```tsx
// Expandable project file tree. Each directory level is fetched lazily from
// the existing one-level listing endpoint; commits are guarded by a sequence
// counter bumped on project change/unmount (pattern from the retired
// read-only panel). Binary files are openable here by design — the editor
// tab shows the "binary or non-previewable" notice on 415 (spec §6).
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, FilePlus2, FileText, Folder, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import { normalizeProjectFilePath, projectFileChildPath } from "@/lib/project-files";
import type { ProjectFileEntry } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface DirState {
  loading: boolean;
  error: string;
  entries: ProjectFileEntry[];
  truncated: boolean;
}

interface FileTreeProps {
  projectId: string;
  pendingPaths: ReadonlySet<string>;
  onOpenFile: (path: string, options?: { create?: boolean }) => void;
}

function listErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "This project registration is no longer valid because its folder identity changed. Restore the original registered folder, then refresh.";
    }
    if (error.status === 404) return "This folder is no longer available.";
    if (error.status === 403) return "This folder is protected.";
  }
  return "Project files are temporarily unavailable.";
}

function hasPendingDescendant(pendingPaths: ReadonlySet<string>, dirPath: string): boolean {
  for (const path of pendingPaths) {
    if (path === dirPath || path.startsWith(`${dirPath}/`)) return true;
  }
  return false;
}

export function FileTree({ projectId, pendingPaths, onOpenFile }: FileTreeProps) {
  const [dirs, setDirs] = useState<Map<string, DirState>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const sequenceRef = useRef(0);
  const projectIdRef = useRef(projectId);

  const loadDir = useCallback(async (path: string) => {
    const sequence = sequenceRef.current;
    const pid = projectIdRef.current;
    if (!pid) return;
    setDirs((current) => {
      const next = new Map(current);
      const existing = current.get(path);
      next.set(path, {
        loading: true,
        error: "",
        entries: existing?.entries ?? [],
        truncated: existing?.truncated ?? false,
      });
      return next;
    });
    try {
      const result = await api.projectFiles(pid, path);
      if (sequenceRef.current !== sequence || projectIdRef.current !== pid) return;
      setDirs((current) => {
        const next = new Map(current);
        next.set(path, {
          loading: false,
          error: "",
          entries: result.entries,
          truncated: result.truncated,
        });
        return next;
      });
    } catch (error) {
      if (sequenceRef.current !== sequence || projectIdRef.current !== pid) return;
      setDirs((current) => {
        const next = new Map(current);
        next.set(path, { loading: false, error: listErrorMessage(error), entries: [], truncated: false });
        return next;
      });
    }
  }, []);

  useEffect(() => {
    projectIdRef.current = projectId;
    sequenceRef.current += 1;
    setDirs(new Map());
    setExpanded(new Set());
    if (projectId) void loadDir("");
    return () => {
      sequenceRef.current += 1;
    };
  }, [loadDir, projectId]);

  const toggleDir = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
        if (!dirs.get(path)) void loadDir(path);
      }
      return next;
    });
  };

  const refresh = () => {
    sequenceRef.current += 1;
    setDirs(new Map());
    setExpanded(new Set());
    void loadDir("");
  };

  const createFile = () => {
    const input = window.prompt("New file path (relative to the project root)");
    if (!input) return;
    let relative: string;
    try {
      relative = normalizeProjectFilePath(input.trim().replace(/\\/g, "/"), false);
    } catch {
      toast.error("That file path is not valid");
      return;
    }
    onOpenFile(relative, { create: true });
  };

  const renderLevel = (path: string, depth: number): JSX.Element | null => {
    const dir = dirs.get(path);
    if (!dir) return null;
    return (
      <div role="group">
        {dir.error ? (
          <div
            role="alert"
            className="mx-2 my-1 rounded border border-danger/30 bg-danger-soft p-2 text-[11.5px] text-danger"
          >
            {dir.error}
            <Button size="sm" variant="ghost" className="mt-1 h-6 px-2" onClick={() => void loadDir(path)}>
              Retry
            </Button>
          </div>
        ) : dir.loading && !dir.entries.length ? (
          <div
            role="status"
            className="px-2 py-1.5 text-[11.5px] text-fg-muted"
            style={{ paddingLeft: 8 + depth * 14 }}
          >
            Loading…
          </div>
        ) : (
          <>
            {dir.entries.map((entry) => {
              const childPath = projectFileChildPath(path, entry.name);
              if (entry.type === "dir") {
                const open = expanded.has(childPath);
                const Chevron = open ? ChevronDown : ChevronRight;
                return (
                  <div key={`dir:${childPath}`}>
                    <button
                      type="button"
                      title={childPath}
                      aria-expanded={open}
                      className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[12.5px] text-fg-muted hover:bg-panel-3 hover:text-fg"
                      style={{ paddingLeft: 8 + depth * 14 }}
                      onClick={() => toggleDir(childPath)}
                    >
                      <Chevron className="h-3.5 w-3.5 shrink-0 text-fg-faint" />
                      <Folder className="h-4 w-4 shrink-0 text-warning" />
                      <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                      {hasPendingDescendant(pendingPaths, childPath) && (
                        <span
                          aria-label="Contains pending staged changes"
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
                        />
                      )}
                    </button>
                    {open && renderLevel(childPath, depth + 1)}
                  </div>
                );
              }
              return (
                <button
                  key={`file:${childPath}`}
                  type="button"
                  title={childPath}
                  className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[12.5px] text-fg-muted hover:bg-panel-3 hover:text-fg"
                  style={{ paddingLeft: 8 + depth * 14 + 18 }}
                  onClick={() => onOpenFile(childPath)}
                >
                  <FileText className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  {pendingPaths.has(childPath) && (
                    <span
                      aria-label="Pending staged change"
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
                    />
                  )}
                </button>
              );
            })}
            {dir.truncated && (
              <div
                role="status"
                className="px-2 py-1 text-[11px] text-warning"
                style={{ paddingLeft: 8 + depth * 14 }}
              >
                …more entries not shown
              </div>
            )}
            {!dir.entries.length && !dir.loading && (
              <div
                className="px-2 py-1.5 text-[11.5px] text-fg-faint"
                style={{ paddingLeft: 8 + depth * 14 }}
              >
                Empty folder
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-1 border-b border-line px-2 py-2">
        <span className="pl-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          Files
        </span>
        <div className="flex items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            aria-label="New file"
            disabled={!projectId}
            onClick={createFile}
          >
            <FilePlus2 />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            aria-label="Refresh project files"
            disabled={!projectId}
            onClick={refresh}
          >
            <RefreshCw className={cn(dirs.get("")?.loading && "animate-spin")} />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {!projectId ? (
          <div className="px-3 py-8 text-center text-[12.5px] text-fg-muted">
            Select a registered project
          </div>
        ) : (
          renderLevel("", 0)
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Swap it into chat.tsx**

- Replace the import `import { ProjectFilesPanel } from "@/components/workbench/project-files-panel";` with `import { FileTree } from "@/components/workbench/file-tree";`
- In the left rail's Files region (kept verbatim in Task 6), replace

```tsx
              <ProjectFilesPanel key={selectedProjectId} projectId={selectedProjectId} />
```

with

```tsx
              <FileTree
                key={selectedProjectId}
                projectId={selectedProjectId}
                pendingPaths={pendingStagedPaths}
                onOpenFile={openFileInEditor}
              />
```

- [ ] **Step 3: Delete the retired panel**

First verify no other consumer: `grep -r "project-files-panel" web/src` → only the (now updated) chat.tsx import may match; then delete `web/src/components/workbench/project-files-panel.tsx`. Do NOT touch `web/src/lib/project-files.ts` (api.ts depends on it).

- [ ] **Step 4: Gates**

Run in `web/`: `npm run typecheck` then `npm run build` then `npm run test`
Expected: all exit 0.

- [ ] **Step 5: Commit**

```bash
git add -A web/src/components/workbench web/src/pages/chat.tsx
git commit -m "feat(web): lazy file tree with staged badges + new-file, retires read-only panel"
```

---

### Task 8: Full verification + ledger (controller-run)

- [ ] **Step 1: Full non-live pytest**

Run: `.venv\Scripts\python.exe -m pytest -m "not live" -q`
Expected: green except the ONE known pre-existing red on master: `tests/test_release_readiness.py::test_build_workflow_verifies_source_version_and_uploads_checksum` (documented pre-existing; NOT ours — do not fix, do not mask). Environment skips (symlink/Ollama) are expected.

- [ ] **Step 2: Web gates**

Run in `web/`: `npm run typecheck` && `npm run build` && `npm run test` — all exit 0.

- [ ] **Step 3: Live editor smoke (dev server or packaged, controller)**

1. Open a registered project → Files tree renders; expand a directory (lazy fetch); open a `.py` file → CM6 editor with highlighting.
2. Type → dirty dot appears; **Ctrl+S** → "Saved" toast; verify on disk; verify a `Manual edits` thread does NOT appear in Threads; verify the save shows in the Staged changes section as `applied` with a Revert button.
3. Edit the same file on disk externally, edit in the editor, Ctrl+S → save-conflict merge view (disk left, buffer right); **Save again** → applied.
4. Open a binary file → "binary or non-previewable" notice tab.
5. New file (tree header) → name it, type, Save → created on disk (`base_sha256: null` path).
6. Resize below 960px → `[Files | Editor | Chat]` tabs switch panes; ≥960px → 3 columns, no horizontal page scrollbar at 1024×700.
7. Ctrl+S must be verified in the **packaged app** (WebView2), not just the browser — if a packaged build isn't warranted mid-plan, log it as a Plan 3 release-gate carry-over in the ledger.

- [ ] **Step 4: Ledger**

Append a `# M2 depth plan 2 - editor surface (2026-07-14)` section to `.superpowers/sdd/progress.md` recording per-task commits, test counts, review verdicts, and any carried findings. `.superpowers/` is gitignored by repo design — do NOT try to commit it (Plan 1 confirmed the commit step is inoperative).

---

## Self-review (spec §10.2 coverage)

- CM6 deps + theme → Tasks 2, 4. Layout restructure (3-col + 960px + narrow tabs) → Task 6. File tree → Task 7. Editor pane/tabs → Tasks 5, 6. Save loop + conflict flow → Tasks 3, 5 (+ route fix Task 1). vitest + `workbench-tabs.ts` tests → Task 3.
- Plan 1 carried findings: (a) lone-surrogate 415 → Task 1; (b) concurrent same-path save → single-flight `beginSave` (Task 3) used by the hook (Task 5); Ctrl+S and the Save button both route through it.
- Spec §6 error table: open 413/415/404/409 → hook `openErrorMessage` + pane notices; save conflict/413/415/CAS-race → hook save flow; deleted-under-buffer → `saveTargetMissing` + recreate; truncated listing → tree row; identity drift → error copy preserved from the retired panel.
- Deliberately NOT here (Plan 3): staged-review diff tabs, staged-queue extraction, SSE/post-apply editor refresh, `product-spine.tsx` copy, external-URL scan/bundle record/min-window release gates.
