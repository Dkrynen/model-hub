import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/pages/chat.tsx", import.meta.url), "utf8");
const pickerSource = readFileSync(
  new URL("../src/components/workbench/context-picker.tsx", import.meta.url),
  "utf8"
);

test("Workbench source has no browser-stored root or global workspace mutation", () => {
  assert.doesNotMatch(source, /lac\.workbench\.projectRoot/);
  assert.doesNotMatch(source, /localStorage/);
  assert.doesNotMatch(source, /api\.switchWorkspace/);
  assert.doesNotMatch(source, /\bcwd\s*:/);
  assert.match(source, /<ContextPicker/);
  assert.match(source, />\s*Threads\s*</);
  assert.match(source, /const selectedProjectId = selectedProject\?\.id \?\? ""/);
});

test("registration description is a sanitized single-line field", () => {
  assert.doesNotMatch(pickerSource, /<textarea/);
  assert.match(pickerSource, /sanitizeProjectDescription\(event\.target\.value\)/);
});

test("project context reset clears thread-owned prompts and drafts", () => {
  const start = source.indexOf("const resetWorkbenchContext");
  const end = source.indexOf("const switchWorkspace", start);
  assert.ok(start >= 0 && end > start);
  const resetSource = source.slice(start, end);
  assert.match(resetSource, /setSystem\(""\)/);
  assert.match(resetSource, /setInput\(""\)/);
  assert.match(resetSource, /registrationRequestSequenceRef\.current \+= 1/);
  assert.match(resetSource, /registeringProjectRef\.current = false/);
  assert.match(resetSource, /setRegisteringProject\(false\)/);
});

test("Ask routes through the durable project-bound agent stream", () => {
  assert.doesNotMatch(source, /streamPlainChat/);
  assert.doesNotMatch(source, /api\.chat\(/);
  assert.match(source, /streamAgentChat\([\s\S]*?runMode,[\s\S]*?runProjectId/);
  assert.match(source, /agent === "ask"[\s\S]*?chatStatsFromEvent/);
});

test("Clear starts a fresh thread instead of retaining hidden saved history", () => {
  const start = source.indexOf("const clear =");
  const end = source.indexOf("const projectMissing", start);
  assert.ok(start >= 0 && end > start);
  const clearSource = source.slice(start, end);
  assert.match(clearSource, /cancelSessionLoad\(\)/);
  assert.match(clearSource, /selectSession\(""\)/);
});

test("Stop keeps send blocked until the aborted stream settles", () => {
  assert.match(source, /runInFlightRef/);
  const start = source.indexOf("const invalidateActiveRun");
  const end = source.indexOf("const clearStagedContext", start);
  assert.ok(start >= 0 && end > start);
  assert.doesNotMatch(source.slice(start, end), /setStreaming\(false\)/);
  assert.match(source, /runInFlightRef\.current = false[\s\S]*?setStreaming\(false\)/);
});

test("settled canceled runs still refresh the durable Threads list", () => {
  const sendStart = source.indexOf("const send =");
  const streamStart = source.indexOf("const streamAgentChat", sendStart);
  assert.ok(sendStart >= 0 && streamStart > sendStart);
  const sendSource = source.slice(sendStart, streamStart);
  const finallyStart = sendSource.lastIndexOf("} finally {");
  assert.ok(finallyStart >= 0);
  const finallySource = sendSource.slice(finallyStart);
  assert.match(finallySource, /sessions\.reload\(\)/);
  assert.doesNotMatch(finallySource, /isActiveRun\(generation\)[\s\S]*?sessions\.reload\(\)/);
});

test("Ask preserves reasoning progress without exposing thinking text", () => {
  assert.match(source, /type === "thinking"/);
  assert.match(source, /replaceAssistant\(assistantIndex, "Thinking\.\.\.", generation, true\)/);
  assert.match(source, /durableTranscript\(system, messages\)/);
});
