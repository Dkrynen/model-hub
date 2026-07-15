import { lazy, Suspense, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Bot,
  Compass,
  FileText,
  Hammer,
  MessageSquare,
  Send,
  Settings2,
  Sparkles,
  Square,
  Trash2,
  User,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page";
import { ContextPicker } from "@/components/workbench/context-picker";
import { FileTree } from "@/components/workbench/file-tree";
import { StagedQueue } from "@/components/workbench/staged-queue";
import { useEditorTabs } from "@/components/workbench/use-editor-tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/markdown";
import { useAsync } from "@/lib/hooks";
import { ApiError, api } from "@/lib/api";
import {
  buildAgentChatPayload,
  isCurrentProjectRegistration,
  isCurrentWorkbenchContext,
  projectFilterForSelection,
  projectSelectionAfterLoad,
  selectedProjectFor,
  shouldRefreshProjectsAfterRegistration,
  workbenchContextKey,
  LEGACY_PROJECT_SELECTION,
} from "@/lib/agent-context";
import {
  approvalFailureMessage,
  createApprovalAnswer,
  initialApprovalState,
  reduceApproval,
} from "@/lib/agent-approval";
import type { ApprovalAction, PendingApproval } from "@/lib/agent-approval";
import {
  approvalMayBeRemembered,
  cancelRunThenAbort,
  parseRunTaskTarget,
  sandboxPresentation,
  shouldCommitSandboxStatus,
} from "@/lib/agent-command";
import {
  approvalLockKey,
  approvalDecisionIntent,
  agentModeNeedsProject,
  isApprovalResponseRelevant,
  isCurrentGeneration,
  isCurrentSessionAction,
  releaseApprovalLock,
  shouldCommitStagedList,
  stagedActionFailure,
  stagedFullPath,
  workbenchControlsDisabled,
  workbenchSendDisabled,
  workbenchSendLabel,
  chatStatsFromEvent,
  durableTranscript,
} from "@/lib/agent-workbench";
import type {
  ApprovalDecisionIntent,
  ChatStats,
  SessionActionIdentity,
  WorkbenchMessage,
} from "@/lib/agent-workbench";
import type {
  PsResponse,
  AgentSandboxStatus,
  ProjectInfo,
  ProjectRegistrationInput,
  SessionDetail,
  SessionEvent,
  SessionSummary,
  StagedChangeSummary,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { parseStudioLaunch, type StudioMode } from "@/lib/studio-launch";

const EditorPane = lazy(() => import("@/components/workbench/editor-pane"));

type Msg = WorkbenchMessage;
type Mode = StudioMode;

interface WorkbenchEvent {
  type: string;
  name?: string;
  ok?: boolean;
  args?: unknown;
  result?: string;
  message?: string;
  timestamp?: number;
  tool?: string;
  target?: unknown;
  path?: string;
  decision?: string;
  remember?: boolean;
}

interface SandboxCheckState {
  projectId: string;
  root: string;
  loading: boolean;
  status: AgentSandboxStatus | null;
  error: string | null;
}

const SUGGESTIONS = [
  "Map the next files to inspect before changing code.",
  "Find the safest implementation path for this project.",
  "Review the current workspace and summarize risks.",
  "Draft a small build plan with verification steps.",
];

const MODES: { id: Mode; label: string; icon: typeof MessageSquare }[] = [
  { id: "ask", label: "Ask", icon: MessageSquare },
  { id: "plan", label: "Plan", icon: FileText },
  { id: "explore", label: "Explore", icon: Compass },
  { id: "build", label: "Build", icon: Hammer },
];

const STUDIO_PANES = [["files", "Files"], ["chat", "Run"], ["editor", "Output"]] as const;

const WORKBENCH_SESSION_LIMIT = 80;

interface WorkspaceProjectsState {
  workspace: string;
  data: ProjectInfo[] | null;
  error: string | null;
  loading: boolean;
}

function useWorkspaceProjects(workspace: string) {
  const [reloadVersion, setReloadVersion] = useState(0);
  const [state, setState] = useState<WorkspaceProjectsState>({
    workspace: "",
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let active = true;
    setState((current) => ({
      workspace,
      data: current.workspace === workspace ? current.data : null,
      error: null,
      loading: true,
    }));
    api.projects(workspace)
      .then((data) => {
        if (active) setState({ workspace, data, error: null, loading: false });
      })
      .catch((error) => {
        if (active) {
          setState({
            workspace,
            data: null,
            error: error instanceof Error ? error.message : String(error),
            loading: false,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [reloadVersion, workspace]);

  const current = state.workspace === workspace;
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: current ? state.loading : true,
    reload: () => setReloadVersion((version) => version + 1),
  };
}

export function Chat() {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [launch] = useState(() => parseStudioLaunch(params, location.state));

  useEffect(() => {
    if (!params.has("prompt")) return;
    const cleaned = new URLSearchParams(params);
    cleaned.delete("prompt");
    const search = cleaned.toString();
    const priorState = location.state && typeof location.state === "object" ? location.state : {};
    navigate(
      { pathname: location.pathname, search: search ? `?${search}` : "", hash: location.hash },
      { replace: true, state: { ...priorState, studioLaunch: launch } },
    );
  }, [launch, location.hash, location.pathname, location.state, navigate, params]);
  const installed = useAsync(() => api.installed());
  const config = useAsync(() => api.config());
  const workspaces = useAsync(() => api.workspaces());
  const running = useAsync<PsResponse>(() => api.ps().catch(() => ({ running: false, models: [] })));

  const models = useMemo(() => (installed.data ?? []).map((m) => m.name), [installed.data]);
  const runningModels = useMemo(() => new Set((running.data?.models ?? []).map((m) => m.name)), [running.data]);

  const [workspace, setWorkspace] = useState("");
  const activeWorkspace = workspace || config.data?.workspace || "default";
  const projects = useWorkspaceProjects(activeWorkspace);
  const [projectSelection, setProjectSelection] = useState("");
  const availableProjects = useMemo(
    () => (projects.data ?? []).filter((project) => project.workspace === activeWorkspace),
    [activeWorkspace, projects.data]
  );
  const selectedProject = selectedProjectFor(availableProjects, projectSelection);
  const selectedProjectId = selectedProject?.id ?? "";
  const projectFilter = projectFilterForSelection(projectSelection);
  const sessions = useAsync(
    () => projectFilter
      ? api.sessions({
          workspace: activeWorkspace,
          projectId: projectFilter,
          limit: WORKBENCH_SESSION_LIMIT,
        })
      : Promise.resolve([]),
    [activeWorkspace, projectFilter]
  );

  const [model, setModel] = useState(launch.model);
  const [mode, setMode] = useState<Mode>(launch.mode);
  const [navigatorView, setNavigatorView] = useState<"threads" | "files">("threads");
  const [mobilePane, setMobilePane] = useState<"files" | "editor" | "chat">("chat");
  const [stagedOpen, setStagedOpen] = useState(true);
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const editor = useEditorTabs(selectedProjectId);

  const handleMobilePaneKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = STUDIO_PANES.findIndex(([id]) => id === mobilePane);
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1) % STUDIO_PANES.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + STUDIO_PANES.length) % STUDIO_PANES.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = STUDIO_PANES.length - 1;
    else return;
    event.preventDefault();
    const pane = STUDIO_PANES[next][0];
    setMobilePane(pane);
    event.currentTarget.querySelector<HTMLButtonElement>(`#studio-${pane}-tab`)?.focus();
  };

  const openFileInEditor = (path: string, options?: { create?: boolean }) => {
    editor.openFile(path, options);
    setMobilePane("editor");
  };
  const [messages, setMessages] = useState<Msg[]>([]);
  const [events, setEvents] = useState<WorkbenchEvent[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [input, setInput] = useState(launch.prompt);
  const [system, setSystem] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [warming, setWarming] = useState(false);
  const [lastStats, setLastStats] = useState<ChatStats | null>(null);
  const [registeringProject, setRegisteringProject] = useState(false);
  const [sandboxCheck, setSandboxCheck] = useState<SandboxCheckState>({
    projectId: "",
    root: "",
    loading: false,
    status: null,
    error: null,
  });
  const [approval, setApproval] = useState(initialApprovalState);
  const [stagedChanges, setStagedChanges] = useState<StagedChangeSummary[]>([]);
  const pendingStagedPaths = useMemo(
    () =>
      new Set(
        stagedChanges
          .filter((change) => change.status === "pending")
          .map((change) => change.path)
      ),
    [stagedChanges]
  );
  const [changeBusy, setChangeBusy] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const runInFlightRef = useRef(false);
  const approvalRef = useRef(initialApprovalState);
  const approvalLockRef = useRef("");
  const changeBusyRef = useRef("");
  const registeringProjectRef = useRef(false);
  const activeSessionRef = useRef("");
  const sessionLoadingRef = useRef(false);
  const mountedRef = useRef(true);
  const runGenerationRef = useRef(0);
  const sessionGenerationRef = useRef(0);
  const sessionLoadGenerationRef = useRef(0);
  const stagedRequestSequenceRef = useRef(0);
  const sandboxRequestSequenceRef = useRef(0);
  const registrationRequestSequenceRef = useRef(0);
  const contextGenerationRef = useRef(0);
  const workspaceContextRef = useRef(activeWorkspace);
  const activeWorkspaceRef = useRef(activeWorkspace);
  const projectSelectionRef = useRef(projectSelection);
  const selectedProjectIdRef = useRef(selectedProjectId);
  const modeRef = useRef<Mode>(mode);
  const scrollRef = useRef<HTMLDivElement>(null);

  activeWorkspaceRef.current = activeWorkspace;
  projectSelectionRef.current = projectSelection;
  selectedProjectIdRef.current = selectedProjectId;

  const applyApprovalAction = (action: ApprovalAction) => {
    const next = reduceApproval(approvalRef.current, action);
    approvalRef.current = next;
    if (mountedRef.current) setApproval(next);
    return next;
  };

  const selectSession = (sessionId: string) => {
    activeSessionRef.current = sessionId;
    setActiveSessionId(sessionId);
  };

  const resetApproval = () => {
    approvalLockRef.current = "";
    applyApprovalAction({ type: "reset" });
  };

  const isActiveRun = (generation: number) =>
    mountedRef.current && isCurrentGeneration(runGenerationRef.current, generation);

  const appendRunEvent = (event: WorkbenchEvent, generation: number) => {
    if (!isActiveRun(generation)) return;
    setEvents((current) =>
      isActiveRun(generation) ? [...current, event] : current
    );
  };

  const commitRunStats = (stats: ChatStats, generation: number) => {
    if (!isActiveRun(generation)) return;
    setLastStats((current) => isActiveRun(generation) ? stats : current);
  };

  const invalidateActiveRun = () => {
    runGenerationRef.current += 1;
    const controller = abortRef.current;
    abortRef.current = null;
    controller?.abort();
    resetApproval();
  };

  const cancelActiveRun = () => {
    cancelRunThenAbort(
      approvalRef.current.run,
      (runId, approvalToken) => api.cancelAgentRun(runId, approvalToken),
      invalidateActiveRun
    );
  };

  const clearStagedContext = () => {
    sessionGenerationRef.current += 1;
    stagedRequestSequenceRef.current += 1;
    changeBusyRef.current = "";
    setChangeBusy("");
    setStagedChanges([]);
    editor.closeDiffs();
  };

  const beginSessionContext = (sessionId: string) => {
    cancelActiveRun();
    clearStagedContext();
    selectSession(sessionId);
    return sessionGenerationRef.current;
  };

  const cancelSessionLoad = () => {
    sessionLoadGenerationRef.current += 1;
    sessionLoadingRef.current = false;
    if (mountedRef.current) setSessionLoading(false);
  };

  const isActiveSessionAction = (identity: SessionActionIdentity) =>
    mountedRef.current &&
    isCurrentSessionAction(
      activeSessionRef.current,
      sessionGenerationRef.current,
      identity
    );

  const refreshStagedChanges = async (sessionId: string, quiet = false) => {
    const request = {
      sessionId,
      sequence: ++stagedRequestSequenceRef.current,
    };
    if (!sessionId) {
      setStagedChanges([]);
      return;
    }
    try {
      const response = await api.stagedChanges(sessionId);
      if (
        !mountedRef.current ||
        !shouldCommitStagedList(
          activeSessionRef.current,
          stagedRequestSequenceRef.current,
          request
        )
      ) return;
      setStagedChanges(response.changes);
    } catch (error) {
      if (
        !quiet &&
        mountedRef.current &&
        shouldCommitStagedList(
          activeSessionRef.current,
          stagedRequestSequenceRef.current,
          request
        )
      ) {
        toast.error("Could not load staged changes", {
          description: error instanceof Error ? error.message : String(error),
        });
      }
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      runGenerationRef.current += 1;
      sessionGenerationRef.current += 1;
      sessionLoadGenerationRef.current += 1;
      stagedRequestSequenceRef.current += 1;
      sandboxRequestSequenceRef.current += 1;
      registrationRequestSequenceRef.current += 1;
      contextGenerationRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      approvalRef.current = initialApprovalState;
      approvalLockRef.current = "";
      changeBusyRef.current = "";
      registeringProjectRef.current = false;
      sessionLoadingRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!workspace && config.data?.workspace) setWorkspace(config.data.workspace);
  }, [config.data?.workspace, workspace]);

  useEffect(() => {
    if (projects.loading || projects.data === null) return;
    const dataBelongsToWorkspace = projects.data.every(
      (project) => project.workspace === activeWorkspace
    );
    if (!dataBelongsToWorkspace) return;
    const nextSelection = projectSelectionAfterLoad(availableProjects, projectSelection);
    if (nextSelection !== projectSelection) {
      projectSelectionRef.current = nextSelection;
      selectedProjectIdRef.current = selectedProjectFor(availableProjects, nextSelection)?.id ?? "";
      setProjectSelection(nextSelection);
    }
  }, [activeWorkspace, availableProjects, projectSelection, projects.data, projects.loading]);

  useEffect(() => {
    const projectId = selectedProject?.id ?? "";
    const root = selectedProject?.root ?? "";
    const sequence = ++sandboxRequestSequenceRef.current;
    if (mode !== "build" || !projectId) {
      setSandboxCheck({ projectId: "", root: "", loading: false, status: null, error: null });
      return;
    }

    const request = { projectId, sequence };
    setSandboxCheck({ projectId, root, loading: true, status: null, error: null });
    const timer = window.setTimeout(() => {
      api.agentSandbox(projectId)
        .then((status) => {
          if (
            !mountedRef.current ||
            modeRef.current !== "build" ||
            !shouldCommitSandboxStatus(
              selectedProjectIdRef.current,
              sandboxRequestSequenceRef.current,
              request
            )
          ) return;
          setSandboxCheck({ projectId, root, loading: false, status, error: null });
        })
        .catch((error) => {
          if (
            !mountedRef.current ||
            modeRef.current !== "build" ||
            !shouldCommitSandboxStatus(
              selectedProjectIdRef.current,
              sandboxRequestSequenceRef.current,
              request
            )
          ) return;
          setSandboxCheck({
            projectId,
            root,
            loading: false,
            status: null,
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }, 250);

    return () => window.clearTimeout(timer);
  }, [mode, selectedProject?.id, selectedProject?.root]);

  useEffect(() => {
    if (model) return;
    const configured = config.data?.default_model;
    if (configured && models.includes(configured)) setModel(configured);
    else if (models.length) setModel(models[0]);
  }, [config.data?.default_model, model, models]);

  useEffect(() => {
    if (!model) return;
    let cancelled = false;
    setWarming(true);
    api.warm(model, true)
      .then((res) => {
        if (cancelled) return;
        if (res?.state === "failed") {
          toast.error("Model warm-up failed", { description: res.error ?? "Ollama did not load the model." });
        }
      })
      .finally(() => {
        if (!cancelled) setWarming(false);
      });
    return () => {
      cancelled = true;
    };
  }, [model]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const visibleSessions = (sessions.data ?? []).filter((session) => {
    if (session.workspace !== activeWorkspace) return false;
    if (projectFilter === "unassigned") return session.project_id == null;
    return Boolean(projectFilter && session.project_id === projectFilter);
  });
  const selectedSession = visibleSessions.find((s) => s.id === activeSessionId);

  const loadSession = async (id: string) => {
    const expectedWorkspace = activeWorkspace;
    const expectedProjectFilter = projectFilter;
    const contextRequest = {
      key: workbenchContextKey(activeWorkspace, projectSelection),
      generation: contextGenerationRef.current,
    };
    const loadGeneration = ++sessionLoadGenerationRef.current;
    sessionLoadingRef.current = true;
    setSessionLoading(true);
    const generation = beginSessionContext(id);
    const identity = { sessionId: id, generation };
    const isCurrentLoad = () =>
      mountedRef.current &&
      isCurrentGeneration(sessionLoadGenerationRef.current, loadGeneration) &&
      isCurrentWorkbenchContext(
        activeWorkspaceRef.current,
        projectSelectionRef.current,
        contextGenerationRef.current,
        contextRequest
      ) &&
      isActiveSessionAction(identity);
    setMessages([]);
    setSystem("");
    setEvents([]);
    setLastStats(null);
    try {
      const detail = await api.session(id);
      if (!isCurrentLoad()) return;
      const detailMatchesContext =
        detail.workspace === expectedWorkspace &&
        (expectedProjectFilter === "unassigned"
          ? detail.project_id == null
          : Boolean(expectedProjectFilter && detail.project_id === expectedProjectFilter));
      if (!detailMatchesContext) {
        throw new Error("Thread no longer belongs to the selected project context");
      }
      const split = splitSystem(detail);
      setMessages(split.messages);
      setSystem(split.system);
      setEvents((detail.events ?? []).map(eventFromStored));
      setLastStats(null);
      if (detail.model) setModel(detail.model);
      await refreshStagedChanges(detail.id);
    } catch (e) {
      if (isCurrentLoad()) {
        selectSession("");
        setMessages([]);
        setSystem("");
        setEvents([]);
        setLastStats(null);
        toast.error("Could not load session", { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      if (
        mountedRef.current &&
        isCurrentGeneration(sessionLoadGenerationRef.current, loadGeneration)
      ) {
        sessionLoadingRef.current = false;
        setSessionLoading(false);
      }
    }
  };

  const newSession = () => {
    cancelSessionLoad();
    beginSessionContext("");
    setMessages([]);
    setEvents([]);
    setSystem("");
    setInput("");
    setLastStats(null);
  };

  const resetWorkbenchContext = ({ preserveInput = false }: { preserveInput?: boolean } = {}) => {
    contextGenerationRef.current += 1;
    registrationRequestSequenceRef.current += 1;
    registeringProjectRef.current = false;
    setRegisteringProject(false);
    cancelSessionLoad();
    cancelActiveRun();
    clearStagedContext();
    selectSession("");
    sandboxRequestSequenceRef.current += 1;
    setSandboxCheck({ projectId: "", root: "", loading: false, status: null, error: null });
    setMessages([]);
    setEvents([]);
    setSystem("");
    if (!preserveInput) setInput("");
    setLastStats(null);
  };

  const switchWorkspace = (next: string) => {
    if (!next || next === activeWorkspaceRef.current) return;
    if (editor.hasDirty && !window.confirm("Discard unsaved editor changes?")) return;
    resetWorkbenchContext();
    activeWorkspaceRef.current = next;
    projectSelectionRef.current = "";
    selectedProjectIdRef.current = "";
    workspaceContextRef.current = next;
    setProjectSelection("");
    setWorkspace(next);
  };

  const switchProject = (next: string) => {
    if (!next || next === projectSelectionRef.current) return;
    if (editor.hasDirty && !window.confirm("Discard unsaved editor changes?")) return;
    resetWorkbenchContext();
    projectSelectionRef.current = next;
    selectedProjectIdRef.current = selectedProjectFor(availableProjects, next)?.id ?? "";
    setProjectSelection(next);
  };

  const registerProject = async (input: ProjectRegistrationInput): Promise<boolean> => {
    if (registeringProjectRef.current) return false;
    const request = {
      workspaceId: activeWorkspace,
      context: {
        key: workbenchContextKey(activeWorkspace, projectSelection),
        generation: contextGenerationRef.current,
      },
      sequence: ++registrationRequestSequenceRef.current,
    };
    registeringProjectRef.current = true;
    setRegisteringProject(true);
    try {
      const created = await api.registerProject(request.workspaceId, input);
      if (shouldRefreshProjectsAfterRegistration(
        activeWorkspaceRef.current,
        request.workspaceId
      )) {
        projects.reload();
      }
      const stillCurrent = mountedRef.current && isCurrentProjectRegistration(
        activeWorkspaceRef.current,
        projectSelectionRef.current,
        contextGenerationRef.current,
        registrationRequestSequenceRef.current,
        request
      );
      if (!stillCurrent) return false;
      switchProject(created.id);
      toast.success("Project registered", { description: created.name });
      return true;
    } catch (error) {
      if (
        mountedRef.current &&
        isCurrentProjectRegistration(
          activeWorkspaceRef.current,
          projectSelectionRef.current,
          contextGenerationRef.current,
          registrationRequestSequenceRef.current,
          request
        )
      ) {
        toast.error("Could not register project", {
          description: error instanceof Error ? error.message : String(error),
        });
      }
      return false;
    } finally {
      if (registrationRequestSequenceRef.current === request.sequence) {
        registeringProjectRef.current = false;
        if (mountedRef.current) setRegisteringProject(false);
      }
    }
  };

  useEffect(() => {
    if (workspaceContextRef.current === activeWorkspace) return;
    workspaceContextRef.current = activeWorkspace;
    resetWorkbenchContext({
      preserveInput: Boolean(input) && messages.length === 0 && !activeSessionId,
    });
    projectSelectionRef.current = "";
    selectedProjectIdRef.current = "";
    setProjectSelection("");
  }, [activeWorkspace]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (sessionLoadingRef.current || runInFlightRef.current) return;
    if (!model) {
      toast.error("Select a model first");
      return;
    }
    const runProjectId = selectedProjectIdRef.current;
    if (agentModeNeedsProject(mode, runProjectId)) {
      toast.error("Select a registered project before sending");
      return;
    }
    if (!trimmed || streaming || warming) return;

    const runMode = mode;
    const generation = ++runGenerationRef.current;
    const prior = durableTranscript(system, messages);
    const assistantIndex = messages.length + 1;
    const initialAssistant = runMode === "ask" ? "" : `${modeLabel(runMode)} agent starting...`;
    setMessages([
      ...messages,
      { role: "user", content: trimmed },
      { role: "assistant", content: initialAssistant, ephemeral: true },
    ]);
    setInput("");
    runInFlightRef.current = true;
    setStreaming(true);
    setLastStats(null);
    resetApproval();
    if (runMode !== "ask") {
      appendRunEvent(
        { type: "run", name: runMode, message: trimmed, timestamp: Date.now() / 1000 },
        generation
      );
    }

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      await streamAgentChat(
        trimmed,
        prior,
        assistantIndex,
        runMode,
        runProjectId,
        generation,
        ac.signal
      );
    } catch (e) {
      if (isActiveRun(generation) && (e as Error).name !== "AbortError") {
        toast.error("Studio error", { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      runInFlightRef.current = false;
      if (mountedRef.current) {
        setStreaming(false);
        sessions.reload();
      }
      if (abortRef.current === ac) abortRef.current = null;
    }
  };

  const streamAgentChat = async (
    text: string,
    prior: Msg[],
    assistantIndex: number,
    agent: Mode,
    projectId: string,
    generation: number,
    signal: AbortSignal
  ) => {
    let acc = "";
    let streamRunId = "";
    let streamSessionId = activeSessionRef.current;
    const startedAt = performance.now();
    let ttftMs: number | undefined;

    try {
      for await (const ev of api.agentChat(
        buildAgentChatPayload({
          agent,
          model,
          message: text,
          messages: prior,
          sessionId: activeSessionRef.current || undefined,
          projectId,
          name: selectedSession?.name || text.slice(0, 64),
        }),
        signal
      )) {
        if (!isActiveRun(generation)) return;
        const type = String(ev.type ?? "");
        if (type === "session" && typeof ev.session_id === "string") {
          streamSessionId = ev.session_id;
          selectSession(ev.session_id);
        } else if (
          type === "run" &&
          typeof ev.run_id === "string" &&
          typeof ev.approval_token === "string"
        ) {
          streamRunId = ev.run_id;
          applyApprovalAction({
            type: "run",
            runId: ev.run_id,
            approvalToken: ev.approval_token,
          });
        } else if (
          type === "ask" &&
          typeof ev.run_id === "string" &&
          typeof ev.ask_id === "string" &&
          typeof ev.tool === "string" &&
          typeof ev.key === "string" &&
          ev.run_id === streamRunId
        ) {
          applyApprovalAction({
            type: "ask",
            runId: ev.run_id,
            askId: ev.ask_id,
            tool: ev.tool,
            target: ev.target,
            key: ev.key,
            rememberable: approvalMayBeRemembered(ev.tool, ev.rememberable === true),
          });
          appendRunEvent(eventFromRaw(ev), generation);
        } else if (
          (type === "ask_resolved" || type === "ask_timeout") &&
          typeof ev.run_id === "string" &&
          typeof ev.ask_id === "string"
        ) {
          applyApprovalAction({
            type: "resolved",
            runId: ev.run_id,
            askId: ev.ask_id,
          });
          approvalLockRef.current = releaseApprovalLock(
            approvalLockRef.current,
            ev.run_id,
            ev.ask_id
          );
          appendRunEvent(eventFromRaw(ev), generation);
        } else if (
          type === "staged_change" &&
          typeof ev.run_id === "string" &&
          typeof ev.session_id === "string" &&
          ev.run_id === streamRunId &&
          ev.session_id === streamSessionId
        ) {
          appendRunEvent(eventFromRaw(ev), generation);
          void refreshStagedChanges(streamSessionId, true);
          if (typeof ev.path === "string") editor.markDiffStaleForPath(ev.path);
        } else if (type === "status") {
          if (agent !== "ask") {
            const event = eventFromRaw(ev);
            appendRunEvent(event, generation);
            if (!acc) {
              replaceAssistant(
                assistantIndex,
                `${event.message || "Agent started"}...`,
                generation,
                true
              );
            }
          }
        } else if (type === "thinking" && agent === "ask" && !acc) {
          ttftMs ??= performance.now() - startedAt;
          replaceAssistant(assistantIndex, "Thinking...", generation, true);
        } else if (type === "delta") {
          const delta = String(ev.content ?? "");
          if (agent === "ask" && delta) ttftMs ??= performance.now() - startedAt;
          acc += delta;
          replaceAssistant(assistantIndex, acc, generation);
        } else if (type === "tool_call" || type === "tool_result" || type === "tool_calls") {
          appendRunEvent(eventFromRaw(ev), generation);
        } else if (type === "done") {
          acc = String(ev.content ?? acc);
          replaceAssistant(
            assistantIndex,
            acc || (agent === "ask" && ttftMs !== undefined
              ? "No response text returned."
              : acc),
            generation,
            !acc
          );
          if (agent === "ask") {
            commitRunStats(chatStatsFromEvent(ev, ttftMs), generation);
          }
        } else if (type === "error") {
          const event = eventFromRaw(ev);
          appendRunEvent(event, generation);
          throw new Error(event.message || "Agent run failed");
        }
      }
    } finally {
      if (isActiveRun(generation)) {
        if (streamRunId) {
          applyApprovalAction({ type: "closed", runId: streamRunId });
        }
        approvalLockRef.current = "";
        if (streamSessionId) void refreshStagedChanges(streamSessionId, true);
      }
    }
  };

  const replaceAssistant = (
    index: number,
    content: string,
    generation: number,
    ephemeral = false
  ) => {
    if (!isActiveRun(generation)) return;
    setMessages((prev) => {
      if (!isActiveRun(generation)) return prev;
      const next = [...prev];
      next[index] = { role: "assistant", content, ...(ephemeral ? { ephemeral: true } : {}) };
      return next;
    });
  };

  const answerPendingApproval = async (intent: ApprovalDecisionIntent) => {
    const pending = approvalRef.current.pending;
    if (
      !pending ||
      pending.runId !== intent.runId ||
      pending.askId !== intent.askId
    ) return;
    const generation = runGenerationRef.current;
    const lockKey = approvalLockKey(intent.runId, intent.askId);
    if (approvalLockRef.current) return;
    const request = createApprovalAnswer(
      approvalRef.current,
      intent.runId,
      intent.askId,
      intent.decision,
      intent.remember
    );
    if (!request) return;

    approvalLockRef.current = lockKey;
    applyApprovalAction({
      type: "submit_started",
      runId: intent.runId,
      askId: intent.askId,
    });
    try {
      await api.answerApproval(request.runId, request.approvalToken, request.body);
      if (
        !isActiveRun(generation) ||
        !isApprovalResponseRelevant(approvalRef.current, intent.runId, intent.askId)
      ) return;
      applyApprovalAction({
        type: "submit_succeeded",
        runId: intent.runId,
        askId: intent.askId,
      });
      toast.success(
        intent.decision === "deny"
          ? "Tool denied"
          : request.body.remember
            ? "Tool allowed and remembered"
            : "Tool allowed once"
      );
    } catch (error) {
      if (
        !isActiveRun(generation) ||
        !isApprovalResponseRelevant(approvalRef.current, intent.runId, intent.askId)
      ) return;
      const confirmedFailure = error instanceof ApiError;
      const message = confirmedFailure
        ? approvalFailureMessage(error.status)
        : "The decision could not be confirmed. No approval is inferred; wait for run status before retrying.";
      applyApprovalAction({
        type: "submit_failed",
        runId: intent.runId,
        askId: intent.askId,
        message,
      });
      if (confirmedFailure) {
        if (error.status === 404) {
          applyApprovalAction({ type: "closed", runId: intent.runId });
        } else {
          applyApprovalAction({
            type: "resolved",
            runId: intent.runId,
            askId: intent.askId,
          });
        }
      }
      toast.error("Approval was not accepted", { description: message });
    } finally {
      approvalLockRef.current = releaseApprovalLock(
        approvalLockRef.current,
        intent.runId,
        intent.askId
      );
    }
  };

  const reviewStagedChange = (change: StagedChangeSummary) => {
    const identity = { sessionId: change.session_id, generation: sessionGenerationRef.current };
    if (!isActiveSessionAction(identity)) return;
    editor.openDiff(change);
    setMobilePane("editor");
  };

  const applyChange = async (change: StagedChangeSummary) => {
    const identity = {
      sessionId: change.session_id,
      generation: sessionGenerationRef.current,
    };
    if (!isActiveSessionAction(identity)) return;
    const fullPath = stagedFullPath(change.root, change.path);
    if (!window.confirm(
      `Apply staged change to disk?\n\nPath: ${fullPath}\nRun: ${change.run_id}\n\nThis is separate from agent approval.`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:apply:${change.id}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      await api.applyStagedChange(change.id);
      if (!isActiveSessionAction(identity)) return;
      toast.success("Staged change applied", {
        description: `${fullPath}\nRun: ${change.run_id}`,
      });
      await refreshStagedChanges(change.session_id);
      editor.syncDiskForPath(change.path);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("apply", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not apply staged change", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(change.session_id, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };

  const rejectChange = async (change: StagedChangeSummary) => {
    const identity = {
      sessionId: change.session_id,
      generation: sessionGenerationRef.current,
    };
    if (!isActiveSessionAction(identity)) return;
    const fullPath = stagedFullPath(change.root, change.path);
    if (!window.confirm(
      `Reject this staged change?\n\nPath: ${fullPath}\nRun: ${change.run_id}`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:reject:${change.id}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      await api.rejectStagedChange(change.id);
      if (!isActiveSessionAction(identity)) return;
      toast.success("Staged change rejected", {
        description: `${fullPath}\nRun: ${change.run_id}`,
      });
      await refreshStagedChanges(change.session_id);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("reject", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not reject staged change", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(change.session_id, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };

  const revertChange = async (change: StagedChangeSummary) => {
    const identity = {
      sessionId: change.session_id,
      generation: sessionGenerationRef.current,
    };
    if (!isActiveSessionAction(identity)) return;
    const fullPath = stagedFullPath(change.root, change.path);
    if (!window.confirm(
      `Revert this applied change?\n\nPath: ${fullPath}\nRun: ${change.run_id}\n\nDisk is restored to the snapshot taken at staging.`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:revert:${change.id}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      await api.revertStagedChange(change.id);
      if (!isActiveSessionAction(identity)) return;
      toast.success("Applied change reverted", {
        description: `${fullPath}\nRun: ${change.run_id}`,
      });
      await refreshStagedChanges(change.session_id);
      editor.syncDiskForPath(change.path);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("revert", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not revert staged change", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(change.session_id, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };

  const applyAllChanges = async () => {
    const sid = activeSessionRef.current;
    if (!sid) return;
    const identity = { sessionId: sid, generation: sessionGenerationRef.current };
    if (!isActiveSessionAction(identity)) return;
    const pendingCount = stagedChanges.filter((c) => c.status === "pending").length;
    if (!window.confirm(
      `Apply all ${pendingCount} pending staged changes to disk?\n\nConflicting changes are skipped and marked.`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:apply-all:${sid}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      const result = await api.applyAllStagedChanges(sid);
      if (!isActiveSessionAction(identity)) return;
      const parts = [`${result.applied.length} applied`];
      if (result.conflicts.length) parts.push(`${result.conflicts.length} conflicts`);
      if (result.errors.length) parts.push(`${result.errors.length} errors`);
      (result.conflicts.length || result.errors.length ? toast.warning : toast.success)(
        "Apply all finished", { description: parts.join(" · ") }
      );
      await refreshStagedChanges(sid);
      for (const c of stagedChanges) {
        if (c.status === "pending") editor.syncDiskForPath(c.path);
      }
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("apply-all", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not apply staged changes", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(sid, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };

  const changeById = (changeId: string) => stagedChanges.find((c) => c.id === changeId);
  const onDiffApply = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await applyChange(change);
    editor.refreshDiff(changeId);
  };
  const onDiffReject = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await rejectChange(change);
    editor.refreshDiff(changeId);
  };
  const onDiffRevert = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await revertChange(change);
    editor.refreshDiff(changeId);
  };

  const stop = () => {
    const sessionId = activeSessionRef.current;
    cancelActiveRun();
    if (sessionId) void refreshStagedChanges(sessionId, true);
  };
  const clear = () => {
    cancelSessionLoad();
    stop();
    clearStagedContext();
    selectSession("");
    setMessages([]);
    setEvents([]);
    setLastStats(null);
  };
  const projectMissing = agentModeNeedsProject(mode, selectedProjectId);
  const workbenchControlsAreDisabled = workbenchControlsDisabled(streaming, sessionLoading);
  const sendDisabled = workbenchSendDisabled({
    model,
    mode,
    projectId: selectedProjectId,
    input,
    warming,
    streaming,
    sessionLoading,
  });

  useEffect(() => {
    if (!selectedProjectId && navigatorView === "files") setNavigatorView("threads");
  }, [navigatorView, selectedProjectId]);

  return (
    <>
      <PageHeader title="Studio" className="mb-3">
        <Button variant="ghost" size="sm" onClick={newSession}>
          <MessageSquare /> New
        </Button>
        <Button variant="ghost" size="sm" onClick={clear} disabled={!messages.length && !events.length}>
          <Trash2 /> Clear
        </Button>
      </PageHeader>

      <div
        role="tablist"
        aria-label="Studio panes"
        className="mb-3 flex gap-1 min-[1440px]:hidden"
        onKeyDown={handleMobilePaneKeyDown}
      >
        {STUDIO_PANES.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`studio-${id}-tab`}
            aria-controls={`studio-${id}-panel`}
            aria-selected={mobilePane === id}
            tabIndex={mobilePane === id ? 0 : -1}
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

      <div className="grid min-h-[520px] grid-cols-1 gap-3 min-[1440px]:h-[calc(100vh-150px)] min-[1440px]:grid-cols-[250px_minmax(480px,1fr)_minmax(400px,620px)]">
        <aside
          id="studio-files-panel"
          role="tabpanel"
          aria-labelledby="studio-files-tab"
          className={cn(
            mobilePane === "files" ? "flex" : "hidden",
            "order-1 h-[520px] min-h-[320px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[1440px]:flex min-[1440px]:h-auto min-[1440px]:min-h-0"
          )}
        >
          <ContextPicker
            workspaces={workspaces.data ?? []}
            workspacesLoading={workspaces.loading}
            workspaceId={activeWorkspace}
            projects={availableProjects}
            projectsLoading={projects.loading}
            projectsError={projects.error}
            projectSelection={projectSelection}
            selectedProject={selectedProject}
            registering={registeringProject}
            onWorkspaceChange={switchWorkspace}
            onProjectChange={switchProject}
            onRegister={registerProject}
          />

          <div role="group" aria-label="Studio navigator" className="flex border-b border-line p-1.5">
            <button
              type="button"
              id="workbench-threads-toggle"
              aria-pressed={navigatorView === "threads"}
              aria-controls="workbench-threads-panel"
              className={cn(
                "flex-1 rounded px-2 py-1.5 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant",
                navigatorView === "threads" ? "bg-panel-3 text-fg" : "text-fg-muted hover:text-fg"
              )}
              onClick={() => setNavigatorView("threads")}
            >
              Threads
            </button>
            <button
              type="button"
              id="workbench-files-toggle"
              aria-pressed={navigatorView === "files"}
              aria-controls="workbench-files-panel"
              disabled={!selectedProjectId}
              className={cn(
                "flex-1 rounded px-2 py-1.5 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant disabled:cursor-not-allowed disabled:opacity-40",
                navigatorView === "files" ? "bg-panel-3 text-fg" : "text-fg-muted hover:text-fg"
              )}
              onClick={() => setNavigatorView("files")}
            >
              Files
            </button>
          </div>

          {navigatorView === "files" ? (
            <div
              id="workbench-files-panel"
              role="region"
              aria-labelledby="workbench-files-toggle"
              className="flex min-h-0 flex-1"
            >
              <FileTree
                key={selectedProjectId}
                projectId={selectedProjectId}
                pendingPaths={pendingStagedPaths}
                onOpenFile={openFileInEditor}
              />
            </div>
          ) : (
            <div
              id="workbench-threads-panel"
              role="region"
              aria-labelledby="workbench-threads-toggle"
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="flex items-center justify-between border-b border-line px-3 py-2">
                <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                  Threads
                </span>
                <Button variant="ghost" size="sm" className="h-7 px-2" onClick={newSession}>
                  New
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {!projectFilter || sessions.loading ? (
                <div className="space-y-2">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : visibleSessions.length ? (
                <div className="space-y-1.5">
                  {visibleSessions.map((s) => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      active={s.id === activeSessionId}
                      onClick={() => loadSession(s.id)}
                    />
                  ))}
                </div>
              ) : (
                <div className="px-2 py-8 text-center text-[12.5px] text-fg-muted">
                  {projectSelection === LEGACY_PROJECT_SELECTION
                    ? "No legacy threads"
                    : selectedProject
                      ? "No project threads"
                      : "Select or register a project"}
                </div>
              )}
              </div>
            </div>
          )}

          {stagedChanges.length > 0 && (
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
                  <StagedQueue
                    changes={stagedChanges}
                    busy={changeBusy}
                    onReview={(change) => reviewStagedChange(change)}
                    onApply={(change) => void applyChange(change)}
                    onReject={(change) => void rejectChange(change)}
                    onRevert={(change) => void revertChange(change)}
                    onApplyAll={() => void applyAllChanges()}
                  />
                </div>
              )}
            </div>
          )}
        </aside>

        <aside
          id="studio-chat-panel"
          role="tabpanel"
          aria-labelledby="studio-chat-tab"
          className={cn(
            mobilePane === "chat" ? "flex" : "hidden",
            "order-2 min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[1440px]:flex min-[1440px]:min-h-0"
          )}
        >
          <div className="border-b border-line px-3 py-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <Badge className="shrink-0 whitespace-nowrap" variant="success" dot>Ollama</Badge>
              <span className="hidden shrink-0 text-[12px] uppercase tracking-[0.08em] text-fg-faint sm:inline">Model</span>
              {installed.loading ? (
                <Skeleton className="h-8 w-48" />
              ) : models.length ? (
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="h-8 min-w-[140px] flex-1 sm:max-w-[240px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <span className="text-[13px] text-fg-muted">No models installed</span>
              )}
              {warming && <Badge className="shrink-0 whitespace-nowrap" variant="info" dot>Warming</Badge>}
              {model && runningModels.has(model) && <Badge className="shrink-0 whitespace-nowrap" variant="success" dot>Resident</Badge>}
            </div>
            <div className="mt-2 flex items-center gap-1 rounded border border-line bg-panel-2 p-0.5">
              {MODES.map((item) => (
                <ModeButton
                  key={item.id}
                  mode={item}
                  active={mode === item.id}
                  disabled={workbenchControlsAreDisabled}
                  onClick={() => {
                    modeRef.current = item.id;
                    setMode(item.id);
                  }}
                />
              ))}
            </div>
            {mode === "plan" && (
              <p className="mt-2 text-[11.5px] text-fg-faint">Plan responses are model output — not executed.</p>
            )}
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Sparkles className="mb-3 h-7 w-7 text-verdant" />
                <h2 className="text-[14px] font-semibold text-fg">What do you want to work on?</h2>
                <p className="mt-1 text-[12px] text-fg-muted">Choose a draft to edit it. Nothing runs until you press the action button.</p>
                <div className="mt-4 grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setInput(s)}
                      disabled={sessionLoading}
                      className="min-h-[54px] rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[12.5px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map((m, i) => (
                  <Bubble key={`${m.role}-${i}`} role={m.role} content={m.content} model={model} />
                ))}
              </div>
            )}
          </div>

          {approval.pending && (
            <div className="border-t border-line p-3">
              <ApprovalCard
                approval={approval.pending}
                onDecision={(intent) => void answerPendingApproval(intent)}
              />
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
              <SandboxStatusPanel
                projectId={selectedProject.id}
                root={selectedProject.root}
                check={sandboxCheck}
              />
            </div>
          )}

          <div className="border-t border-line p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex items-end gap-2"
            >
              <label htmlFor="studio-prompt" className="sr-only">Studio prompt</label>
              <textarea
                id="studio-prompt"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  model
                    ? sessionLoading
                      ? "Loading session..."
                      : projectMissing
                      ? "Select a project to start"
                      : `Message ${modeLabel(mode)} with ${model}`
                    : "Install a model to start"
                }
                disabled={!model || warming || workbenchControlsAreDisabled}
                rows={2}
                className="min-h-[44px] flex-1 resize-none rounded border border-line bg-panel-2 px-3 py-2 text-[14px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong disabled:cursor-not-allowed disabled:opacity-60"
              />
              {streaming ? (
                <Button type="button" variant="secondary" onClick={stop}>
                  <Square /> Stop
                </Button>
              ) : (
                <Button type="submit" disabled={sendDisabled}>
                  <Send /> {workbenchSendLabel(mode, selectedProjectId)}
                </Button>
              )}
            </form>
          </div>

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
                <div className="grid grid-cols-2 gap-2 text-[12.5px]">
                  <StatTile label="Session" value={activeSessionId ? shortId(activeSessionId) : "New"} />
                  <StatTile label="Events" value={String(events.length)} />
                </div>
                <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                  <Settings2 className="h-3.5 w-3.5" /> System
                </div>
                <textarea
                  value={system}
                  onChange={(e) => setSystem(e.target.value)}
                  placeholder="Optional system prompt"
                  rows={4}
                  className="w-full resize-none rounded border border-line bg-panel-2 px-3 py-2 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong"
                />
                <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                  <Bot className="h-3.5 w-3.5" /> Events
                </div>
                {events.length ? (
                  <div className="space-y-2">
                    {events.slice().reverse().map((event, i) => (
                      <RunEvent key={`${event.type}-${i}`} event={event} />
                    ))}
                  </div>
                ) : (
                  <div className="rounded border border-dashed border-line px-3 py-8 text-center text-[12.5px] text-fg-muted">
                    No agent events
                  </div>
                )}
                <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                  Stats
                </div>
                {lastStats ? (
                  <div className="grid grid-cols-2 gap-2">
                    <StatTile label="TTFT" value={formatMs(lastStats.ttft_ms) ?? "-"} />
                    <StatTile label="Speed" value={lastStats.tokens_per_second ? `${Math.round(lastStats.tokens_per_second)} tok/s` : "-"} />
                  </div>
                ) : (
                  <div className="text-[12.5px] text-fg-muted">No stats for this run</div>
                )}
              </div>
            )}
          </div>
        </aside>

        <section
          id="studio-editor-panel"
          role="tabpanel"
          aria-labelledby="studio-editor-tab"
          className={cn(
            mobilePane === "editor" ? "flex" : "hidden",
            "order-3 min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel min-[1440px]:flex min-[1440px]:min-h-0"
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
                  <div className="text-[13px] font-semibold text-fg">Output inspector</div>
                  <p className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-fg-muted">
                    Open a project file or review a staged change here. Studio will never render arbitrary model HTML as a live preview.
                  </p>
                </div>
              }
              onActivate={editor.activate}
              onClose={editor.close}
              onChangeDoc={editor.updateDoc}
              onSave={editor.save}
              onSaveAgain={editor.saveAgain}
              onKeepEditing={editor.keepEditing}
              diffTabs={editor.diffTabs}
              onDiffApply={(id) => void onDiffApply(id)}
              onDiffReject={(id) => void onDiffReject(id)}
              onDiffRevert={(id) => void onDiffRevert(id)}
              onRefreshDiff={(id) => editor.refreshDiff(id)}
            />
          </Suspense>
        </section>
      </div>
    </>
  );
}

function SandboxStatusPanel({
  projectId,
  root,
  check,
}: {
  projectId: string;
  root: string;
  check: SandboxCheckState;
}) {
  const exactProject = check.projectId === projectId && check.root === root;
  const status = exactProject ? check.status : null;
  const loading = exactProject && check.loading;
  const error = exactProject ? check.error : null;
  const presentation = status ? sandboxPresentation(status) : null;

  return (
    <div className="mt-3 rounded border border-line bg-panel-2 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          Task sandbox
        </span>
        <Badge variant={presentation?.tone ?? "neutral"}>
          {loading ? "Checking" : error ? "Status unavailable" : presentation?.label ?? "Checking"}
        </Badge>
      </div>
      <div className="mt-1.5 break-all text-[10.5px] text-fg-faint">Root: {root}</div>
      {loading && (
        <div className="mt-1.5 text-[11px] leading-relaxed text-fg-muted">
          Checking the configured Docker sandbox for this exact root.
        </div>
      )}
      {error && (
        <>
          <div className="mt-1.5 text-[11px] leading-relaxed text-warning">{error}</div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-fg-faint">
            Staged editing remains available. Verification tasks are unavailable.
          </div>
        </>
      )}
      {status && presentation && (
        <>
          <div className="mt-1.5 text-[11px] leading-relaxed text-fg-muted">
            {presentation.detail}
          </div>
          {status.available ? (
            <>
              <div
                className="mt-2 flex max-h-24 flex-wrap gap-1 overflow-y-auto pr-1"
                role="list"
                aria-label="Configured sandbox tasks"
                tabIndex={0}
              >
                {status.tasks.map((task) => (
                  <span key={task} role="listitem">
                    <Badge variant="outline">{task}</Badge>
                  </span>
                ))}
              </div>
              {status.image && (
                <div className="mt-1.5 break-all text-[10.5px] text-fg-faint">Image: {status.image}</div>
              )}
              <div className="mt-1 text-[10.5px] text-fg-faint">Network disabled</div>
            </>
          ) : (
            <div className="mt-1 text-[10.5px] leading-relaxed text-fg-faint">
              Staged editing remains available. Verification tasks are unavailable.
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ApprovalCard({
  approval,
  onDecision,
}: {
  approval: PendingApproval;
  onDecision: (intent: ApprovalDecisionIntent) => void;
}) {
  const disabled = approval.submitting || approval.submitted;
  const runTask = approval.tool === "run_task";
  const runTaskDetails = runTask ? parseRunTaskTarget(approval.target) : null;
  const allowDisabled = disabled || (runTask && runTaskDetails === null);
  const invalidDetailsId = "run-task-approval-invalid-details";
  return (
    <div className="rounded-lg border border-warning/40 bg-warning-soft/40 p-3" role="alert">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold uppercase tracking-[0.08em] text-warning">
          Approval required
        </div>
        <Badge variant="warning">{approval.key}</Badge>
      </div>
      <div className="mt-2 text-[13px] font-medium text-fg">
        {runTask ? "Sandbox verification task" : approval.tool}
      </div>
      {runTask ? (
        runTaskDetails ? (
          <RunTaskApprovalDetails details={runTaskDetails} />
        ) : (
          <div
            id={invalidDetailsId}
            className="mt-2 rounded border border-danger/40 bg-danger-soft p-2 text-[11px] leading-relaxed text-danger"
          >
            Task details are incomplete. Approval is disabled.
          </div>
        )
      ) : (
        <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-panel px-2 py-1.5 text-[11.5px] leading-relaxed text-fg-muted">
          {formatApprovalTarget(approval.target)}
        </pre>
      )}
      {approval.error && (
        <div className="mt-2 text-[11.5px] leading-relaxed text-danger">{approval.error}</div>
      )}
      {approval.submitted && (
        <div className="mt-2 text-[11.5px] text-fg-muted">Decision submitted; awaiting run acknowledgement.</div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="danger"
          disabled={disabled}
          onClick={() => onDecision(approvalDecisionIntent(approval, "deny", false))}
        >
          Deny
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={allowDisabled}
          aria-describedby={runTask && runTaskDetails === null ? invalidDetailsId : undefined}
          onClick={() => onDecision(approvalDecisionIntent(approval, "allow", false))}
        >
          Allow once
        </Button>
        {!runTask && approval.rememberable && (
          <Button
            size="sm"
            disabled={disabled}
            onClick={() => onDecision(approvalDecisionIntent(approval, "allow", true))}
          >
            Always allow
          </Button>
        )}
      </div>
      {(runTask || !approval.rememberable) && (
        <div className="mt-2 text-[11px] text-fg-faint">This request cannot be remembered.</div>
      )}
    </div>
  );
}

function RunTaskApprovalDetails({
  details,
}: {
  details: NonNullable<ReturnType<typeof parseRunTaskTarget>>;
}) {
  const rows = [
    ["Task", details.name],
    ["Argv", JSON.stringify(details.argv)],
    ["Project root", details.root],
    ["Image", details.image],
    ["Resolved image", details.imageId],
    ["Timeout", `${details.timeoutSeconds} seconds`],
    ["Staged overlay", details.stagedOverlayDigest],
    ["Config digest", details.configDigest],
  ];
  return (
    <div className="mt-2 rounded border border-line bg-panel p-2">
      <div
        className="max-h-48 overflow-y-auto pr-1"
        tabIndex={0}
        aria-label="Sandbox task details"
      >
        <dl className="space-y-1.5">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt className="text-[10px] font-semibold uppercase tracking-[0.07em] text-fg-faint">{label}</dt>
              <dd className="break-all text-[11px] leading-relaxed text-fg-muted">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-3 border-t border-line pt-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.07em] text-fg-faint">
            Staged changes ({details.stagedChanges.length})
          </div>
          {details.stagedChanges.length === 0 ? (
            <div className="mt-1 text-[11px] text-fg-muted">No staged rows in this snapshot.</div>
          ) : (
            <div className="mt-1.5 space-y-2">
              {details.stagedChanges.map((change, index) => (
                <div key={`${change.id}:${index}`} className="rounded border border-line bg-panel-2 p-2">
                  <div className="break-all text-[11px] font-medium text-fg">Path: {change.path}</div>
                  <dl className="mt-1 space-y-1 text-[10.5px] text-fg-muted">
                    <div>
                      <dt className="inline font-semibold text-fg-faint">ID: </dt>
                      <dd className="inline break-all">{change.id}</dd>
                    </div>
                    <div>
                      <dt className="inline font-semibold text-fg-faint">Revision: </dt>
                      <dd className="inline break-all">{String(change.updatedAt)}</dd>
                    </div>
                    <div>
                      <dt className="inline font-semibold text-fg-faint">Base hash: </dt>
                      <dd className="inline break-all">{change.baseHash ?? "none (new file)"}</dd>
                    </div>
                    <div>
                      <dt className="inline font-semibold text-fg-faint">Content hash: </dt>
                      <dd className="inline break-all">{change.contentHash}</dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-2 rounded border border-line bg-panel-2 px-2 py-1.5 text-[10.5px] leading-relaxed text-fg-muted">
        <div>Network disabled</div>
        <div>Disposable snapshot; real project unchanged</div>
      </div>
    </div>
  );
}

function ModeButton({
  mode,
  active,
  disabled,
  onClick,
}: {
  mode: { id: Mode; label: string; icon: typeof MessageSquare };
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const Icon = mode.icon;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex h-8 items-center gap-1.5 rounded px-2 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        active ? "bg-panel text-fg shadow-sm" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{mode.label}</span>
    </button>
  );
}

function SessionRow({
  session,
  active,
  onClick,
}: {
  session: SessionSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded border px-2.5 py-2 text-left transition-colors",
        active ? "border-verdant bg-verdant-soft/40" : "border-line bg-panel-2 hover:border-line-strong"
      )}
    >
      <div className="truncate text-[13px] font-medium text-fg">{session.name || "Untitled"}</div>
      <div className="mt-1 flex items-center justify-between gap-2 text-[11.5px] text-fg-faint">
        <span className="truncate">{session.model || "No model"}</span>
        <span className="shrink-0">{formatDate(session.updated_at)}</span>
      </div>
    </button>
  );
}

function Bubble({ role, content, model }: { role: string; content: string; model: string }) {
  const user = role === "user";
  const Icon = user ? User : Bot;
  return (
    <div className={cn("flex gap-3", user && "flex-row-reverse")}>
      <div
        className={cn(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          user ? "bg-verdant text-verdant-fg" : "bg-panel-3 text-fg-muted"
        )}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className={cn("min-w-0 max-w-[84%]", user && "text-right")}>
        <div className={cn("mb-1 text-[11px] text-fg-faint", user && "hidden")}>{model}</div>
        <div
          className={cn(
            "rounded-lg px-3.5 py-2.5 text-[14px]",
            user ? "bg-verdant text-verdant-fg" : "bg-panel-2 text-fg"
          )}
        >
          {user ? content : <Markdown text={content || "..."} />}
        </div>
      </div>
    </div>
  );
}

function RunEvent({ event }: { event: WorkbenchEvent }) {
  const ok = event.ok;
  const title =
    event.type === "ask"
      ? `Approval requested: ${event.tool || "tool"}`
      : event.type === "ask_resolved"
        ? `Approval ${event.decision || "resolved"}: ${event.tool || "tool"}`
        : event.type === "ask_timeout"
          ? `Approval timed out: ${event.tool || "tool"}`
          : event.type === "staged_change"
            ? `Staged: ${event.path || "change"}`
            : event.name || event.type;
  const body =
    event.result ||
    event.message ||
    (event.target !== undefined ? formatApprovalTarget(event.target) : "") ||
    (event.args ? JSON.stringify(event.args) : "");
  return (
    <div className="rounded border border-line bg-panel-2 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-[12.5px] font-medium text-fg">{title}</div>
        {typeof ok === "boolean" && (
          <Badge variant={ok ? "success" : "danger"}>{ok ? "OK" : "Fail"}</Badge>
        )}
      </div>
      {body && (
        <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-fg-muted">
          {body}
        </pre>
      )}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel-2 px-2.5 py-2">
      <div className="text-[11px] uppercase tracking-[0.08em] text-fg-faint">{label}</div>
      <div className="mt-1 truncate text-[13px] font-medium text-fg">{value}</div>
    </div>
  );
}

function splitSystem(detail: SessionDetail): { system: string; messages: Msg[] } {
  const messages = detail.messages ?? [];
  const systemMessage = messages.find((m) => m.role === "system");
  return {
    system: systemMessage?.content || detail.system_prompt || "",
    messages: messages.filter((m) => m.role !== "system"),
  };
}

function eventFromStored(event: SessionEvent): WorkbenchEvent {
  return eventFromRaw({ type: event.type, ...event.payload, timestamp: event.timestamp });
}

function eventFromRaw(raw: Record<string, unknown>): WorkbenchEvent {
  return {
    type: String(raw.type ?? "event"),
    name: typeof raw.name === "string" ? raw.name : undefined,
    ok: typeof raw.ok === "boolean" ? raw.ok : undefined,
    args: raw.args,
    result: typeof raw.result === "string" ? raw.result : undefined,
    message: typeof raw.message === "string" ? raw.message : undefined,
    timestamp: typeof raw.timestamp === "number" ? raw.timestamp : undefined,
    tool: typeof raw.tool === "string" ? raw.tool : undefined,
    target: raw.target,
    path: typeof raw.path === "string" ? raw.path : undefined,
    decision: typeof raw.decision === "string" ? raw.decision : undefined,
    remember: typeof raw.remember === "boolean" ? raw.remember : undefined,
  };
}

function formatApprovalTarget(target: unknown): string {
  if (target === null || target === undefined || target === "") return "(no target provided)";
  if (typeof target === "string") return target;
  try {
    return JSON.stringify(target, null, 2);
  } catch {
    return String(target);
  }
}

function formatMs(ms: number | undefined): string | null {
  if (!ms || !Number.isFinite(ms)) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function formatDate(value: number): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function modeLabel(mode: Mode): string {
  if (mode === "ask") return "Ask";
  if (mode === "explore") return "Explore";
  if (mode === "build") return "Build";
  return "Plan";
}
