# LAC Pro Delivery + Hardening — Design Spec

**Date:** 2026-07-05 · **Status:** Approved · **Relates to:** `2026-07-04-lac-pro-autopilot-design.md`, `2026-07-05-lac-pro-custom-model-import-design.md`

## 1. Why

Two real gaps, one build:

1. **There is no Pro delivery mechanism at all.** `lac-pro` is a private repo with no build/dist, editable-installed into the dev venv. Nothing in `site/`, `README.md`, or the handoff tells a paying customer how to *receive* the plugin. Today someone could pay via Polar and have no built way to get Pro. You cannot actually sell Pro without this.
2. **The Pro gate is trivially bypassable.** It's client-side readable Python: anyone technical edits the `require()`/`check()` call or sets `LAC_PRO_DEV=1` and unlocks everything.

These collapse into one thing: **you have to build Pro delivery anyway, so build it hardened from day one** — the delivery mechanism is exactly where casual-piracy hardening bakes in.

**Strategic framing (decided after a deliberate back-and-forth with Duan):** the *correct order* is cheap hardening + delivery now (necessary to sell Pro), and the server-side-value moat (crowd-benchmark database, cross-machine sync) + real accounts *later*, once there are users. Rationale: the moat is the only structurally un-pirateable value, but it's worthless with zero users (you can't "rank against thousands of rigs" with zero rigs), so it must follow traction, not precede it. Building a full account system pre-launch would pour effort into the least-valuable parts while being blocked on the best part — building the system backwards.

**Honest boundary, stated up front and never oversold:** none of this is *uncrackable*. Compiling raises the bar enormously against casual bypass and gating delivery means crackers don't even get a free copy to study — but a determined reverse-engineer can still attack a binary. Absolute protection only ever comes from the server-side moat (out of scope here, deferred to post-traction).

## 2. Decisions locked (do not re-litigate)

