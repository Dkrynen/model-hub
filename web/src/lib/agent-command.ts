import type { AgentSandboxStatus, SandboxTaskApprovalTarget } from "./types";

export interface SandboxRequestIdentity {
  root: string;
  sequence: number;
}

export interface SandboxPresentation {
  label: string;
  tone: "success" | "warning";
  detail: string;
}

export interface RunCapability {
  runId: string;
  approvalToken: string;
}

export interface RunTaskDetails {
  name: string;
  argv: string[];
  root: string;
  image: string;
  imageId: string;
  timeoutSeconds: number;
  network: "none";
  stagedOverlayDigest: string;
  configDigest: string;
  stagedChanges: RunTaskStagedChangeDetails[];
}

export interface RunTaskStagedChangeDetails {
  id: string;
  path: string;
  baseHash: string | null;
  updatedAt: number;
  contentHash: string;
}

const SANDBOX_CODE_LABELS: Record<string, string> = {
  ready: "Ready",
  sandbox_unconfigured: "Not configured",
  docker_cli_missing: "Docker CLI missing",
  docker_context_unavailable: "Docker context unavailable",
  docker_context_refused: "Docker context refused",
  docker_daemon_unavailable: "Docker unavailable",
  docker_non_linux: "Linux context required",
  docker_image_unavailable: "Image unavailable",
  docker_image_refused: "Image refused",
  sandbox_busy: "Sandbox busy",
  daemon_unavailable: "Docker unavailable",
  context_refused: "Docker context refused",
  remote_context_refused: "Docker context refused",
  non_linux_context: "Linux context required",
  image_unconfigured: "Image not configured",
  image_unpinned: "Image unpinned",
  image_unavailable: "Image unavailable",
  tasks_unconfigured: "Tasks not configured",
  no_valid_tasks: "No valid tasks",
  invalid_config: "Invalid sandbox config",
};

const RUN_TASK_TARGET_KEYS = [
  "argv",
  "config_digest",
  "image",
  "image_id",
  "kind",
  "name",
  "network",
  "root",
  "staged_changes",
  "staged_overlay_digest",
  "timeout_seconds",
] as const;
const TASK_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
const LOCAL_IMAGE_ID_PATTERN = /^sha256:[0-9a-fA-F]{64}$/;
const PINNED_IMAGE_PATTERN = /^[A-Za-z0-9][^\s@]*@sha256:[0-9a-fA-F]{64}$/;
const OVERLAY_DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const STAGED_CHANGE_KEYS = [
  "base_hash",
  "content_hash",
  "id",
  "path",
  "updated_at",
] as const;
const STAGED_CHANGE_ID_PATTERN = /^[0-9a-f]{14}$/;
const LOWER_HEX_DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const INVALID_WINDOWS_PATH_CHARS = /[<>:"\\|?*\u0000-\u001f\u007f]/;
const RESERVED_WINDOWS_PATH_STEM = /^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|(?:com|lpt)(?:[1-9]|\u00b9|\u00b2|\u00b3))$/i;

function nonemptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function fallbackCodeLabel(code: string): string {
  const label = code.trim().replace(/[_-]+/g, " ");
  if (!label) return "Unavailable";
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function hasExactKeys(value: object, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isExactImageReference(value: unknown): value is string {
  return nonemptyString(value) && value.length <= 512 && (
    LOCAL_IMAGE_ID_PATTERN.test(value) || PINNED_IMAGE_PATTERN.test(value)
  );
}

function isBoundedArgv(value: unknown): value is string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 64) return false;
  let total = 0;
  for (let index = 0; index < value.length; index += 1) {
    const arg = value[index];
    if (
      typeof arg !== "string" ||
      arg.length === 0 ||
      arg.length > 4096 ||
      [...arg].some((character) => character.charCodeAt(0) < 32) ||
      (index === 0 && arg.startsWith("-"))
    ) return false;
    total += arg.length;
    if (total > 32768) return false;
  }
  return true;
}

function isRelativePosixPath(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 512 ||
    value.startsWith("/") ||
    value.includes("\\") ||
    [...value].some((character) => character.charCodeAt(0) < 32)
  ) return false;
  const parts = value.split("/");
  return parts.every((part) => {
    const stem = part.split(".", 1)[0];
    return part.length > 0 &&
      part !== "." &&
      part !== ".." &&
      !part.endsWith(".") &&
      !part.endsWith(" ") &&
      !INVALID_WINDOWS_PATH_CHARS.test(part) &&
      !RESERVED_WINDOWS_PATH_STEM.test(stem);
  });
}

