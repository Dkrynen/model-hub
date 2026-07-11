import assert from "node:assert/strict";
import test from "node:test";

import { api, ApiError } from "../src/lib/api.ts";

const PINNED_IMAGE = `example/lac@sha256:${"a".repeat(64)}`;

test("agentSandbox requests status for the exact encoded project root", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl: string | URL | Request | undefined;
  globalThis.fetch = (async (url) => {
    capturedUrl = url;
    return new Response(JSON.stringify({
      backend: "docker",
      available: false,
      code: "daemon_unavailable",
      message: "Docker is unavailable.",
      tasks: [],
      network: "none",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await api.agentSandbox("C:\\work\\repo & one");
    assert.equal(capturedUrl, "/api/agent/sandbox?cwd=C%3A%5Cwork%5Crepo%20%26%20one");
    assert.equal(result.code, "daemon_unavailable");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("agentSandbox fails closed on malformed or contradictory readiness responses", async (t) => {
  const originalFetch = globalThis.fetch;
  const validReady = {
    backend: "docker",
    available: true,
    code: "ready",
    message: "Task sandbox ready.",
    tasks: ["test"],
    image: PINNED_IMAGE,
    network: "none",
  };
  const invalidResponses = {
    wrong_backend: { ...validReady, backend: "host" },
    network_enabled: { ...validReady, network: "bridge" },
    contradictory_code: { ...validReady, code: "docker_daemon_unavailable" },
    unavailable_ready_code: { ...validReady, available: false, tasks: [], image: null },
    empty_tasks: { ...validReady, tasks: [] },
    invalid_tasks: { ...validReady, tasks: ["../test"] },
    duplicate_tasks: { ...validReady, tasks: ["test", "test"] },
    unpinned_image: { ...validReady, image: "python:latest" },
    unexpected_field: { ...validReady, host_mount: "C:\\" },
    missing_image: (({ image: _image, ...rest }) => rest)(validReady),
    missing_tasks: (({ tasks: _tasks, ...rest }) => rest)(validReady),
  };

  try {
    for (const [name, body] of Object.entries(invalidResponses)) {
      await t.test(name, async () => {
        globalThis.fetch = (async () =>
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })) as typeof fetch;
        await assert.rejects(
          () => api.agentSandbox("C:\\work\\repo"),
          /Invalid agent sandbox status response/
        );
      });
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("agentSandbox accepts a consistent ready response and a bounded unavailable response", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    {
      backend: "docker",
      available: true,
      code: "ready",
      message: "Task sandbox ready.",
      tasks: ["test"],
      image: PINNED_IMAGE,
      network: "none",
    },
    {
      backend: "docker",
      available: false,
      code: "sandbox_unconfigured",
      message: "No sandbox is configured.",
      tasks: [],
      image: null,
      network: "none",
    },
  ];
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(responses.shift()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;

  try {
    assert.equal((await api.agentSandbox("C:\\work\\repo")).available, true);
    assert.equal((await api.agentSandbox("C:\\work\\repo")).available, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("cancelAgentRun encodes the exact run and sends only its capability", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl: string | URL | Request | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    assert.deepEqual(await api.cancelAgentRun("run/one ?#", "capability-token"), { ok: true });
    assert.equal(capturedUrl, "/api/agent/runs/run%2Fone%20%3F%23/cancel");
    assert.equal(capturedInit?.method, "POST");
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      approval_token: "capability-token",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("answerApproval encodes the run id and sends the exact capability body", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl: string | URL | Request | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await api.answerApproval(
      "run/one ?#",
      "capability-token",
      { ask_id: "ask-1", decision: "allow", remember: true }
    );

    assert.deepEqual(result, { ok: true });
    assert.equal(capturedUrl, "/api/agent/runs/run%2Fone%20%3F%23/answer");
    assert.equal(capturedInit?.method, "POST");
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      ask_id: "ask-1",
      approval_token: "capability-token",
      decision: "allow",
      remember: true,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

for (const status of [404, 409, 410, 504]) {
  test(`answerApproval preserves ApiError status ${status}`, async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: `failure-${status}` }), {
        status,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;

    try {
      await assert.rejects(
        () =>
          api.answerApproval("run-1", "capability-token", {
            ask_id: "ask-1",
            decision: "deny",
            remember: false,
          }),
        (error: unknown) => {
          assert.ok(error instanceof ApiError);
          assert.equal(error.status, status);
          assert.deepEqual(error.body, { error: `failure-${status}` });
          return true;
        }
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
}
