import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalMayBeRemembered,
  cancelRunThenAbort,
  createRunCancelRequest,
  parseRunTaskTarget,
  sandboxPresentation,
  shouldCommitSandboxStatus,
} from "../src/lib/agent-command.ts";

const PINNED_IMAGE = `example/lac@sha256:${"a".repeat(64)}`;
const IMAGE_ID = `sha256:${"b".repeat(64)}`;
const OVERLAY_DIGEST = "c".repeat(64);
const CONFIG_DIGEST = "d".repeat(64);
const BASE_HASH = "e".repeat(64);
const CONTENT_HASH = "f".repeat(64);
const STAGED_CHANGE = {
  id: "a1b2c3d4e5f607",
  path: "src/app.py",
  base_hash: BASE_HASH,
  updated_at: 1234.5,
  content_hash: CONTENT_HASH,
} as const;

const validRunTaskTarget = {
  kind: "sandbox_task",
  name: "test:unit",
  argv: ["python", "-m", "pytest", "-q"],
  root: "C:\\work\\repo",
  image: PINNED_IMAGE,
  image_id: IMAGE_ID,
  timeout_seconds: 120,
  network: "none",
  staged_overlay_digest: OVERLAY_DIGEST,
  config_digest: CONFIG_DIGEST,
  staged_changes: [STAGED_CHANGE],
} as const;

test("sandbox status commits only for the latest exact selected root", () => {
  const request = { root: "C:\\work\\repo", sequence: 4 };

  assert.equal(shouldCommitSandboxStatus("C:\\work\\repo", 4, request), true);
  assert.equal(shouldCommitSandboxStatus("C:\\work\\other", 4, request), false);
  assert.equal(shouldCommitSandboxStatus("C:\\work\\repo", 5, request), false);
});

test("sandbox status presentation stays honest and distinguishes readiness codes", () => {
  assert.deepEqual(
    sandboxPresentation({
      backend: "docker",
      available: true,
      code: "ready",
      message: "Task sandbox ready.",
      tasks: ["test", "typecheck"],
      image: PINNED_IMAGE,
      network: "none",
    }),
    {
      label: "Ready",
      tone: "success",
      detail: "Task sandbox ready.",
    }
  );

  assert.deepEqual(
    sandboxPresentation({
      backend: "docker",
      available: false,
      code: "daemon_unavailable",
      message: "Docker Desktop is installed, but its daemon is unavailable.",
      tasks: [],
      network: "none",
    }),
    {
      label: "Docker unavailable",
      tone: "warning",
      detail: "Docker Desktop is installed, but its daemon is unavailable.",
    }
  );

  assert.equal(
    sandboxPresentation({
      backend: "docker",
      available: false,
      code: "image_unpinned",
      message: "The sandbox image must use a digest.",
      tasks: [],
      network: "none",
    }).label,
    "Image unpinned"
  );
});

test("run_task approval target is parsed into exact structured details", () => {
  assert.deepEqual(
    parseRunTaskTarget({
      ...validRunTaskTarget,
    }),
    {
      name: "test:unit",
      argv: ["python", "-m", "pytest", "-q"],
      root: "C:\\work\\repo",
      image: PINNED_IMAGE,
      imageId: IMAGE_ID,
      timeoutSeconds: 120,
      network: "none",
      stagedOverlayDigest: OVERLAY_DIGEST,
      configDigest: CONFIG_DIGEST,
      stagedChanges: [{
        id: STAGED_CHANGE.id,
        path: STAGED_CHANGE.path,
        baseHash: BASE_HASH,
        updatedAt: STAGED_CHANGE.updated_at,
        contentHash: CONTENT_HASH,
      }],
    }
  );
});

test("malformed run_task details fail closed without throwing", () => {
  assert.equal(parseRunTaskTarget("test"), null);
  assert.equal(parseRunTaskTarget({ kind: "other", name: "test" }), null);
  assert.equal(parseRunTaskTarget({ kind: "sandbox_task", name: "test" }), null);
});

