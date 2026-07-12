import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_PROJECT_SELECTION,
  buildAgentChatPayload,
  isCurrentProjectRegistration,
  isCurrentWorkbenchContext,
  projectFilterForSelection,
  projectIdForSelection,
  projectSelectionAfterLoad,
  projectRegistrationDisabled,
  sanitizeProjectDescription,
  selectedProjectFor,
  shouldRefreshProjectsAfterRegistration,
  workbenchContextKey,
} from "../src/lib/agent-context.ts";

test("project selection maps explicit legacy context without inventing a project", () => {
  assert.equal(projectFilterForSelection("project-1"), "project-1");
  assert.equal(projectIdForSelection("project-1"), "project-1");
  assert.equal(projectFilterForSelection(LEGACY_PROJECT_SELECTION), "unassigned");
  assert.equal(projectIdForSelection(LEGACY_PROJECT_SELECTION), null);
  assert.equal(projectFilterForSelection(""), null);
  assert.equal(projectIdForSelection(""), null);
});

test("project refresh preserves a nonempty pending selection but cannot make it runnable", () => {
  const projects = [{
    id: "project-old",
    workspace: "client-a",
    name: "Old",
    description: "",
    root: "C:\\work\\old",
    status: "active",
    created_at: 1,
    updated_at: 1,
  }] as const;

  assert.equal(projectSelectionAfterLoad(projects, "project-new"), "project-new");
  assert.equal(selectedProjectFor(projects, "project-new"), null);
  assert.equal(selectedProjectFor(projects, "project-old")?.id, "project-old");
  assert.equal(projectSelectionAfterLoad(projects, ""), "project-old");
  assert.equal(projectSelectionAfterLoad([], ""), LEGACY_PROJECT_SELECTION);
});

test("agent payload is bound only by project id and never carries cwd or workspace", () => {
  const payload = buildAgentChatPayload({
    agent: "build",
    model: "qwen",
    message: "Implement the change",
    messages: [{ role: "user", content: "Prior" }],
    sessionId: "session-1",
    projectId: "project-1",
    name: "Thread",
  });

  assert.deepEqual(payload, {
    agent: "build",
    model: "qwen",
    message: "Implement the change",
    messages: [{ role: "user", content: "Prior" }],
    session_id: "session-1",
    project_id: "project-1",
    name: "Thread",
  });
  assert.equal("cwd" in payload, false);
  assert.equal("workspace" in payload, false);
});

test("Ask uses the same durable project-bound agent payload", () => {
  const payload = buildAgentChatPayload({
    agent: "ask",
    model: "qwen-local",
    message: "Remember this thread",
    messages: [],
    projectId: "project-1",
    name: "Local Ask",
  });

  assert.deepEqual(payload, {
    agent: "ask",
    model: "qwen-local",
    message: "Remember this thread",
    messages: [],
    project_id: "project-1",
    name: "Local Ask",
  });
});

test("stale context responses cannot commit after workspace or project changes", () => {
  const request = { key: workbenchContextKey("client-a", "project-a"), generation: 4 };

  assert.equal(isCurrentWorkbenchContext("client-a", "project-a", 4, request), true);
  assert.equal(isCurrentWorkbenchContext("client-b", "project-a", 4, request), false);
  assert.equal(isCurrentWorkbenchContext("client-a", "project-b", 4, request), false);
  assert.equal(isCurrentWorkbenchContext("client-a", "project-a", 5, request), false);
});

test("project registration ownership is invalidated by context or request sequence changes", () => {
  const request = {
    workspaceId: "client-a",
    context: { key: workbenchContextKey("client-a", "project-a"), generation: 4 },
    sequence: 7,
  };

  assert.equal(
    isCurrentProjectRegistration("client-a", "project-a", 4, 7, request),
    true
  );
  assert.equal(
    isCurrentProjectRegistration("client-a", "project-b", 5, 8, request),
    false
  );
  assert.equal(
    isCurrentProjectRegistration("client-b", "project-a", 5, 8, request),
    false
  );
});

test("a successful stale registration refreshes only while its workspace remains active", () => {
  assert.equal(shouldRefreshProjectsAfterRegistration("client-a", "client-a"), true);
  assert.equal(shouldRefreshProjectsAfterRegistration("client-b", "client-a"), false);
});

test("project registration requires bounded nonempty fields and blocks duplicate submits", () => {
  assert.equal(projectRegistrationDisabled("Project", "C:\\work\\repo", false), false);
  assert.equal(projectRegistrationDisabled("", "C:\\work\\repo", false), true);
  assert.equal(projectRegistrationDisabled("Project", " ", false), true);
  assert.equal(projectRegistrationDisabled("Project", "C:\\work\\repo", true), true);
  assert.equal(projectRegistrationDisabled("x".repeat(121), "C:\\work\\repo", false), true);
  assert.equal(projectRegistrationDisabled("Project", "x".repeat(4097), false), true);
  assert.equal(
    projectRegistrationDisabled("Project", "C:\\work\\repo", false, "x".repeat(1001)),
    true
  );
});

test("project descriptions are normalized to bounded single-line text", () => {
  assert.equal(
    sanitizeProjectDescription("Client\noperations\tportal\u007f"),
    "Clientoperationsportal"
  );
  assert.equal(sanitizeProjectDescription(`  ${"x".repeat(1005)}  `).length, 1000);
});
