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
  changeIdOfTabId,
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
  tabId,
} from "@/lib/workbench-tabs";
import type { StagedChangeSummary } from "@/lib/types";

export interface FileBuffer {
  phase: "loading" | "ready" | "error";
  doc: string;
  baseSha: string | null;
  docVersion: number;
  error: { status: number | null; message: string } | null;
  save: SaveState;
  conflict: { diskContent: string; diskSha256: string | null } | null;
}

export interface DiffTabState {
  phase: "loading" | "ready" | "error";
  detail: import("@/lib/types").StagedChangeDetail | null;
  stale: boolean;
  error: { status: number | null; message: string } | null;
}

function diffErrorMessage(status: number | null): string {
  if (status === 404) return "This staged change is no longer available.";
  if (status === 409) return "The project registration changed; re-pick the project.";
  return "This staged change could not be loaded.";
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
  const [diffTabs, setDiffTabs] = useState<Map<string, DiffTabState>>(new Map());
  const diffTabsRef = useRef(diffTabs);
  diffTabsRef.current = diffTabs;
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

  const patchDiff = useCallback((changeId: string, patch: Partial<DiffTabState>) => {
    setDiffTabs((current) => {
      const existing = current.get(changeId);
      if (!existing) return current;
      const next = new Map(current);
      next.set(changeId, { ...existing, ...patch });
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    sequenceRef.current += 1;
    setTabs(emptyTabs);
    setBuffers(new Map());
    setDirty(new Set());
    setDiffTabs(new Map());
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
    const changeId = changeIdOfTabId(id);
    if (changeId) {
      setDiffTabs((current) => {
        if (!current.has(changeId)) return current;
        const next = new Map(current);
        next.delete(changeId);
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
          // Keystrokes that landed during the round-trip keep the buffer dirty.
          if (buffersRef.current.get(path)?.doc !== content) return current;
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

  const loadDiff = useCallback(
    async (changeId: string) => {
      const sequence = sequenceRef.current;
      const pid = projectIdRef.current;
      try {
        const detail = await api.stagedChange(changeId);
        if (!isCurrent(sequence, pid)) return;
        setDiffTabs((current) => {
          if (!current.has(changeId)) return current;
          const next = new Map(current);
          next.set(changeId, { phase: "ready", detail, stale: false, error: null });
          return next;
        });
      } catch (error) {
        if (!isCurrent(sequence, pid)) return;
        const status = error instanceof ApiError ? error.status : null;
        patchDiff(changeId, {
          phase: "error",
          error: { status, message: diffErrorMessage(status) },
        });
      }
    },
    [isCurrent, patchDiff]
  );

  const openDiff = useCallback(
    (change: StagedChangeSummary) => {
      setTabs((current) => openTab(current, { kind: "diff", key: change.id }));
      if (diffTabsRef.current.has(change.id)) return;
      setDiffTabs((current) =>
        new Map(current).set(change.id, {
          phase: "loading",
          detail: null,
          stale: false,
          error: null,
        })
      );
      void loadDiff(change.id);
    },
    [loadDiff]
  );

  const refreshDiff = useCallback(
    (changeId: string) => {
      if (!diffTabsRef.current.has(changeId)) return;
      patchDiff(changeId, { stale: false });
      void loadDiff(changeId);
    },
    [loadDiff, patchDiff]
  );

  const markDiffStaleForPath = useCallback((path: string) => {
    setDiffTabs((current) => {
      let changed = false;
      const next = new Map(current);
      for (const [id, state] of current) {
        if (state.detail?.path === path && !state.stale) {
          next.set(id, { ...state, stale: true });
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, []);

  const closeDiffs = useCallback(() => {
    setTabs((current) => {
      const remaining = current.tabs.filter((tab) => tab.kind !== "diff");
      if (remaining.length === current.tabs.length) return current;
      const active =
        current.active && remaining.some((tab) => tabId(tab) === current.active)
          ? current.active
          : remaining.length
            ? tabId(remaining[remaining.length - 1])
            : null;
      return { tabs: remaining, active };
    });
    setDiffTabs(new Map());
  }, []);

  const syncDiskForPath = useCallback(
    (path: string) => {
      const buffer = buffersRef.current.get(path);
      if (!buffer || buffer.phase !== "ready") return;
      if (dirtyRef.current.has(path) || buffer.save.phase === "saving") return;
      void loadBuffer(path);
    },
    [loadBuffer]
  );

  return {
    tabs,
    buffers,
    dirty,
    diffTabs,
    hasDirty: dirty.size > 0,
    openFile,
    activate,
    close,
    updateDoc,
    save: (path: string) => void save(path),
    saveAgain,
    keepEditing,
    openDiff,
    refreshDiff,
    markDiffStaleForPath,
    closeDiffs,
    syncDiskForPath,
    reset,
  };
}