1. **Gated download of a COMPILED plugin.** Not bundle-and-unlock (that puts Pro bits on every free user's disk + in the public release, handing crackers a free copy). Not a readable pip install. The compiled artifact reaches validated license keys only.
2. **Free-tier infra only, for now** — a deliberate capital-constraint decision (Duan, pre-revenue). **Cloudflare Workers** (free tier, ~100k req/day) for the gate; **Cloudflare R2** (free tier, zero egress) for private artifact storage. Nuitka is free/OSS. The whole delivery system runs at $0 until there's revenue to fund the next layer.
3. **No accounts, no user database, no PII stored.** The gate is stateless: key in → validate against Polar → signed URL out, storing nothing. Stays consistent with the landing page's "no account needed" selling point.
4. **Nuitka compilation, spike-gated.** Nuitka compiles the plugin to a binary extension (no readable `.py`). If the spike (§7) shows Nuitka + entry-point discovery is impractical, the fallback is **PyArmor** obfuscation (weaker but simpler — keeps `.py`-shaped but obfuscated). The spike decides; do not build downstream on the assumption Nuitka works until Task 1 proves it.
5. **`lac-pro` never gets a public remote.** The compiled artifact is built from the private repo and stored in a private R2 bucket — never public, never listable.
6. **The install/bootstrap command lives in the OPEN-SOURCE core**, not the Pro plugin — because a free user who just bought a key doesn't have the plugin yet, so the thing that fetches it cannot be inside it. Core stays Pro-*logic*-unaware; it gains a generic "fetch-and-install-a-licensed-plugin" bootstrap only. This is a delivery mechanism, not Pro feature logic — it does not violate the open-core boundary.
7. **Dev path unchanged.** Editable install + `LAC_PRO_DEV=1` stays for Duan's development. For real users, gated delivery *neutralizes the backdoor by construction*: a free user never receives the Pro code, so there is no installed plugin for `LAC_PRO_DEV=1` to unlock. (The release/compiled build may also strip or ignore the dev override, but this is belt-and-suspenders — the delivery gate is the real control.)
8. **Delivery (once) and activation (ongoing) are separate.** Delivery = fetch + install the compiled plugin once. Activation = the installed plugin's existing `check()`/`require()` license validation against Polar, unchanged (`lac_pro/license.py`).

## 3. Architecture / components

**A. Compiled Pro artifact build** (`lac-pro` repo).
A build step (Nuitka) produces a distributable, installable artifact — a wheel or archive containing the compiled extension module(s) that STILL exposes the `lac.plugins` entry point (`pro = lac_pro.plugin:PLUGIN`) so the main app's `backend/plugins.py::discover()` finds it identically to the current editable install. The build runs from the private repo; its output is uploaded to R2 (private), versioned by plugin version.

**B. Private artifact storage** (Cloudflare R2, private bucket).
Holds the compiled artifact(s), keyed by version. Not publicly readable/listable. The Worker (C) mints short-lived presigned GET URLs for it; nothing else can reach it.

**C. License-gated download Worker** (Cloudflare Worker, free tier).
A stateless endpoint, e.g. `POST /pro/download {license_key, plugin_version?}`:
1. Validate `license_key` against Polar's customer-portal license-key validate API — the same endpoint `lac_pro/ls.py::validate` already uses, run server-side here (the Worker holds no Polar secret beyond what's needed for the public validate call; if a secret is required it lives in a Worker secret binding, never in the client).
2. On valid → return a short-lived (e.g. 5-min) presigned R2 URL for the current artifact.
3. On invalid/expired → 403 with a clear machine-readable reason.
Stores nothing, no DB, no logging of PII. Rate-limited (Workers' built-in limits + a simple per-key/IP throttle).

**D. Client install/bootstrap** (open-source core: `cli.py` + web UI + a new small `backend/` helper).
A core command `lac unlock <key>` (name TBD at plan time — could be `lac pro-install`) and a matching UI "Activate Pro" action:
1. POST the key to the Worker.
2. On success, download the artifact from the presigned URL.
3. Install it into the running Python environment / plugin path so `discover()` finds its entry point (mechanism: `pip install` the downloaded wheel into the app's environment, or drop it on a plugin search path — decided at plan time per what the packaged app allows).
4. Re-run discovery; confirm `lac pro status` now shows the plugin active.
Idempotent (re-running with an already-installed plugin is a no-op or a clean re-validate). Honest, specific failures (invalid key, network, download, install).

**E. Ongoing activation** — unchanged. Once installed, the Pro plugin's `check()`/`require()` validates the license against Polar exactly as today (`lac_pro/license.py`, the 3-day revalidate / 14-day grace contract). This spec does not touch it.

## 4. Data flow

Free user → buys on Polar → gets license key → runs `lac unlock <key>` (or clicks Activate Pro) → core POSTs key to the Worker → Worker validates against Polar → returns presigned R2 URL → core downloads the compiled plugin → installs it → entry-point discovery finds it → `lac pro status` active → all Pro features (tune, benchmark, autopilot, custom-model import) now work locally, gated ongoing by the existing license check.

## 5. Error handling (honest, specific — no generic failures)

- **Invalid/expired key** → Worker 403; client: "That license key isn't valid or has expired — check it or manage it in your Polar portal."
- **Network failure reaching the Worker** → client: clear retryable message.
- **Download failure from R2** → clean, retryable, no partial install left behind.
- **Install failure** (env/permissions) → specific message; the app is unchanged (Pro just isn't installed), never a crash.
- **Already installed** → idempotent re-validate, not an error.

## 6. Testing approach

- **Spike (Task 1, see §7)** — the gating technical risk; must pass before anything downstream is built.
- **Worker** — unit-test the validate→signed-URL logic with a mocked Polar validate call (valid → URL, invalid → 403); a real free-tier deploy smoke-tested against a real (test-mode) Polar key.
- **Client bootstrap** — test the fetch→install→discover flow with the Worker mocked; assert a mocked-successful download results in a discoverable entry point, and each of the §5 failure states surfaces its honest message without crashing the app.
- **End-to-end** — one real run: compiled artifact in R2 + deployed Worker + a real test-mode Polar key → `lac unlock <key>` → Pro active. (Same "run it against reality once" discipline that caught a real bug in the custom-model-import feature.)

## 7. Open technical risks — spike FIRST

1. **Nuitka + setuptools entry-point discovery (biggest).** Does a Nuitka-compiled `lac_pro` still register and get discovered via the `lac.plugins` entry point by `importlib.metadata.entry_points`? Task 1 must compile the real plugin, install the artifact into a clean environment, and confirm `discover()` finds it AND a Pro command runs from the compiled binary — before any Worker/bootstrap code is built on the assumption. Fallback: PyArmor (decision §2.4).
2. **Cloudflare R2 presigned URL from a Worker** — confirm the free-tier mechanism for a Worker to mint a short-lived GET URL (R2 binding + signed URL, or the S3-compatible presign) works as expected.
3. **Installing a wheel into the packaged (PyInstaller) app's environment** — the shipped LAC is a PyInstaller exe with a frozen environment; confirm the bootstrap can actually add a plugin to it (may need a plugin dir on the entry-point path rather than `pip install`). This shapes component D's install mechanism and should be pinned early.

## 8. Out of scope (explicitly)

- **User accounts, login pages, a hosted user database, a dashboard** — the "full system." Deferred to post-traction, and only if a concrete need appears that Polar's portal doesn't cover.
- **The server-side moat** (crowd-benchmark database, cross-machine sync) — the structurally-un-pirateable value; needs users first.
- **The `model-hub` → `lac` rename** (local dir + `backend` package) — confirmed separately, a distinct mechanical migration with its own spec/plan; blocks nothing here.
- **Lightweight leads-capture** (fixing the waitlist form) — a small, separate launch item that's mostly Duan's hosted-form account setup + a trivial code swap; not part of this delivery system.
- **Any change to the existing license `check()`/`require()` activation contract, the Autopilot/import Pro features themselves, or Polar pricing.**
