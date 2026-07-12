import type { AgentApprovalDecision, SessionMessage } from "./types";

export type WorkbenchMode = "ask" | "plan" | "explore" | "build";

export interface ApprovalDecisionIntent {
  runId: string;
  askId: string;
  decision: AgentApprovalDecision;
  remember: boolean;
}

export interface ApprovalResponseState {
  run: { runId: string } | null;
  pending: { runId: string; askId: string } | null;
}

export interface WorkbenchSendState {
  model: string;
  mode: WorkbenchMode;
  projectId: string;
  input: string;
  warming: boolean;
  streaming: boolean;
  sessionLoading: boolean;
}

export interface SessionActionIdentity {
  sessionId: string;
  generation: number;
}

export interface StagedListIdentity {
  sessionId: string;
  sequence: number;
}

export interface StagedActionFailure {
  title: string;
  description: string;
}

export interface ChatStats {
  ttft_ms?: number;
  load_ms?: number;
  prompt_ms?: number;
  eval_ms?: number;
  eval_count?: number;
  tokens_per_second?: number;
}

export interface WorkbenchMessage extends SessionMessage {
  ephemeral?: boolean;
}

export const STAGED_SNAPSHOT_LABEL = "Snapshot at staging";

function nsToMs(value: unknown): number | undefined {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n / 1_000_000;
}

export function chatStatsFromEvent(
  event: Record<string, unknown>,
  ttftMs?: number
): ChatStats {
  const evalMs = nsToMs(event.eval_duration);
  const evalCount = Number(event.eval_count ?? 0);
  const tokensPerSecond = evalMs && evalCount > 0
    ? (evalCount / evalMs) * 1000
    : undefined;
  return {
    ttft_ms: ttftMs,
    load_ms: nsToMs(event.load_duration),
    prompt_ms: nsToMs(event.prompt_eval_duration),
    eval_ms: evalMs,
    eval_count: evalCount > 0 ? evalCount : undefined,
    tokens_per_second: tokensPerSecond,
  };
}

export function durableTranscript(
  system: string,
  messages: readonly WorkbenchMessage[]
): SessionMessage[] {
  const durable = messages
    .filter((message) => !message.ephemeral)
    .map((message) => ({ role: message.role, content: message.content }));
  const prompt = system.trim();
  return prompt ? [{ role: "system", content: prompt }, ...durable] : durable;
}

export function approvalDecisionIntent(
  approval: { runId: string; askId: string },
  decision: AgentApprovalDecision,
  remember: boolean
): ApprovalDecisionIntent {
  return {
    runId: approval.runId,
    askId: approval.askId,
    decision,
    remember,
  };
}

export function approvalLockKey(runId: string, askId: string): string {
  return JSON.stringify([runId, askId]);
}

export function releaseApprovalLock(
  currentLock: string,
  runId: string,
  askId: string
): string {
  return currentLock === approvalLockKey(runId, askId) ? "" : currentLock;
}

export function isApprovalResponseRelevant(
  state: ApprovalResponseState,
  runId: string,
  askId: string
): boolean {
  return Boolean(
    state.run?.runId === runId &&
    state.pending?.runId === runId &&
    state.pending.askId === askId
  );
}

export function agentModeNeedsProject(_mode: WorkbenchMode, projectId: string): boolean {
  return projectId.trim().length === 0;
}

export function workbenchSendLabel(mode: WorkbenchMode, projectId: string): string {
  return agentModeNeedsProject(mode, projectId) ? "Select project" : "Send";
}

export function workbenchControlsDisabled(streaming: boolean, sessionLoading: boolean): boolean {
  return streaming || sessionLoading;
}

export function workbenchSendDisabled(state: WorkbenchSendState): boolean {
  return (
    !state.model ||
    state.warming ||
    workbenchControlsDisabled(state.streaming, state.sessionLoading) ||
    !state.input.trim() ||
    agentModeNeedsProject(state.mode, state.projectId)
  );
}

export function isCurrentGeneration(current: number, expected: number): boolean {
  return current === expected;
}

export function isCurrentSessionAction(
  activeSessionId: string,
  currentGeneration: number,
  action: SessionActionIdentity
): boolean {
  return (
    activeSessionId === action.sessionId &&
    isCurrentGeneration(currentGeneration, action.generation)
  );
}

export function shouldCommitStagedList(
  activeSessionId: string,
  currentSequence: number,
  request: StagedListIdentity
): boolean {
  return activeSessionId === request.sessionId && currentSequence === request.sequence;
}

export function stagedFullPath(root: string, path: string): string {
  const trimmedRoot = root.trim().replace(/[\\/]+$/, "");
  const trimmedPath = path.trim().replace(/^[\\/]+/, "");
  if (!trimmedRoot) return trimmedPath;
  if (!trimmedPath) return trimmedRoot;
  const separator = trimmedRoot.includes("\\") ? "\\" : "/";
  return `${trimmedRoot}${separator}${trimmedPath}`;
}

function responseField(body: unknown, field: string): string {
  if (!body || typeof body !== "object" || !(field in body)) return "";
  const value = (body as Record<string, unknown>)[field];
  return typeof value === "string" ? value : "";
}

export function stagedActionFailure(
  action: "apply" | "reject",
  status: number,
  body: unknown
): StagedActionFailure | null {
  if (status !== 409) return null;

  const responseStatus = responseField(body, "status");
  if (action === "apply" && responseStatus === "conflict") {
    return {
      title: "Apply blocked by a disk conflict",
      description: "The file changed after staging. Nothing was overwritten.",
    };
  }

  if (responseStatus === "not_pending") {
    const current = responseField(body, "current") || "unknown";
    return {
      title: `Could not ${action} staged change`,
      description: `This staged change is no longer pending (current status: ${current}).`,
    };
  }

  const error = responseField(body, "error");
  return {
    title: `Could not ${action} staged change`,
    description: error || "The server rejected this action because the staged change state changed.",
  };
}
