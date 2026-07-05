# LAC Pro Delivery + Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing LAC Pro delivery pipeline — a hardened, compiled Pro plugin handed out only to validated Polar license keys via a free-tier Cloudflare gate — so Pro can actually be sold, and isn't trivially free to anyone who edits a Python file.

**Architecture:** The private `lac-pro` plugin is compiled to a non-readable artifact (Nuitka, spike-gated; PyArmor fallback) and stored in a private Cloudflare R2 bucket. A stateless free-tier Cloudflare Worker validates a submitted Polar license key against Polar's public customer-portal API and, only on `granted`, streams the artifact back from its R2 binding. A bootstrap command in the **open-source core** (`lac unlock <key>` + a web "Activate Pro" action) fetches and installs it; the plugin is then discovered via the existing `lac.plugins` entry-point seam, and its existing license `check()` gates ongoing use. Delivery (once) and activation (ongoing, unchanged) are separate.

**Tech Stack:** Python (both repos) · Nuitka *or* PyArmor (spike decides) · Cloudflare Workers + R2 (JavaScript/TypeScript + `wrangler` + `vitest`/`miniflare`, a new non-Python surface) · the existing Polar customer-portal license-key API.

## Global Constraints

- Execution: subagent-driven-development — fresh implementer + fresh reviewer per task. Every subagent dispatch must say **"work in the foreground, do NOT spawn agents."**
- TDD per task (except Task 1, a spike): failing test first, confirm it fails for the stated reason, implement, confirm pass.
- Commits land on `master` in **both** repos, per task. **Never push to origin** without Duan's separate explicit go-ahead each time.
- **`lac-pro` NEVER gets a public remote.** Its compiled artifact is stored **privately** (private R2 bucket), never public, never in the open-source release.
- lac-pro's Python interpreter for all commands is model-hub's venv: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe`, run from `C:\Users\User\repos\lac-pro`.
- **Core stays Pro-LOGIC-unaware.** The bootstrap in core is *generic licensed-plugin delivery* (fetch bytes from a configured URL given a key, install them), containing zero tuning/benchmark/import/license *logic*. It must not import `lac_pro`.
- **No accounts, no database, no PII stored.** The Worker is stateless: validate → stream → store nothing.
- **Free-tier infra only.** Cloudflare Workers + R2 free tiers. No paid infra.
- Untouched / out of scope: the existing `check()`/`require()` activation contract (`lac_pro/license.py`), the Autopilot & custom-model-import Pro features, Polar pricing, the `model-hub`→`lac` rename, and the leads-capture form.
- **Duan-gated:** anything needing Duan's Cloudflare account (creating the R2 bucket, `wrangler deploy`, uploading the artifact, live end-to-end) is NOT a subagent task — it's the gated section at the end. Everything buildable/testable with mocks + local emulation gets built and reviewed without the live account.
- Full spec: `docs/superpowers/specs/2026-07-05-lac-pro-delivery-and-hardening-design.md` — read it before Task 1.

---

### Task 1: Spike — compiled plugin + entry-point discovery (resolves the hardening approach)

The flagged top risk (spec §7.1 + §7.3). Everything downstream assumes a *compiled/obfuscated* `lac-pro` can still be discovered via `importlib.metadata.entry_points(group="lac.plugins")` and run. Prove it — or fall back — before building the Worker or bootstrap. **This is a spike, not a TDD task:** the deliverable is a documented, reproducible working recipe (or a fallback decision), committed as notes + a build script stub.

**Files:**
- Create: `C:\Users\User\repos\lac-pro\build\SPIKE-NOTES.md` (findings), and whatever throwaway build scratch the spike needs (clean up scratch, keep the notes).

**The spike must answer, hands-on (not from docs):**

1. **Nuitka path first.** Install Nuitka into model-hub's venv (`...\.venv\Scripts\python.exe -m pip install nuitka` — note if it needs a C compiler and whether one is present; record the real state). Compile `lac_pro` (module or standalone) to a non-readable artifact. The hard part: an artifact that `importlib.metadata.entry_points(group="lac.plugins")` still finds as `pro = lac_pro.plugin:PLUGIN`. Entry-point discovery reads `*.dist-info/entry_points.txt` metadata, NOT the source — so the artifact must ship the compiled module **plus** its `.dist-info` (with `entry_points.txt`) into a location on `sys.path`. Determine the exact packaging: does a Nuitka-compiled `lac_pro` + its `lac_pro-0.1.0.dist-info` dropped onto a path get discovered? Confirm end to end:
   ```
   # in a CLEAN venv (python -m venv a throwaway dir), with the core installed but lac-pro NOT pip-installed:
   # drop the compiled artifact + dist-info onto sys.path, then:
   python -c "from importlib.metadata import entry_points; eps=entry_points(group='lac.plugins'); print([e.name for e in eps]); p=[e for e in eps if e.name=='pro'][0].load(); print(type(p), p.name, p.version)"
   # then confirm a Pro command actually runs from the compiled code, e.g. `lac pro status` with LAC_PRO_DEV=1
   ```
   Confirm the shipped module is genuinely NOT readable Python (no recoverable `.py`).
2. **If Nuitka + entry-point discovery is impractical** (compiler missing, discovery won't work with a compiled module, standalone mode too heavy), evaluate the **PyArmor fallback**: PyArmor obfuscates but keeps the normal package + `.dist-info` structure, so entry-point discovery and a normal wheel install work unchanged — a much smaller integration risk, weaker protection. Confirm PyArmor obfuscates `lac_pro`, the obfuscated package still loads + registers its entry point, and the source isn't trivially readable.
3. **Install-into-frozen-app reality (risk §7.3).** The shipped LAC is a PyInstaller exe with a frozen environment (no `pip`). So the runtime install mechanism is likely "drop the artifact + dist-info into a plugin directory that core adds to `sys.path` before calling `discover()`," NOT `pip install`. Determine the concrete mechanism the bootstrap (Task 4) will use: a `~/.lac/plugins/` (or `~/.model-hub/plugins/`) directory that core prepends to `sys.path`, into which the artifact unpacks. Verify `entry_points` discovers a package from such a directory. (Full frozen-exe verification is part of the Duan-gated E2E; the dev-venv proof is the Task-1 bar.)

**Deliverable (`build/SPIKE-NOTES.md`):** which approach won (Nuitka or PyArmor) with the exact reproducible commands that worked; the exact artifact layout (what files, what directory structure, where `.dist-info` goes); the exact runtime install location + how core will put it on `sys.path`; and any gotchas. This recipe is consumed verbatim by Tasks 2 and 4 — they do NOT re-derive it.

- [ ] **Step 1: Run the Nuitka path hands-on**, recording real output (compiler present? discovery works?).
- [ ] **Step 2: If it fails/impractical, run the PyArmor fallback hands-on.**
- [ ] **Step 3: Resolve the runtime install location + sys.path mechanism** for a frozen app (the plugin-dir approach).
- [ ] **Step 4: Write `build/SPIKE-NOTES.md`** — the reproducible winning recipe + artifact layout + install location, or a BLOCKED report if neither approach works (escalate to Duan; do not fake a green).
- [ ] **Step 5: Commit** (from `C:\Users\User\repos\lac-pro`):
  ```bash
  git add build/SPIKE-NOTES.md
  git commit -m "spike: compiled/obfuscated lac-pro + entry-point discovery — resolve the delivery-hardening approach"
  ```

**Controller note:** review Task 1 by reading SPIKE-NOTES.md + independently reproducing the winning recipe's discovery check. If the spike lands on PyArmor instead of Nuitka, that's a legitimate finding — Tasks 2/4 adapt to whichever won; do not force Nuitka.

---

### Task 2: Reproducible compiled-artifact build script (lac-pro)

Consumes Task 1's recipe. A one-command, deterministic build that turns the current `lac_pro` source into the distributable hardened artifact for upload to R2.

**Files:**
- Create: `C:\Users\User\repos\lac-pro\build\build_artifact.py` (the build script), `C:\Users\User\repos\lac-pro\tests\test_build_artifact.py`
- Reference: `build/SPIKE-NOTES.md` (Task 1's recipe — the source of truth for the exact build commands + layout)

**Interfaces:**
- Produces: `build_artifact.py` writes the hardened, installable artifact to `build/dist/lac-pro-<version>.<ext>` (exact form per Task 1: e.g. a zip of the compiled module + `.dist-info`), and prints the output path + a SHA256. Later tasks/the Worker consume this file as opaque bytes.

- [ ] **Step 1: Write the failing test** (`tests/test_build_artifact.py`): a test that runs the build (or a fast subset) into a `tmp_path`, then installs the produced artifact into a fresh throwaway venv / onto a temp `sys.path` per Task 1's mechanism, and asserts `importlib.metadata.entry_points(group="lac.plugins")` finds `pro` and `.load()` returns the plugin object with `.name == "pro"`. (Mark it `slow`/`live` if the full Nuitka build is heavy — register the marker in `pyproject.toml` like the existing `live` marker; a heavy build test that runs on demand is acceptable, but it MUST really build + really discover, not stub.)
- [ ] **Step 2: Run it, confirm it fails** (`build_artifact.py` doesn't exist).
- [ ] **Step 3: Implement `build/build_artifact.py`** using Task 1's exact recipe — compile/obfuscate `lac_pro`, assemble the artifact + `.dist-info` in the exact layout Task 1 proved discoverable, write to `build/dist/`, print path + SHA256. Deterministic (same source → same layout). Confirm the artifact contains NO readable `lac_pro` `.py` source (add an assertion in the build or test that greps the artifact for a known source string and fails if found).
- [ ] **Step 4: Run the test, confirm it passes** (artifact builds + is discoverable + source not readable).
- [ ] **Step 5: Commit** (lac-pro):
  ```bash
  git add build/build_artifact.py tests/test_build_artifact.py pyproject.toml
  git commit -m "feat(build): reproducible hardened lac-pro artifact build (per spike recipe); verifies entry-point discovery + no readable source"
  ```

---

### Task 3: Cloudflare Worker gate (worker/, JS/TS)

A stateless free-tier Worker: validate a Polar key → on `granted`, stream the artifact from its R2 binding → else 403. New non-Python surface. Built + unit-tested locally with a mocked Polar `fetch` and emulated R2; NOT deployed (Duan-gated).

**Files:**
- Create: `C:\Users\User\repos\model-hub\worker\` — `worker/src/index.ts`, `worker/wrangler.toml`, `worker/package.json`, `worker/test/gate.test.ts`, `worker/tsconfig.json`, `worker/README.md`
- Note: this dir lives in the OPEN-SOURCE model-hub repo, but it contains ZERO Pro logic and ZERO secrets (the Polar org id is public; the artifact lives in R2, not here). It's just the gate. That's fine for a public repo. Add `worker/node_modules` to `.gitignore`.

**Interfaces:**
- Produces: `POST /pro/download` with JSON `{ "license_key": string }` → `200` streaming the artifact bytes (`Content-Type: application/octet-stream`, `Content-Disposition` filename) when Polar returns `status: "granted"`; `403 {"error":"invalid_or_expired"}` otherwise; `400` on a missing/non-string `license_key`; `405` on non-POST.

- [ ] **Step 1: Scaffold + write the failing tests** (`worker/test/gate.test.ts`, using `vitest` + `@cloudflare/vitest-pool-workers` or `miniflare`):
  - a valid key (mock the Polar `fetch` to return `{status:"granted", ...}`) → `200`, body equals the (mock) R2 object's bytes, correct headers.
  - an invalid key (mock Polar → `{status:"not_granted"}` or a 404 body) → `403` with `{"error":"invalid_or_expired"}`.
  - missing/non-string `license_key` → `400`.
  - a GET (wrong method) → `405`.
  Set up `wrangler.toml` with an R2 binding (e.g. `R2_BUCKET`, bound to a test bucket name) and the public `POLAR_ORG_ID` as a `[vars]` value.
- [ ] **Step 2: Run tests, confirm they fail** (no `index.ts` yet): `cd worker && npm install && npm test`.
- [ ] **Step 3: Implement `worker/src/index.ts`.** Replicate lac-pro's Polar validate call in TS — `POST https://api.polar.sh/v1/customer-portal/license-keys/validate` with body `{key: license_key, organization_id: POLAR_ORG_ID}` and headers `{Accept, Content-Type: application/json, User-Agent: "LAC-Pro-Gate/1.0"}` (a real User-Agent is REQUIRED — Polar's Cloudflare WAF 403s the default; this is a confirmed gotcha from `lac_pro/ls.py`). Treat `status === "granted"` as valid (anything else, including a 4xx body, is invalid → 403). On valid: `const obj = await env.R2_BUCKET.get(ARTIFACT_KEY); return new Response(obj.body, {headers:{...}})`. Stateless, log nothing containing the key. Guard method/body shape (400/405). Keep the artifact key configurable via a `[vars]` value (e.g. `ARTIFACT_KEY = "lac-pro-latest.zip"`).
- [ ] **Step 4: Run tests, confirm pass.**
- [ ] **Step 5: Commit** (model-hub):
  ```bash
  git add worker/ .gitignore
  git commit -m "feat(worker): stateless Cloudflare gate — validate Polar key, stream Pro artifact from R2 (free-tier, no state/PII)"
  ```

