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
    if (!expanded.has(path) && !dirs.get(path)) void loadDir(path);
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
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