function parseStagedChanges(value: unknown): RunTaskStagedChangeDetails[] | null {
  if (!Array.isArray(value) || value.length > 16) return null;
  const parsed: RunTaskStagedChangeDetails[] = [];
  for (const row of value) {
    if (
      !row ||
      typeof row !== "object" ||
      Array.isArray(row) ||
      !hasExactKeys(row, STAGED_CHANGE_KEYS)
    ) return null;
    const item = row as Record<string, unknown>;
    if (
      typeof item.id !== "string" ||
      !STAGED_CHANGE_ID_PATTERN.test(item.id) ||
      !isRelativePosixPath(item.path) ||
      !(
        item.base_hash === null ||
        (typeof item.base_hash === "string" && LOWER_HEX_DIGEST_PATTERN.test(item.base_hash))
      ) ||
      typeof item.updated_at !== "number" ||
      !Number.isFinite(item.updated_at) ||
      item.updated_at < 0 ||
      typeof item.content_hash !== "string" ||
      !LOWER_HEX_DIGEST_PATTERN.test(item.content_hash)
    ) return null;
    parsed.push({
      id: item.id,
      path: item.path,
      baseHash: item.base_hash,
      updatedAt: item.updated_at,
      contentHash: item.content_hash,
    });
  }
  return parsed;
}

export function shouldCommitSandboxStatus(
  selectedRoot: string,
  currentSequence: number,
  request: SandboxRequestIdentity
): boolean {
  return selectedRoot === request.root && currentSequence === request.sequence;
}

export function sandboxPresentation(status: AgentSandboxStatus): SandboxPresentation {
  return {
    label: status.available
      ? "Ready"
      : SANDBOX_CODE_LABELS[status.code] ?? fallbackCodeLabel(status.code),
    tone: status.available ? "success" : "warning",
    detail: status.message || (status.available
      ? "Configured verification tasks can run in the disposable sandbox."
      : "Sandboxed verification tasks are unavailable."),
  };
}

export function parseRunTaskTarget(target: unknown): RunTaskDetails | null {
  if (
    !target ||
    typeof target !== "object" ||
    Array.isArray(target) ||
    !hasExactKeys(target, RUN_TASK_TARGET_KEYS)
  ) return null;
  const value = target as Partial<SandboxTaskApprovalTarget>;
  const stagedChanges = parseStagedChanges(value.staged_changes);
  if (
    value.kind !== "sandbox_task" ||
    typeof value.name !== "string" ||
    !TASK_NAME_PATTERN.test(value.name) ||
    !isBoundedArgv(value.argv) ||
    !nonemptyString(value.root) ||
    !isExactImageReference(value.image) ||
    typeof value.image_id !== "string" ||
    !LOCAL_IMAGE_ID_PATTERN.test(value.image_id) ||
    typeof value.timeout_seconds !== "number" ||
    !Number.isInteger(value.timeout_seconds) ||
    value.timeout_seconds < 1 ||
    value.timeout_seconds > 300 ||
    value.network !== "none" ||
    typeof value.staged_overlay_digest !== "string" ||
    !OVERLAY_DIGEST_PATTERN.test(value.staged_overlay_digest) ||
    typeof value.config_digest !== "string" ||
    !LOWER_HEX_DIGEST_PATTERN.test(value.config_digest) ||
    stagedChanges === null ||
    (LOCAL_IMAGE_ID_PATTERN.test(value.image) && value.image !== value.image_id)
  ) return null;

  return {
    name: value.name,
    argv: [...value.argv],
    root: value.root,
    image: value.image,
    imageId: value.image_id,
    timeoutSeconds: value.timeout_seconds,
    network: value.network,
    stagedOverlayDigest: value.staged_overlay_digest,
    configDigest: value.config_digest,
    stagedChanges,
  };
}

export function approvalMayBeRemembered(
  tool: string,
  backendRememberable: boolean
): boolean {
  return tool !== "run_task" && backendRememberable;
}

export function createRunCancelRequest(run: RunCapability | null): RunCapability | null {
  if (!run || !nonemptyString(run.runId) || !nonemptyString(run.approvalToken)) return null;
  return { runId: run.runId, approvalToken: run.approvalToken };
}

export function cancelRunThenAbort(
  run: RunCapability | null,
  cancel: (runId: string, approvalToken: string) => Promise<unknown>,
  abort: () => void
): void {
  const request = createRunCancelRequest(run);
  let cancellation: Promise<unknown> | null = null;
  if (request) {
    try {
      cancellation = cancel(request.runId, request.approvalToken);
    } catch {
      cancellation = null;
    }
  }
  abort();
  void cancellation?.catch(() => undefined);
}