---

### Task 4: Core bootstrap install command (`lac unlock <key>`) + backend helper

The open-source-core bootstrap. Fetch the artifact from the configured gate URL given a key, install it into the plugin dir Task 1 identified, verify discovery. Honest per-failure-state handling. Pro-LOGIC-unaware (no `lac_pro` import).

**Files:**
- Create: `C:\Users\User\repos\model-hub\backend\pro_install.py` (the generic licensed-plugin fetch+install helper), `C:\Users\User\repos\model-hub\tests\test_pro_install.py`
- Modify: `cli.py` (add `cmd_unlock` + register the `unlock` subparser around line 1064's `sub.add_parser(...)` block), `backend/plugins.py` (prepend the plugin dir Task 1 chose — e.g. `~/.model-hub/plugins/` — to `sys.path`/the discovery path before `discover()`, so a bootstrap-installed artifact is found)

**Interfaces:**
- Consumes: the gate URL (a module constant, e.g. `PRO_GATE_URL = "https://<worker-subdomain>.workers.dev/pro/download"` — a placeholder value committed now, finalized at the Duan-gated deploy; make it overridable via a `LAC_PRO_GATE_URL` env var for testing).
- Produces: `install_pro_plugin(license_key: str, *, gate_url: str | None = None, http_post=None) -> dict` returning `{"state": "installed"}` or `{"state": "failed", "error_type": ..., "message": ...}` (honest states: `invalid_key` (gate 403), `network`, `download`, `install`); `cmd_unlock(args)` prints the outcome + exit code.

- [ ] **Step 1: Write the failing tests** (`tests/test_pro_install.py`): with a mocked `http_post` (a fake gate) — a `granted`/200 returning fake artifact bytes → installs into a `tmp_path` plugin dir + returns `{"state":"installed"}`; a 403 → `{"state":"failed","error_type":"invalid_key"}` naming the honest message; a network error → `network`; a corrupt/undownloadable body → `download`. Plus a `cmd_unlock` test: invalid key → exit 1 with the honest message on stderr; success → exit 0 printing where it installed + "restart LAC to use Pro" (or that it's live). Isolate the plugin dir via a patched constant, never touch a real `~/.model-hub/plugins`.
- [ ] **Step 2: Run, confirm fail** (`backend.pro_install` doesn't exist).
- [ ] **Step 3: Implement `backend/pro_install.py`** — `install_pro_plugin` POSTs `{license_key}` to `gate_url`, on 200 writes the streamed bytes to the plugin dir + unpacks to the exact layout Task 1 requires for discovery, on 403 returns `invalid_key`, wraps network/download failures as their honest states (never raises to the caller; the CLI decides exit codes). Then `cmd_unlock` in `cli.py` (gated on nothing — a free user runs it) + register `sub.add_parser("unlock", help="Activate LAC Pro with your license key")` with a `key` positional and `set_defaults(func=cmd_unlock)` matching the file's existing dispatch pattern. Modify `backend/plugins.py::discover()` (or its caller) to prepend the plugin dir to `sys.path` so a bootstrap-installed artifact is discoverable (guard: dir may not exist — skip cleanly). Confirm `backend/pro_install.py` does NOT import `lac_pro`.
- [ ] **Step 4: Run tests, confirm pass; run the full model-hub suite** (`.venv\Scripts\python.exe -m pytest -q -m "not live"`) — no regressions.
- [ ] **Step 5: Commit** (model-hub):
  ```bash
  git add backend/pro_install.py backend/plugins.py cli.py tests/test_pro_install.py
  git commit -m "feat(core): lac unlock <key> — generic licensed-plugin bootstrap (fetch from gate, install, discover); honest failure states; core stays Pro-logic-unaware"
  ```

---

### Task 5: Web UI "Activate Pro" action

A small web surface mirroring `lac unlock`: enter key → a core API route runs `install_pro_plugin` → show honest result.

**Files:**
- Modify: `backend/api.py` (add `POST /api/pro/unlock` calling `install_pro_plugin`), `web/src/lib/api.ts` (an `unlockPro(key)` method), and a settings/UI surface (`web/src/pages/settings.tsx` or the closest existing page) with a key field + "Activate Pro" button + result toast.
- Test: `tests/test_api.py` (append — the route: mocked `install_pro_plugin`, assert 200 on installed, a JSON error shape on failed); web verified via `npm run typecheck && npm run build`.

**Interfaces:**
- Consumes: `install_pro_plugin` (Task 4).
- Produces: `POST /api/pro/unlock {key}` → `{"state":"installed"}` or `{"state":"failed", "error_type", "message"}` (200 with the state in the body; the frontend branches on `state`).

- [ ] **Step 1: Write the failing test** (`tests/test_api.py`, append): mock `install_pro_plugin` → `{"state":"installed"}`, POST `/api/pro/unlock {"key":"x"}` → 200 + that body; mock a `{"state":"failed",...}` → 200 + the honest failure body; non-string/missing key → 400 (reuse the `get_json(silent=True)`+isinstance guard pattern already in api.py).
- [ ] **Step 2: Run, confirm fail** (route 404).
- [ ] **Step 3: Implement** the route in `backend/api.py`, the `unlockPro` client method in `api.ts`, and the settings UI (one field + button + toast surfacing `message` on failure, "Pro activated — restart LAC" on success). Match existing api.py guard + existing UI card/toast patterns.
- [ ] **Step 4: Verify** — `.venv\Scripts\python.exe -m pytest -q tests/test_api.py`, then `cd web && npm run typecheck && npm run build` (both exit 0, bare/unpiped).
- [ ] **Step 5: Commit** (model-hub):
  ```bash
  git add backend/api.py web/src/lib/api.ts web/src/pages/settings.tsx tests/test_api.py
  git commit -m "feat(web): Activate Pro — enter license key, bootstrap-install the plugin, honest result"
  ```

---

### Task 6: Config + customer-facing docs

Finalize the configurable bits and document how a customer actually activates Pro.

**Files:**
- Modify: `backend/pro_install.py` (confirm `PRO_GATE_URL` constant + `LAC_PRO_GATE_URL` override are the single source of the gate URL), `worker/README.md` (deploy steps), `README.md` / `site/index.html` (a short "After you buy: run `lac unlock <key>` or click Activate Pro" — replacing any implication Pro is already installed), and `docs/` a `PRO-DELIVERY.md` operator doc (build artifact → upload to R2 → deploy Worker → the whole chain).
- Test: none new (docs + a constant); `grep` the repo to confirm no other hardcoded gate URL exists.

- [ ] **Step 1:** Ensure exactly one gate-URL source; grep to confirm.
- [ ] **Step 2:** Write `docs/PRO-DELIVERY.md` (the operator runbook) + the customer-facing "how to activate" copy in README/site.
- [ ] **Step 3:** Commit (model-hub):
  ```bash
  git add backend/pro_install.py worker/README.md README.md site/index.html docs/PRO-DELIVERY.md
  git commit -m "docs: LAC Pro activation (customer) + delivery runbook (operator); single gate-URL source"
  ```

---

## Duan-Gated Live Steps (NOT subagent tasks — require Duan's Cloudflare account)

Do these manually, in order, after the code tasks are merged. None can be done by a subagent.

1. **Create the Cloudflare account** (free) + an **R2 bucket** (private) for the Pro artifact. Note the bucket name.
2. **Build + upload the artifact:** run `build/build_artifact.py` (Task 2) → upload the output to R2 under the `ARTIFACT_KEY` the Worker expects (`wrangler r2 object put` or the dashboard).
3. **Deploy the Worker:** set `worker/wrangler.toml`'s R2 binding to the real bucket + `POLAR_ORG_ID` var, `cd worker && npx wrangler deploy`. Note the deployed `*.workers.dev` URL.
4. **Wire the client:** put the real Worker URL into `PRO_GATE_URL` (Task 4/6), rebuild the app.
5. **Real end-to-end** (the "run it against reality once" gate): with a real **test-mode Polar license key**, run `lac unlock <key>` on a clean machine/env (ideally the packaged exe) → confirm the plugin downloads, installs, is discovered (`lac pro status` active), and a Pro command works — AND confirm an **invalid** key is cleanly refused (403 → honest message). Confirm a free user (no key) has no Pro code on disk.
6. **Push** both repos to their origins **only** on Duan's explicit go (model-hub → GitHub; lac-pro stays remote-less — its artifact lives in R2, not a git remote).

---

## Self-Review notes

- **Spec coverage:** §3.A→Task 2; §3.B (R2)→Duan-gated step 1 + Task 3's binding; §3.C (Worker)→Task 3; §3.D (bootstrap)→Task 4 + Task 5; §3.E (activation unchanged)→untouched, confirmed; §4 flow→Tasks 4/5 + gated E2E; §5 error states→Tasks 4/5; §7 risks→Task 1 (risks 1&3) + Task 3 (risk 2, dissolved by streaming from the R2 binding instead of presigning); §2 decisions→Global Constraints + tasks. §2.7 backdoor-neutralized-by-delivery holds (free users get no artifact). No spec section unmapped.
- **Spike-gated honesty:** Tasks 2 and 4 consume Task 1's documented recipe rather than pre-baking a Nuitka-specific approach; if the spike lands on PyArmor, those tasks use that instead — the interfaces (a discoverable installable artifact; a plugin dir on the discovery path) are approach-agnostic.
- **Placeholder check:** the one deliberately-unfinalized value is the gate URL (`PRO_GATE_URL`), which cannot exist until the Duan-gated Worker deploy — it's committed as an env-overridable placeholder and finalized in the gated section, not hand-waved.
