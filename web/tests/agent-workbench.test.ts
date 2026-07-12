import assert from "node:assert/strict";
import test from "node:test";

import {
  STAGED_SNAPSHOT_LABEL,
  agentModeNeedsProject,
  approvalLockKey,
  approvalDecisionIntent,
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
} from "../src/lib/agent-workbench.ts";

test("approval actions preserve the run and ask rendered by the card", () => {
  const intent = approvalDecisionIntent(
    { runId: "run-rendered", askId: "ask-rendered" },
    "allow",
    true
  );

  assert.deepEqual(intent, {
    runId: "run-rendered",
    askId: "ask-rendered",
    decision: "allow",
    remember: true,
  });
});

test("approval responses are relevant only while their exact run and ask remain pending", () => {
  const newerAsk = {
    run: { runId: "run-a", approvalToken: "token-a" },
    pending: { runId: "run-a", askId: "ask-b" },
  };

  assert.equal(isApprovalResponseRelevant(newerAsk, "run-a", "ask-a"), false);
  assert.equal(isApprovalResponseRelevant(newerAsk, "run-a", "ask-b"), true);
  assert.equal(isApprovalResponseRelevant(newerAsk, "run-b", "ask-b"), false);
  assert.equal(
    isApprovalResponseRelevant({ run: newerAsk.run, pending: null }, "run-a", "ask-a"),
    false
  );
});

test("approval lock release clears only the exact lock owner", () => {
  const lockA = approvalLockKey("run-a", "ask-a");
  const lockB = approvalLockKey("run-a", "ask-b");

  assert.equal(releaseApprovalLock(lockA, "run-a", "ask-a"), "");
  assert.equal(releaseApprovalLock(lockB, "run-a", "ask-a"), lockB);
  assert.equal(releaseApprovalLock(lockA, "run-b", "ask-a"), lockA);
});

test("every Workbench mode requires a registered project identity", () => {
  assert.equal(agentModeNeedsProject("build", ""), true);
  assert.equal(agentModeNeedsProject("plan", "   "), true);
  assert.equal(agentModeNeedsProject("explore", "project-1"), false);
  assert.equal(agentModeNeedsProject("ask", ""), true);
  assert.equal(workbenchSendLabel("build", ""), "Select project");
  assert.equal(workbenchSendLabel("plan", "project-1"), "Send");
});

test("Ask completion stats preserve TTFT and Ollama token timing", () => {
  assert.deepEqual(
    chatStatsFromEvent({
      load_duration: 2_000_000,
      prompt_eval_duration: 3_000_000,
      eval_count: 8,
      eval_duration: 2_000_000_000,
    }, 125),
    {
      ttft_ms: 125,
      load_ms: 2,
      prompt_ms: 3,
      eval_ms: 2000,
      eval_count: 8,
      tokens_per_second: 4,
    }
  );
});

test("display-only reasoning placeholders never become durable model history", () => {
  assert.deepEqual(
    durableTranscript("Stay local", [
      { role: "user", content: "First turn" },
      { role: "assistant", content: "Thinking...", ephemeral: true },
      { role: "assistant", content: "A real answer" },
      { role: "assistant", content: "No response text returned.", ephemeral: true },
    ]),
    [
      { role: "system", content: "Stay local" },
      { role: "user", content: "First turn" },
      { role: "assistant", content: "A real answer" },
    ]
  );
});

test("transcript filtering is metadata-based, not content-based", () => {
  assert.deepEqual(
    durableTranscript("", [
      { role: "assistant", content: "Thinking..." },
      { role: "assistant", content: "No response text returned." },
    ]),
    [
      { role: "assistant", content: "Thinking..." },
      { role: "assistant", content: "No response text returned." },
    ]
  );
});

test("session loading disables Workbench controls and send even with otherwise valid input", () => {
  assert.equal(workbenchControlsDisabled(false, true), true);
  assert.equal(workbenchControlsDisabled(false, false), false);
  assert.equal(
    workbenchSendDisabled({
      model: "qwen",
      mode: "plan",
      projectId: "project-1",
      input: "Inspect this",
      warming: false,
      streaming: false,
      sessionLoading: true,
    }),
    true
  );
  assert.equal(
    workbenchSendDisabled({
      model: "qwen",
      mode: "plan",
      projectId: "project-1",
      input: "Inspect this",
      warming: false,
      streaming: false,
      sessionLoading: false,
    }),
    false
  );
});

test("only the latest staged-list request for the active session may commit", () => {
  assert.equal(
    shouldCommitStagedList("session-a", 4, { sessionId: "session-a", sequence: 4 }),
    true
  );
  assert.equal(
    shouldCommitStagedList("session-a", 4, { sessionId: "session-a", sequence: 3 }),
    false
  );
  assert.equal(
    shouldCommitStagedList("session-b", 4, { sessionId: "session-a", sequence: 4 }),
    false
  );
});

test("staged actions and stream updates are rejected after their generation changes", () => {
  assert.equal(
    isCurrentSessionAction("session-a", 8, { sessionId: "session-a", generation: 8 }),
    true
  );
  assert.equal(
    isCurrentSessionAction("session-a", 9, { sessionId: "session-a", generation: 8 }),
    false
  );
  assert.equal(
    isCurrentSessionAction("session-b", 8, { sessionId: "session-a", generation: 8 }),
    false
  );
  assert.equal(isCurrentGeneration(12, 12), true);
  assert.equal(isCurrentGeneration(13, 12), false);
});

test("staged identities display the full root, path, and immutable snapshot wording", () => {
  assert.equal(stagedFullPath("C:\\work\\repo", "src\\app.ts"), "C:\\work\\repo\\src\\app.ts");
  assert.equal(stagedFullPath("/work/repo/", "/src/app.ts"), "/work/repo/src/app.ts");
  assert.equal(STAGED_SNAPSHOT_LABEL, "Snapshot at staging");
});

test("staged 409 responses distinguish disk conflicts from no-longer-pending state", () => {
  assert.deepEqual(
    stagedActionFailure("apply", 409, {
      status: "conflict",
      disk_hash: "disk",
      base_hash: "base",
    }),
    {
      title: "Apply blocked by a disk conflict",
      description: "The file changed after staging. Nothing was overwritten.",
    }
  );
  assert.deepEqual(
    stagedActionFailure("apply", 409, { status: "not_pending", current: "applied" }),
    {
      title: "Could not apply staged change",
      description: "This staged change is no longer pending (current status: applied).",
    }
  );
  assert.deepEqual(
    stagedActionFailure("reject", 409, { status: "not_pending", current: "rejected" }),
    {
      title: "Could not reject staged change",
      description: "This staged change is no longer pending (current status: rejected).",
    }
  );
});
