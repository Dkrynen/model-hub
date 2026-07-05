# LAC Pro delivery gate (Cloudflare Worker)

A tiny, **stateless** Cloudflare Worker that gates downloads of the compiled
LAC **Pro** plugin. It validates a [Polar](https://polar.sh) license key, and
on success streams the Pro artifact straight from a private R2 bucket.

- **No state.** No KV, no D1, no Durable Objects, no database.
- **No PII / no secrets in this repo.** The Polar org id is public (it already
  ships in the LAC client); the artifact bytes live in R2, not here. The license
  key is passed to Polar and **never logged**.
- **Fails closed.** Any doubt — non-`granted` status, a 4xx body, a non-JSON WAF
  wall, or a network error — returns `403`.
- **Free-tier compatible.** Nothing here uses a paid Cloudflare feature.

## Endpoint

```
POST /pro/download
Content-Type: application/json

{ "license_key": "<polar license key>" }
```

| Condition | Response |
|---|---|
| Polar returns `status: "granted"` | `200`, artifact bytes streamed from R2 (`Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename="…"`) |
| Any other Polar outcome (other status, 4xx body, non-JSON, network error) | `403 {"error":"invalid_or_expired"}` |
| Missing / non-string `license_key` | `400 {"error":"invalid_request"}` |
| Non-POST method | `405 {"error":"method_not_allowed"}` (with `Allow: POST`) |
| Key valid but artifact missing from R2 | `503 {"error":"artifact_unavailable"}` |

The Polar call this Worker replicates:
`POST https://api.polar.sh/v1/customer-portal/license-keys/validate` with body
`{key, organization_id}` and a **real `User-Agent`** (`LAC-Pro-Gate/1.0`). The
User-Agent is required — Polar sits behind Cloudflare's WAF, which `403`s
absent/default User-Agents before the request reaches the API. (Same gotcha as
`lac-pro/lac_pro/ls.py`.)

## Configuration (`wrangler.toml`)

| Key | Meaning |
|---|---|
| `[vars] POLAR_ORG_ID` | Public Polar org UUID. |
| `[vars] ARTIFACT_KEY` | R2 object key to stream (placeholder `lac-pro-latest.zip`; the real key is chosen at upload time). |
| `[vars] ARTIFACT_FILENAME` | Filename offered to the client in `Content-Disposition`. |
| `[[r2_buckets]] binding = "R2_BUCKET"` | The private artifact bucket. Emulated locally in tests. |

## Testing (local, no Cloudflare account needed)

```bash
cd worker
npm install
npm test
```

Tests run under plain **Vitest** with a thin handler harness: the Worker's
exported `fetch` is invoked directly with a **mocked global `fetch`** (Polar)
and a **faked R2 binding** (a `Map`-backed store returning a real
`ReadableStream` body). This exercises the real request → validate → stream
logic without needing `workerd`.

> **Toolchain note.** The plan preferred `@cloudflare/vitest-pool-workers`
> (real `workerd` + Miniflare-emulated R2). Its current npm release
> (`0.18.0`) ships a broken package `exports` map — the documented
> `@cloudflare/vitest-pool-workers/config` entrypoint isn't exported, so the
> vitest config can't load under `vitest@4`. The thin-harness fallback above
> meets the same bar (mocked Polar + emulated R2, real streaming) with zero
> native/`workerd` dependencies, which is also friendlier for an open-source
> repo and Windows contributors.

## Deploy (Duan-gated — needs the Cloudflare account)

Not part of the build task; run these when you're ready to go live. Requires
`wrangler` (used on-demand via `npx`, so nothing extra is committed).

1. **Authenticate** to the Acend Cloudflare account:
   ```bash
   npx wrangler login
   ```
2. **Create the private R2 bucket** (name must match `bucket_name` in
   `wrangler.toml`, or edit it to taste):
   ```bash
   npx wrangler r2 bucket create lac-pro-artifacts
   ```
3. **Upload the compiled artifact** and set `ARTIFACT_KEY` in `wrangler.toml`
   to the object key you used (the built file is ABI-tagged, e.g.
   `lac-pro-0.1.0-cp311-win_amd64.zip`, but the R2 key is yours to choose):
   ```bash
   npx wrangler r2 object put lac-pro-artifacts/lac-pro-latest.zip \
     --file ../../lac-pro/dist/lac-pro-0.1.0-cp311-win_amd64.zip
   ```
4. **Confirm the vars** in `wrangler.toml` (`POLAR_ORG_ID`, `ARTIFACT_KEY`,
   `ARTIFACT_FILENAME`). None are secret, so no `wrangler secret` is needed.
5. **Deploy:**
   ```bash
   npm run deploy        # → npx wrangler deploy
   ```
6. **Smoke test** with a real license key:
   ```bash
   curl -sS -X POST https://<your-worker-subdomain>/pro/download \
     -H 'Content-Type: application/json' \
     -d '{"license_key":"<real key>"}' -o lac-pro.zip -w '%{http_code}\n'
   ```

To roll a new artifact, `r2 object put` the new bytes (same key, or bump
`ARTIFACT_KEY` and re-deploy). No code change needed.
