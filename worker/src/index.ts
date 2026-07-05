/**
 * LAC Pro delivery gate — a stateless Cloudflare Worker.
 *
 * Flow:  POST /pro/download  { "license_key": "<polar key>" }
 *   1. Validate the key against Polar's public customer-portal API.
 *   2. If Polar returns status "granted", stream the compiled Pro artifact
 *      from the private R2 bucket binding.
 *   3. Otherwise 403.
 *
 * Stateless by design: no KV, no D1, no Durable Objects, no logging that
 * contains the license key. Fails CLOSED — any doubt about validity → 403.
 *
 * This file lives in the OPEN-SOURCE repo and contains no secrets: the Polar
 * org id is public and already ships in the client; the artifact bytes live
 * in R2, never here.
 */

export interface Env {
  /** Private R2 bucket holding the compiled Pro artifact. */
  R2_BUCKET: R2Bucket;
  /** Public Polar.sh organization UUID (safe to commit). */
  POLAR_ORG_ID: string;
  /** R2 object key of the artifact to stream. */
  ARTIFACT_KEY: string;
  /** Filename offered to the client via Content-Disposition. */
  ARTIFACT_FILENAME: string;
}

const POLAR_VALIDATE_URL =
  "https://api.polar.sh/v1/customer-portal/license-keys/validate";

function jsonResponse(status: number, body: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...(headers ?? {}) },
  });
}

/**
 * Replicates lac-pro's Polar validate call. Returns true only when Polar
 * explicitly answers status "granted". Every other outcome — a different
 * status, a 4xx JSON body, a non-JSON WAF wall, or a transport failure —
 * returns false so the caller fails closed.
 *
 * The license key is passed straight to Polar and never logged.
 */
async function polarGranted(licenseKey: string, orgId: string): Promise<boolean> {
  let resp: Response;
  try {
    resp = await fetch(POLAR_VALIDATE_URL, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        // Polar sits behind Cloudflare's WAF, which 403s absent/default
        // User-Agents before the request ever reaches Polar's API. A real
        // User-Agent is REQUIRED (confirmed production gotcha, see lac_pro/ls.py).
        "User-Agent": "LAC-Pro-Gate/1.0",
      },
      body: JSON.stringify({ key: licenseKey, organization_id: orgId }),
    });
  } catch {
    // DNS / timeout / connection refused → fail closed.
    return false;
  }

  let data: unknown;
  try {
    data = await resp.json();
  } catch {
    // Non-JSON body (e.g. an HTML WAF wall) → treat as invalid.
    return false;
  }

  return (
    typeof data === "object" &&
    data !== null &&
    (data as { status?: unknown }).status === "granted"
  );
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    // Only POST is allowed.
    if (request.method !== "POST") {
      return jsonResponse(405, { error: "method_not_allowed" }, { Allow: "POST" });
    }

    // Parse the JSON body defensively.
    let payload: unknown;
    try {
      payload = await request.json();
    } catch {
      return jsonResponse(400, { error: "invalid_request" });
    }

    const licenseKey = (payload as { license_key?: unknown } | null)?.license_key;
    if (typeof licenseKey !== "string" || licenseKey.length === 0) {
      return jsonResponse(400, { error: "invalid_request" });
    }

    // Gate on Polar. Anything short of an explicit "granted" → 403.
    const granted = await polarGranted(licenseKey, env.POLAR_ORG_ID);
    if (!granted) {
      return jsonResponse(403, { error: "invalid_or_expired" });
    }

    // Valid key: stream the artifact straight from R2.
    const object = await env.R2_BUCKET.get(env.ARTIFACT_KEY);
    if (!object) {
      // Key is valid but the artifact is missing from the bucket — a
      // server-side misconfiguration, not the client's fault.
      return jsonResponse(503, { error: "artifact_unavailable" });
    }

    const filename = env.ARTIFACT_FILENAME || "lac-pro.zip";
    return new Response(object.body, {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename="${filename}"`,
      },
    });
  },
};
