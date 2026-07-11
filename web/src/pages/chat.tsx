import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bot,
  Code2,
  Compass,
  FileText,
  FolderOpen,
  Hammer,
  MessageSquare,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  User,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/markdown";
import { useAsync } from "@/lib/hooks";
import { ApiError, api } from "@/lib/api";
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
  STAGED_SNAPSHOT_LABEL,
  approvalLockKey,
  approvalDecisionIntent,
  buildNeedsProjectRoot,
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
} from "@/lib/agent-workbench";
import type {
  ApprovalDecisionIntent,
  SessionActionIdentity,
} from "@/lib/agent-workbench";
import type {
  PsResponse,
  AgentSandboxStatus,
  SessionDetail,
  SessionEvent,
  SessionMessage,
  SessionSummary,
  StagedChangeDetail,
  StagedChangeSummary,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type Msg = SessionMessage;
type Mode = "ask" | "plan" | "explore" | "build";

interface ChatStats {
  ttft_ms?: number;
  load_ms?: number;
  prompt_ms?: number;
  eval_ms?: number;
  eval_count?: number;
  tokens_per_second?: number;
}

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

const PROJECT_ROOT_KEY = "lac.workbench.projectRoot";
const WORKBENCH_SESSION_LIMIT = 80;

export function Chat() {
  const [params] = useSearchParams();
  const installed = useAsync(() => api.installed());
  const config = useAsync(() => api.config());
  const workspaces = useAsync(() => api.workspaces());
  const running = useAsync<PsResponse>(() => api.ps().catch(() => ({ running: false, models: [] })));

  const models = useMemo(() => (installed.data ?? []).map((m) => m.name), [installed.data]);
  const runningModels = useMemo(() => new Set((running.data?.models ?? []).map((m) => m.name)), [running.data]);

  const [workspace, setWorkspace] = useState("");
  const activeWorkspace = workspace || config.data?.workspace || "default";
  const sessions = useAsync(() => api.sessions(activeWorkspace, WORKBENCH_SESSION_LIMIT), [activeWorkspace]);

  const [model, setModel] = useState(params.get("model") ?? "");
  const [mode, setMode] = useState<Mode>("plan");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [events, setEvents] = useState<WorkbenchEvent[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [input, setInput] = useState("");
  const [system, setSystem] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [warming, setWarming] = useState(false);
  const [lastStats, setLastStats] = useState<ChatStats | null>(null);
  const [projectRoot, setProjectRoot] = useState(() => localStorage.getItem(PROJECT_ROOT_KEY) ?? "");
  const [sandboxCheck, setSandboxCheck] = useState<SandboxCheckState>({
    root: "",
    loading: false,
    status: null,
    error: null,
  });
  const [approval, setApproval] = useState(initialApprovalState);
  const [stagedChanges, setStagedChanges] = useState<StagedChangeSummary[]>([]);
  const [selectedChange, setSelectedChange] = useState<StagedChangeDetail | null>(null);
  const [changeBusy, setChangeBusy] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const approvalRef = useRef(initialApprovalState);
  const approvalLockRef = useRef("");
  const changeBusyRef = useRef("");
  const activeSessionRef = useRef("");
  const sessionLoadingRef = useRef(false);
  const mountedRef = useRef(true);
  const runGenerationRef = useRef(0);
  const sessionGenerationRef = useRef(0);
  const sessionLoadGenerationRef = useRef(0);
  const stagedRequestSequenceRef = useRef(0);
  const sandboxRequestSequenceRef = useRef(0);
  const projectRootRef = useRef(projectRoot);
  const modeRef = useRef<Mode>(mode);
  const scrollRef = useRef<HTMLDivElement>(null);

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
    setStreaming(false);
  };

  const clearStagedContext = () => {
    sessionGenerationRef.current += 1;
    stagedRequestSequenceRef.current += 1;
    changeBusyRef.current = "";
    setChangeBusy("");
    setStagedChanges([]);
    setSelectedChange(null);
  };

  const beginSessionContext = (sessionId: string) => {
    invalidateActiveRun();
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
      setSelectedChange(null);
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
      setSelectedChange((current) =>
        current && response.changes.some((change) => change.id === current.id) ? current : null
      );
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
      abortRef.current?.abort();
      abortRef.current = null;
      approvalRef.current = initialApprovalState;
      approvalLockRef.current = "";
      changeBusyRef.current = "";
      sessionLoadingRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!workspace && config.data?.workspace) setWorkspace(config.data.workspace);
  }, [config.data?.workspace, workspace]);

  useEffect(() => {
    if (projectRoot.trim()) localStorage.setItem(PROJECT_ROOT_KEY, projectRoot.trim());
    else localStorage.removeItem(PROJECT_ROOT_KEY);
  }, [projectRoot]);

  useEffect(() => {
    const root = projectRoot.trim();
    const sequence = ++sandboxRequestSequenceRef.current;
    if (mode !== "build" || !root) {
      setSandboxCheck({ root: "", loading: false, status: null, error: null });
      return;
    }

    const request = { root, sequence };
    setSandboxCheck({ root, loading: true, status: null, error: null });
    const timer = window.setTimeout(() => {
      api.agentSandbox(root)
        .then((status) => {
          if (
            !mountedRef.current ||
            modeRef.current !== "build" ||
            !shouldCommitSandboxStatus(
              projectRootRef.current.trim(),
              sandboxRequestSequenceRef.current,
              request
            )
          ) return;
          setSandboxCheck({ root, loading: false, status, error: null });
        })
        .catch((error) => {
          if (
            !mountedRef.current ||
            modeRef.current !== "build" ||
            !shouldCommitSandboxStatus(
              projectRootRef.current.trim(),
              sandboxRequestSequenceRef.current,
              request
            )
          ) return;
          setSandboxCheck({
            root,
            loading: false,
            status: null,
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }, 250);

    return () => window.clearTimeout(timer);
  }, [mode, projectRoot]);

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

  const visibleSessions = sessions.data ?? [];
  const selectedSession = visibleSessions.find((s) => s.id === activeSessionId);

  const loadSession = async (id: string) => {
    const loadGeneration = ++sessionLoadGenerationRef.current;
    sessionLoadingRef.current = true;
    setSessionLoading(true);
    const generation = beginSessionContext(id);
    const identity = { sessionId: id, generation };
    const isCurrentLoad = () =>
      mountedRef.current &&
      isCurrentGeneration(sessionLoadGenerationRef.current, loadGeneration) &&
      isActiveSessionAction(identity);
    setMessages([]);
    setSystem("");
    setEvents([]);
    setLastStats(null);
    try {
      const detail = await api.session(id);
      if (!isCurrentLoad()) return;
      const split = splitSystem(detail);
      setMessages(split.messages);
      setSystem(split.system);
      setEvents((detail.events ?? []).map(eventFromStored));
      setLastStats(null);
      if (detail.model) setModel(detail.model);
      if (detail.workspace) setWorkspace(detail.workspace);
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
    setLastStats(null);
  };

  const switchWorkspace = (next: string) => {
    cancelSessionLoad();
    beginSessionContext("");
    setWorkspace(next);
    setMessages([]);
    setEvents([]);
    api.switchWorkspace(next).catch((e) => {
      toast.error("Could not switch workspace", { description: e instanceof Error ? e.message : String(e) });
    });
  };

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (sessionLoadingRef.current) return;
    if (!model) {
      toast.error("Select a model first");
      return;
    }
    if (buildNeedsProjectRoot(mode, projectRoot)) {
      toast.error("Set a project root before using Build");
      return;
    }
    if (!trimmed || streaming || warming) return;

    const runMode = mode;
    const generation = ++runGenerationRef.current;
    const prior = transcriptWithSystem(system, messages);
    const assistantIndex = messages.length + 1;
    const initialAssistant = runMode === "ask" ? "" : `${modeLabel(runMode)} agent starting...`;
    setMessages([...messages, { role: "user", content: trimmed }, { role: "assistant", content: initialAssistant }]);
    setInput("");
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
      if (runMode === "ask") {
        await streamPlainChat(trimmed, prior, assistantIndex, generation, ac.signal);
      } else {
        await streamAgentChat(trimmed, prior, assistantIndex, runMode, generation, ac.signal);
      }
    } catch (e) {
      if (isActiveRun(generation) && (e as Error).name !== "AbortError") {
        toast.error("Workbench error", { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      if (isActiveRun(generation)) {
        setStreaming(false);
        if (abortRef.current === ac) abortRef.current = null;
        sessions.reload();
      }
    }
  };

  const streamPlainChat = async (
    text: string,
    prior: Msg[],
    assistantIndex: number,
    generation: number,
    signal: AbortSignal
  ) => {
    const history = [...prior, { role: "user", content: text }];
    let acc = "";
    const startedAt = performance.now();
    let ttftMs: number | undefined;

    for await (const ev of api.chat(model, history as { role: string; content: string }[], signal)) {
      if (!isActiveRun(generation)) return;
      if (ev.error) throw new Error(String(ev.error));
      const message = ev.message as { content?: string; thinking?: string } | undefined;
      const delta = message?.content ?? "";
      const thinking = message?.thinking ?? "";
      if (thinking && !acc) {
        ttftMs ??= performance.now() - startedAt;
        replaceAssistant(assistantIndex, "Thinking...", generation);
      }
      if (delta) {
        ttftMs ??= performance.now() - startedAt;
        acc += delta;
        replaceAssistant(assistantIndex, acc, generation);
      }
      if (ev.done === true) {
        commitRunStats(chatStatsFromEvent(ev, ttftMs), generation);
      }
    }
  };

  const streamAgentChat = async (
    text: string,
    prior: Msg[],
    assistantIndex: number,
    agent: Exclude<Mode, "ask">,
    generation: number,
    signal: AbortSignal
  ) => {
    let acc = "";
    let streamRunId = "";
    let streamSessionId = activeSessionRef.current;

    try {
      for await (const ev of api.agentChat(
        {
          agent,
          model,
          message: text,
          messages: prior,
          session_id: activeSessionRef.current || undefined,
          workspace: activeWorkspace,
          cwd: projectRoot.trim() || undefined,
          name: selectedSession?.name || text.slice(0, 64),
        },
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
        } else if (type === "status") {
          const event = eventFromRaw(ev);
          appendRunEvent(event, generation);
          if (!acc) replaceAssistant(assistantIndex, `${event.message || "Agent started"}...`, generation);
        } else if (type === "delta") {
          acc += String(ev.content ?? "");
          replaceAssistant(assistantIndex, acc, generation);
        } else if (type === "tool_call" || type === "tool_result" || type === "tool_calls") {
          appendRunEvent(eventFromRaw(ev), generation);
        } else if (type === "done") {
          acc = String(ev.content ?? acc);
          replaceAssistant(assistantIndex, acc, generation);
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

  const replaceAssistant = (index: number, content: string, generation: number) => {
    if (!isActiveRun(generation)) return;
    setMessages((prev) => {
      if (!isActiveRun(generation)) return prev;
      const next = [...prev];
      next[index] = { role: "assistant", content };
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

  const reviewStagedChange = async (change: StagedChangeSummary) => {
    const identity = {
      sessionId: change.session_id,
      generation: sessionGenerationRef.current,
    };
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:review:${change.id}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      const detail = await api.stagedChange(change.id);
      if (
        isActiveSessionAction(identity) &&
        detail.session_id === change.session_id &&
        detail.run_id === change.run_id
      ) setSelectedChange(detail);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        toast.error("Could not review staged change", {
          description: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
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
      if (!isActiveSessionAction(identity)) return;
      const detail = await api.stagedChange(change.id);
      if (
        isActiveSessionAction(identity) &&
        detail.session_id === change.session_id &&
        detail.run_id === change.run_id
      ) setSelectedChange(detail);
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
      if (!isActiveSessionAction(identity)) return;
      const detail = await api.stagedChange(change.id);
      if (
        isActiveSessionAction(identity) &&
        detail.session_id === change.session_id &&
        detail.run_id === change.run_id
      ) setSelectedChange(detail);
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

  const stop = () => {
    const sessionId = activeSessionRef.current;
    cancelRunThenAbort(
      approvalRef.current.run,
      (runId, approvalToken) => api.cancelAgentRun(runId, approvalToken),
      invalidateActiveRun
    );
    if (sessionId) void refreshStagedChanges(sessionId, true);
  };
  const clear = () => {
    stop();
    setMessages([]);
    setEvents([]);
    setLastStats(null);
  };
  const buildRootMissing = buildNeedsProjectRoot(mode, projectRoot);
  const workbenchControlsAreDisabled = workbenchControlsDisabled(streaming, sessionLoading);
  const sendDisabled = workbenchSendDisabled({
    model,
    mode,
    projectRoot,
    input,
    warming,
    streaming,
    sessionLoading,
  });

  return (
    <>
      <PageHeader title="Workbench" className="mb-3">
        <Button variant="ghost" size="sm" onClick={newSession}>
          <MessageSquare /> New
        </Button>
        <Button variant="ghost" size="sm" onClick={clear} disabled={!messages.length && !events.length}>
          <Trash2 /> Clear
        </Button>
      </PageHeader>

      <div className="grid min-h-[520px] grid-cols-1 gap-3 xl:h-[calc(100vh-150px)] xl:grid-cols-[270px_minmax(0,1fr)_320px]">
        <aside className="flex min-h-[320px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              <FolderOpen className="h-3.5 w-3.5" /> Workspace
            </div>
            {workspaces.loading ? (
              <Skeleton className="h-8 w-full" />
            ) : (
              <Select value={activeWorkspace} onValueChange={switchWorkspace}>
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(workspaces.data ?? []).map((w) => (
                    <SelectItem key={w.id} value={w.id}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <label
                htmlFor="workbench-project-root"
                className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint"
              >
                <Code2 className="h-3.5 w-3.5" /> Project
              </label>
              <Badge variant={buildRootMissing ? "warning" : "outline"}>
                {buildRootMissing ? "Required for Build" : "Root"}
              </Badge>
            </div>
            <Input
              id="workbench-project-root"
              value={projectRoot}
              onChange={(e) => {
                projectRootRef.current = e.target.value;
                setProjectRoot(e.target.value);
              }}
              placeholder="C:\\Users\\User\\repos\\model-hub"
              disabled={streaming}
              aria-invalid={buildRootMissing}
              aria-describedby={buildRootMissing ? "workbench-project-root-error" : undefined}
              className="h-8 text-[12.5px]"
            />
            {buildRootMissing && (
              <div id="workbench-project-root-error" className="mt-1.5 text-[11px] leading-relaxed text-warning">
                Build is disabled until a project root is set.
              </div>
            )}
            <div
              id="workbench-sandbox-status"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              {mode === "build" && projectRoot.trim() && (
                <SandboxStatusPanel root={projectRoot.trim()} check={sandboxCheck} />
              )}
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                Sessions
              </span>
              <Button variant="ghost" size="sm" className="h-7 px-2" onClick={newSession}>
                New
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {sessions.loading ? (
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
                <div className="px-2 py-8 text-center text-[12.5px] text-fg-muted">No saved sessions</div>
              )}
            </div>
          </div>
        </aside>

        <section className="flex min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <span className="text-[12px] uppercase tracking-[0.08em] text-fg-faint">Model</span>
              {installed.loading ? (
                <Skeleton className="h-8 w-48" />
              ) : models.length ? (
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="h-8 w-full max-w-[280px]">
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
              {warming && <Badge variant="info" dot>Warming</Badge>}
              {model && runningModels.has(model) && <Badge variant="success" dot>Resident</Badge>}
            </div>

            <div className="flex items-center gap-1 rounded border border-line bg-panel-2 p-0.5">
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
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Sparkles className="mb-3 h-7 w-7 text-verdant" />
                <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      disabled={sessionLoading}
                      className="min-h-[54px] rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[13px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-4xl space-y-5">
                {messages.map((m, i) => (
                  <Bubble key={`${m.role}-${i}`} role={m.role} content={m.content} model={model} />
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-line p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex items-end gap-2"
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  model
                    ? sessionLoading
                      ? "Loading session..."
                      : buildRootMissing
                      ? "Set a project root before using Build"
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
                  <Send /> {workbenchSendLabel(mode, projectRoot)}
                </Button>
              )}
            </form>
          </div>
        </section>

        <aside className="flex min-h-[360px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                <ShieldCheck className="h-3.5 w-3.5" /> Run
              </div>
              <Badge variant={mode === "build" ? "warning" : mode === "ask" ? "neutral" : "accent"}>
                {modeLabel(mode)}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[12.5px]">
              <StatTile label="Session" value={activeSessionId ? shortId(activeSessionId) : "New"} />
              <StatTile label="Events" value={String(events.length)} />
            </div>
          </div>

          {approval.pending && (
            <div className="border-b border-line p-3">
              <ApprovalCard
                approval={approval.pending}
                onDecision={(intent) => void answerPendingApproval(intent)}
              />
            </div>
          )}

          {(stagedChanges.length > 0 || selectedChange) && (
            <div className="max-h-[42%] overflow-y-auto border-b border-line p-3">
              <StagedChangesPanel
                changes={stagedChanges}
                selected={selectedChange}
                busy={changeBusy}
                onReview={(change) => void reviewStagedChange(change)}
                onApply={(change) => void applyChange(change)}
                onReject={(change) => void rejectChange(change)}
              />
            </div>
          )}

          <div className="border-b border-line p-3">
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
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
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
          </div>

          <div className="border-t border-line p-3">
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
        </aside>
      </div>
    </>
  );
}

function SandboxStatusPanel({
  root,
  check,
}: {
  root: string;
  check: SandboxCheckState;
}) {
  const status = check.root === root ? check.status : null;
  const loading = check.root === root && check.loading;
  const error = check.root === root ? check.error : null;
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

function StagedChangesPanel({
  changes,
  selected,
  busy,
  onReview,
  onApply,
  onReject,
}: {
  changes: StagedChangeSummary[];
  selected: StagedChangeDetail | null;
  busy: string;
  onReview: (change: StagedChangeSummary) => void;
  onApply: (change: StagedChangeSummary) => void;
  onReject: (change: StagedChangeSummary) => void;
}) {
  const pending = changes.filter((change) => change.status === "pending").length;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          Staged changes
        </div>
        <Badge variant={pending ? "warning" : "neutral"}>{pending} pending</Badge>
      </div>
      <div className="space-y-2">
        {changes.slice().reverse().map((change) => {
          const isBusy = Boolean(busy);
          const fullPath = stagedFullPath(change.root, change.path);
          return (
            <div key={change.id} className="rounded border border-line bg-panel-2 p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 break-all text-[12px] font-medium text-fg">{fullPath}</div>
                <Badge variant={stagedStatusVariant(change.status)}>{change.status}</Badge>
              </div>
              <div className="mt-1 break-all text-[11px] text-fg-faint">Run: {change.run_id}</div>
              <div className="mt-0.5 text-[11px] text-fg-faint">{formatBytes(change.new_size)} proposed</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onReview(change)}>
                  Review
                </Button>
                {change.status === "pending" && (
                  <>
                    <Button size="sm" disabled={isBusy} onClick={() => onApply(change)}>
                      Apply to disk
                    </Button>
                    <Button size="sm" variant="danger" disabled={isBusy} onClick={() => onReject(change)}>
                      Reject
                    </Button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {selected && (
        <div className="mt-3 rounded border border-line-strong bg-panel p-2.5">
          <div className="break-all text-[12px] font-semibold text-fg">
            Review: {stagedFullPath(selected.root, selected.path)}
          </div>
          <div className="mt-1 break-all text-[11px] text-fg-faint">Run: {selected.run_id}</div>
          <div className="mt-2 text-[10.5px] uppercase tracking-[0.08em] text-fg-faint">
            {STAGED_SNAPSHOT_LABEL}
          </div>
          <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded bg-panel-2 p-2 text-[11px] text-fg-muted">
            {selected.old_content ?? "(new file)"}
          </pre>
          <div className="mt-2 text-[10.5px] uppercase tracking-[0.08em] text-fg-faint">Proposed</div>
          <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-panel-2 p-2 text-[11px] text-fg-muted">
            {selected.new_content}
          </pre>
        </div>
      )}
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

function transcriptWithSystem(system: string, messages: Msg[]): Msg[] {
  const prompt = system.trim();
  return prompt ? [{ role: "system", content: prompt }, ...messages] : [...messages];
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

function stagedStatusVariant(
  status: StagedChangeSummary["status"]
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "pending") return "warning";
  if (status === "applied") return "success";
  if (status === "conflict") return "danger";
  if (status === "reverted") return "info";
  return "neutral";
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function nsToMs(value: unknown): number | undefined {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n / 1_000_000;
}

function chatStatsFromEvent(ev: Record<string, unknown>, ttftMs?: number): ChatStats {
  const evalMs = nsToMs(ev.eval_duration);
  const evalCount = Number(ev.eval_count ?? 0);
  const tokensPerSecond = evalMs && evalCount > 0 ? (evalCount / evalMs) * 1000 : undefined;
  return {
    ttft_ms: ttftMs,
    load_ms: nsToMs(ev.load_duration),
    prompt_ms: nsToMs(ev.prompt_eval_duration),
    eval_ms: evalMs,
    eval_count: evalCount > 0 ? evalCount : undefined,
    tokens_per_second: tokensPerSecond,
  };
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
