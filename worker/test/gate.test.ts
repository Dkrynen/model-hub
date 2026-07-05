import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import worker, { type Env } from "../src/index";

// ── Fixtures ────────────────────────────────────────────────────────────────
const ORG_ID = "c3771fa4-19b0-4f29-a444-0aa52b0daf36";
const ARTIFACT_KEY = "lac-pro-latest.zip";
const FILENAME = "lac-pro.zip";
const VALIDATE_URL =
  "https://api.polar.sh/v1/customer-portal/license-keys/validate";

// Fake artifact bytes. Real ZIP magic up front so the octet-stream assertion
// is meaningful (this is binary, not text).
const ARTIFACT_BYTES = new Uint8Array([
  0x50, 0x4b, 0x03, 0x04, // "PK\x03\x04" ZIP local file header
  ...new TextEncoder().encode(" lac-pro compiled artifact payload"),
]);

// Faked R2 binding: a Map-backed store exposing the single `.get(key)` the
// Worker uses. Returns an object with a real ReadableStream `.body`, or null
// when the key is absent (so the "missing artifact" path is exercised).
function makeR2(store: Map<string, Uint8Array>) {
  return {
    async get(key: string) {
      const bytes = store.get(key);
      if (!bytes) return null;
      return { body: new Response(bytes).body as ReadableStream };
    },
  };
}

function makeEnv(store: Map<string, Uint8Array>): Env {
  return {
    R2_BUCKET: makeR2(store) as unknown as R2Bucket,
    POLAR_ORG_ID: ORG_ID,
    ARTIFACT_KEY,
    ARTIFACT_FILENAME: FILENAME,
  };
}

function seededEnv(): Env {
  return makeEnv(new Map([[ARTIFACT_KEY, ARTIFACT_BYTES]]));
}

const ctx = {
  waitUntil() {},
  passThroughOnException() {},
} as unknown as ExecutionContext;

function post(body: unknown, raw = false): Request {
  return new Request("https://gate.example/pro/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: raw ? (body as string) : JSON.stringify(body),
  });
}

// ── Polar `fetch` mock ───────────────────────────────────────────────────────
let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function polarReplies(bodyText: string, status = 200, contentType = "application/json") {
  fetchSpy.mockResolvedValue(
    new Response(bodyText, { status, headers: { "Content-Type": contentType } }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────
describe("LAC Pro gate — POST /pro/download", () => {
  it("valid key → 200 streaming the R2 artifact with correct headers", async () => {
    polarReplies(JSON.stringify({ status: "granted", id: "lk_123" }));

    const res = await worker.fetch(post({ license_key: "valid-key" }), seededEnv(), ctx);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/octet-stream");
    expect(res.headers.get("Content-Disposition") ?? "").toContain(FILENAME);

    const bytes = new Uint8Array(await res.arrayBuffer());
    expect(bytes).toEqual(ARTIFACT_BYTES);
  });

  it("replicates the exact Polar validate contract (URL, method, UA, body)", async () => {
    polarReplies(JSON.stringify({ status: "granted" }));

    await worker.fetch(post({ license_key: "abc-123" }), seededEnv(), ctx);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(VALIDATE_URL);
    expect(init.method).toBe("POST");

    const headers = new Headers(init.headers as HeadersInit);
    expect(headers.get("User-Agent")).toBe("LAC-Pro-Gate/1.0"); // WAF gotcha
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");

    expect(JSON.parse(init.body as string)).toEqual({
      key: "abc-123",
      organization_id: ORG_ID,
    });
  });

  it("not_granted status → 403 invalid_or_expired", async () => {
    polarReplies(JSON.stringify({ status: "not_granted" }));

    const res = await worker.fetch(post({ license_key: "revoked" }), seededEnv(), ctx);

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("Polar 4xx (unknown key) body → 403 invalid_or_expired", async () => {
    polarReplies(JSON.stringify({ detail: "License key not found" }), 404);

    const res = await worker.fetch(post({ license_key: "nope" }), seededEnv(), ctx);

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("malformed (non-JSON) Polar body → 403 (fail closed)", async () => {
    polarReplies("<html>WAF wall</html>", 200, "text/html");

    const res = await worker.fetch(post({ license_key: "weird" }), seededEnv(), ctx);

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("Polar network error → 403 (fail closed)", async () => {
    fetchSpy.mockRejectedValue(new Error("connection refused"));

    const res = await worker.fetch(post({ license_key: "any" }), seededEnv(), ctx);

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("missing license_key → 400, and Polar is never called", async () => {
    const res = await worker.fetch(post({ not_a_key: "x" }), seededEnv(), ctx);
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("non-string license_key → 400, and Polar is never called", async () => {
    const res = await worker.fetch(post({ license_key: 12345 }), seededEnv(), ctx);
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("non-JSON request body → 400", async () => {
    const res = await worker.fetch(post("this is not json{", true), seededEnv(), ctx);
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("GET (wrong method) → 405", async () => {
    const req = new Request("https://gate.example/pro/download", { method: "GET" });
    const res = await worker.fetch(req, seededEnv(), ctx);
    expect(res.status).toBe(405);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("granted but artifact missing in R2 → clean 5xx, not a crash", async () => {
    polarReplies(JSON.stringify({ status: "granted" }));

    // Empty store: the object was never put().
    const res = await worker.fetch(
      post({ license_key: "valid-key" }),
      makeEnv(new Map()),
      ctx,
    );

    expect(res.status).toBeGreaterThanOrEqual(500);
    expect(res.status).toBeLessThan(600);
  });

  it("never logs the license key", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchSpy.mockRejectedValue(new Error("boom")); // force the error path too

    await worker.fetch(post({ license_key: "SECRET-KEY-123" }), seededEnv(), ctx);

    const logged = [errSpy, logSpy, warnSpy]
      .flatMap((s) => s.mock.calls.flat())
      .map((a) => String(a))
      .join(" ");
    expect(logged).not.toContain("SECRET-KEY-123");

    errSpy.mockRestore();
    logSpy.mockRestore();
    warnSpy.mockRestore();
  });
});