test("run_task target rejects every out-of-contract security field", () => {
  const invalidTargets = {
    unexpected_field: { ...validRunTaskTarget, mount: "C:\\" },
    invalid_task_name: { ...validRunTaskTarget, name: "../test" },
    too_many_args: { ...validRunTaskTarget, argv: Array.from({ length: 65 }, () => "x") },
    empty_arg: { ...validRunTaskTarget, argv: ["python", ""] },
    control_character_arg: { ...validRunTaskTarget, argv: ["python", "bad\narg"] },
    option_as_executable: { ...validRunTaskTarget, argv: ["-c", "bad"] },
    oversized_arg: { ...validRunTaskTarget, argv: ["x".repeat(4097)] },
    oversized_argv_total: {
      ...validRunTaskTarget,
      argv: Array.from({ length: 9 }, () => "x".repeat(4096)),
    },
    unpinned_image: { ...validRunTaskTarget, image: "python:latest" },
    oversized_image: { ...validRunTaskTarget, image: `repo/${"x".repeat(513)}@sha256:${"a".repeat(64)}` },
    invalid_image_id: { ...validRunTaskTarget, image_id: "not-an-image-id" },
    zero_timeout: { ...validRunTaskTarget, timeout_seconds: 0 },
    fractional_timeout: { ...validRunTaskTarget, timeout_seconds: 1.5 },
    oversized_timeout: { ...validRunTaskTarget, timeout_seconds: 301 },
    network_enabled: { ...validRunTaskTarget, network: "bridge" },
    invalid_overlay_digest: { ...validRunTaskTarget, staged_overlay_digest: "sha256:overlay" },
    missing_config_digest: (({ config_digest: _digest, ...rest }) => rest)(validRunTaskTarget),
    invalid_config_digest: { ...validRunTaskTarget, config_digest: "sha256:config" },
    uppercase_config_digest: { ...validRunTaskTarget, config_digest: "A".repeat(64) },
    missing_staged_changes: (({ staged_changes: _changes, ...rest }) => rest)(validRunTaskTarget),
    staged_changes_not_array: { ...validRunTaskTarget, staged_changes: {} },
    too_many_staged_changes: {
      ...validRunTaskTarget,
      staged_changes: Array.from({ length: 17 }, (_, index) => ({
        ...STAGED_CHANGE,
        id: index.toString(16).padStart(14, "0"),
      })),
    },
    staged_row_extra_field: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, root: "C:\\work\\repo" }],
    },
    staged_row_missing_field: {
      ...validRunTaskTarget,
      staged_changes: [(({ content_hash: _hash, ...rest }) => rest)(STAGED_CHANGE)],
    },
    invalid_staged_id: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, id: "ABC123" }],
    },
    empty_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "" }],
    },
    absolute_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "/src/app.py" }],
    },
    backslash_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src\\app.py" }],
    },
    alternate_data_stream_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src/app.py:payload" }],
    },
    reserved_device_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "NUL.txt" }],
    },
    reserved_console_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "CONOUT$/capture.txt" }],
    },
    trailing_dot_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src/app.py." }],
    },
    trailing_space_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src/app.py " }],
    },
    parent_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src/../app.py" }],
    },
    control_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "src/bad\n.py" }],
    },
    oversized_staged_path: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, path: "x".repeat(513) }],
    },
    invalid_base_hash: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, base_hash: "sha256:base" }],
    },
    invalid_staged_revision: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, updated_at: -1 }],
    },
    nonfinite_staged_revision: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, updated_at: Number.POSITIVE_INFINITY }],
    },
    invalid_content_hash: {
      ...validRunTaskTarget,
      staged_changes: [{ ...STAGED_CHANGE, content_hash: "sha256:content" }],
    },
    mismatched_local_image: {
      ...validRunTaskTarget,
      image: `sha256:${"9".repeat(64)}`,
    },
  };

  for (const [name, target] of Object.entries(invalidTargets)) {
    assert.equal(parseRunTaskTarget(target), null, name);
  }
});

test("run_task accepts an exact local sha256 image reference", () => {
  assert.ok(parseRunTaskTarget({ ...validRunTaskTarget, image: IMAGE_ID }));
});

test("run_task accepts no staged rows and a null staged base hash", () => {
  assert.ok(parseRunTaskTarget({ ...validRunTaskTarget, staged_changes: [] }));
  assert.ok(parseRunTaskTarget({
    ...validRunTaskTarget,
    staged_changes: [{ ...STAGED_CHANGE, base_hash: null }],
  }));
});

test("run_task is never rememberable even if an event incorrectly claims it is", () => {
  assert.equal(approvalMayBeRemembered("run_task", true), false);
  assert.equal(approvalMayBeRemembered("write_file", true), true);
  assert.equal(approvalMayBeRemembered("write_file", false), false);
});

test("cancel request stays bound to the exact active run capability", () => {
  assert.deepEqual(
    createRunCancelRequest({ runId: "run-a", approvalToken: "token-a" }),
    { runId: "run-a", approvalToken: "token-a" }
  );
  assert.equal(createRunCancelRequest(null), null);
  assert.equal(createRunCancelRequest({ runId: " ", approvalToken: "token-a" }), null);
  assert.equal(createRunCancelRequest({ runId: "run-a", approvalToken: " " }), null);
});

test("Stop dispatches exact capability cancellation before aborting the stream", () => {
  const calls: string[] = [];
  cancelRunThenAbort(
    { runId: "run-a", approvalToken: "token-a" },
    async (runId, approvalToken) => {
      calls.push(`cancel:${runId}:${approvalToken}`);
    },
    () => calls.push("abort")
  );
  assert.deepEqual(calls, ["cancel:run-a:token-a", "abort"]);

  const withoutCapability: string[] = [];
  cancelRunThenAbort(
    null,
    async () => {
      withoutCapability.push("cancel");
    },
    () => withoutCapability.push("abort")
  );
  assert.deepEqual(withoutCapability, ["abort"]);
});

test("a synchronous cancellation failure still aborts the stream", () => {
  const calls: string[] = [];
  cancelRunThenAbort(
    { runId: "run-a", approvalToken: "token-a" },
    () => {
      calls.push("cancel");
      throw new Error("offline");
    },
    () => calls.push("abort")
  );
  assert.deepEqual(calls, ["cancel", "abort"]);
});
