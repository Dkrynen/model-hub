# Design — LAC Pro: Self-Serve Activation & Visible Pro (S2)

**Created:** 2026-07-06 · **Status:** approved for planning · **Repos:** `model-hub` (open core, `Dkrynen/lac`) + `lac-pro` (private, no remote)
**Predecessor context:** `docs/superpowers/HANDOFF-lac-desktop-shell-and-pro-expansion.md`

> Slice **2 of 4** in the "make LAC a product people feel is worth paying for" effort.
> Sequence (locked): S1 shell ✅ → **S2 Pro self-serve/visible** → S3 Pro cockpit (felt value) → S4 chat latency.
> S2 makes Pro **self-serve** (a GUI buyer can activate without the CLI), **visible** (you can see you have Pro),
> and **celebrated** (an activation moment) — safely, with **no manual restart** and **zero risk to the running app**.

## 1. Problem

The shipped web "Activate Pro" flow (`POST /api/pro/unlock`) only **installs the compiled `.pyd`** — it
**never writes the license grant**. The grant is written solely by `lac pro activate <key>` (CLI, in
`lac-pro`), which GUI buyers cannot run. The plugin also only mounts its routes/hooks at **process
startup** (entry-point discovery over `sys.path`). Consequences:

1. **GUI buyers cannot self-license.** They pay, click Activate, the `.pyd` lands — but no grant is
   written, so Pro never actually turns on.
2. **No way to see you have Pro.** The Settings card always shows the "Activate Pro" input, even when
   licensed. There is no Pro status surface anywhere.
3. **No activation moment.** Nothing marks the transition to Pro.
4. **Nothing loads without a restart.**

## 2. Architecture decision — safe "no-restart feel" via clean self-relaunch (Option A)

Researched (`researcher` pass, 2026-07-06; sources in that report). The finding drove the choice:

- **In-process hot-load of the `.pyd` is unsafe and rejected.** CPython cannot unload a native extension
  once imported (loaded for process lifetime); a bad Pro build **segfaults the whole running app** with
  no catchable boundary and no rollback (on Windows the `.pyd` is file-locked while loaded). Flask
  **explicitly guards against** adding routes after first request. So we never import Pro into, or mount
  Pro routes onto, the **running** process.
- **Chosen — Option A: clean self-relaunch.** Every dangerous operation (native import, plugin mount)
  happens **only at a fresh process start**, the one place CPython makes it safe and which can cleanly
  fall back to open-core if a build is ever bad. The "no restart" *feeling* comes from an instant
  celebration + a sub-second, state-preserving, animated relaunch. ~90-95% of the magic, **zero risk to
  the live app**.
- **Option B (out-of-process Pro host, true zero-restart) is future roadmap**, not this slice — it needs
  IPC, child-process supervision, and a changed plugin contract.

**Open-core boundary preserved:** `model-hub` never imports `lac_pro`. It installs the artifact
(opaque), writes the grant by running the plugin's own CLI in a **throwaway subprocess**, and relaunches
so the normal startup seam mounts Pro.

## 3. Goal & non-goals

**Goal:** A GUI buyer clicks Activate, enters a key, sees a celebration, the app seamlessly relaunches,
and Pro is on and visibly so — with no manual restart and no possibility of a bad Pro build destabilizing
the running app.

**Non-goals (later slices / roadmap):**
- The Pro **cockpit** / model-tweaking UX — the celebration *lists* what unlocked; building the deep
  tuning surface is **S3**.
- Out-of-process Pro host (Option B).
- Chat latency (S4).
- Any change to how the grant is cryptographically formed or validated (that shipped + is proven; S2
  only *triggers* it via the existing `lac pro activate`).

## 4. Design

Three independent workstreams.

### Workstream P — `lac-pro`: Pro status route

Add one route to `ProPlugin.register_api(app)` (in `lac_pro/plugin.py`):

- **`GET /api/pro/status`** → `200` with
  `{"licensed": bool, "plan": str|None, "expires_human": str|None, "machine": str|None, "checked": str|None}`,
  derived from `license.check()` + `license._load_raw()` (the same data `_cmd_status` prints). Unlicensed
  (grant is `None`) → `{"licensed": false, ...nulls}`. This route only exists once the plugin is mounted
  (i.e., post-activation, after relaunch); before that it 404s, which the frontend reads as "not licensed."

No `/api/pro/activate` route is added — activation is done via the subprocess CLI (Workstream C), the safe
fresh-process path. `lac-pro` change is limited to this one route + its tests.

### Workstream C — `model-hub`: activate-that-licenses + safe self-relaunch

**C1. `POST /api/pro/activate`** (core; supersedes the install-only `/api/pro/unlock` for the GUI path):
1. Validate the request body has a non-empty string `key` (else `400`).
2. `install_pro_plugin(key)` (existing) — install the `.pyd`. On its honest failure dict, return that
   verbatim (`install_failed` family).
3. On install success, **write the grant** by running the freshly-installed plugin's CLI in a throwaway
   subprocess via `backend.cookbook.proc.run`: argv `= [<self>, "pro", "activate"]`, with **the key passed
   over stdin or the environment, never as an argv token** (so it never appears in the process table — the
   A3 tasklist lesson). `<self>` = `sys.executable` when frozen (the exe), else `[sys.executable, "server.py"]`
   for dev — resolved by a small `_self_invocation()` helper. Parse the subprocess exit code + output into
   honest states: `activated`, or `activation_failed` with a human message (bad/expired key, network, seat
   taken).
4. Return `{"state": "activated"}` | `{"state": "install_failed", ...}` | `{"state": "activation_failed",
   "message": ...}`. Never raises.

`/api/pro/unlock` remains for backward-compat (CLI `lac unlock`), but the GUI calls `/api/pro/activate`.

**C2. CLI-vs-window routing fix** (`server.py`): the frozen exe invoked with a subcommand (e.g.
`lac.exe pro activate`) must run the **CLI, not open the desktop window**. Today `_should_use_window`
returns True whenever frozen. Change: `_should_use_window` returns False when `argv` carries a recognized
CLI subcommand/positional (the subprocess-activate invocation, and any CLI use of the exe). The no-arg
frozen launch still opens the window. (Correct independent of S2; required for C1's subprocess to not spawn
a window.)

**C3. `POST /api/app/relaunch`** (core, backed by a helper in `backend/desktop.py`):
1. Persist window state — current bounds `{x,y,width,height}` (from the live pywebview window) + the
   `view` the caller passes — to `~/.model-hub/window_state.json` (written via the A4
   `resolve_under_data_root` guard).
2. Relaunch: `proc.popen([*self_invocation(), *window_args])` then `os._exit(0)`. The detached fresh
   process cold-boots, entry-point discovery mounts Pro, and window creation restores the saved
   geometry + navigates to the saved `view`.
3. If `Popen` fails, do **not** exit — return an honest error so the frontend can tell the user to restart
   manually (the grant is already on disk, so Pro comes up on any later launch).

**C4. Window-state restore** (`backend/desktop.py`): on `launch_desktop`, read `window_state.json` (if
present + valid) and pass saved `x/y/width/height` to `webview.create_window`, and the saved `view` as a
URL query/hash so the UI lands where it left off. Missing/corrupt state → current defaults (best-effort;
never blocks launch).

### Workstream F — Frontend: status surface + the activation moment

**F1. Pro status surface** (`web/src/pages/settings.tsx`): fetch `GET /api/pro/status` (treat a 404 /
route-absent as `{licensed:false}`). Licensed → a **Pro status card** (plan · expires · machine, and a
subdued "manage" affordance); not licensed → the existing "Activate Pro" **key input**. This replaces the
always-show-input bug.

**F2. The activation flow** (new component, e.g. `web/src/components/pro-activation.tsx`):
key input → `POST /api/pro/activate` with a real "Activating…" progress state → on `activated`, a
**celebration modal** ("You're Pro" + a concise list of what unlocked: Autopilot · model tuning · custom
Hugging Face import · calibration insights — *listed, not built here*) → a single "Enter Pro" action that
posts `/api/app/relaunch` (passing the current view) and shows a branded **"Activating Pro…" overlay** →
the app self-relaunches and returns to the same view with Pro visible. Honest inline errors for
`install_failed` / `activation_failed` (no celebration, keep the input).

**F3. View persistence** across relaunch: the frontend reads the restored `view` (from the URL query/hash
C4 sets) on boot and navigates there, so the relaunch reads as continuity.

## 5. Data flow (happy path)

```
[web] Activate Pro (key)
  -> POST /api/pro/activate
       -> install_pro_plugin(key)            # .pyd into ~/.model-hub/plugins  (existing)
       -> proc.run([self,"pro","activate"], key via stdin/env)   # throwaway process writes encrypted grant
       -> {state: activated}
  -> celebration modal ("You're Pro")
  -> "Enter Pro" -> POST /api/app/relaunch (view)
       -> persist window_state.json  ->  Popen(self, --window …)  ->  os._exit(0)
  [fresh process] launch_desktop -> restore geometry+view -> entry-point discovery mounts Pro
       -> /api/pro/status now 200 {licensed:true}
  -> Settings shows Pro status card; Pro features live
```

## 6. Error handling

- **Install fails** → `install_failed` (network/invalid_key/download/install, verbatim from
  `install_pro_plugin`); no grant write, no relaunch, keep the input + honest message.
- **Activation fails** (subprocess non-zero: bad/expired key, network, seat taken) → `activation_failed`
  with a human message; `.pyd` is installed but no grant; keep the input.
- **Relaunch fails** (`Popen` error) → honest error, do NOT `os._exit`; grant is on disk so Pro activates
  on the next manual launch.
- **Corrupt `window_state.json`** → ignored; default geometry/view.
- Every new endpoint follows the existing "never raise; honest JSON state" idiom.

## 7. Testing

**Automated:**
- `lac-pro`: `/api/pro/status` returns licensed/unlicensed shapes for a present vs `None` grant
  (monkeypatch `check`/`_load_raw`; isolate `GRANT_PATH`).
- `model-hub` `/api/pro/activate`: install-success → subprocess invoked with key NOT in argv (assert key
  passed via stdin/env) → `activated`; install-failure passthrough; subprocess-nonzero → `activation_failed`.
  (`install_pro_plugin` and `proc.run` both mocked.)
- `_self_invocation()` resolves frozen (exe) vs dev correctly.
- C2 routing: `_should_use_window` returns False when argv has a CLI subcommand, True for the no-arg frozen
  launch.
- C3 relaunch helper: window_state persisted under the data-root guard; `Popen` called with the self
  invocation; `os._exit` reached only on `Popen` success (both mocked); `Popen`-fail path returns error and
  does NOT exit.
- C4 restore: valid state → geometry+view passed to a mocked `create_window`; corrupt/missing → defaults.
- Frontend: status card vs input by licensed state; celebration shows only on `activated`; failure states
  keep the input.
- Boundary guard: a test asserting `model-hub` sources never `import lac_pro` / `from lac_pro`.

**Manual smoke (recorded in the ledger, like B4's build proof):**
- Real end-to-end on the packaged exe with a real key: Activate → grant written (encrypted `license.json`)
  → celebration → self-relaunch → lands on same view → `/api/pro/status` = licensed → a Pro feature works.
- Relaunch preserves the window position + the view.

## 8. Risks & mitigations

- **Subprocess-activate can't spawn a window (C2).** If the routing fix is wrong, `lac.exe pro activate`
  opens a window instead of writing the grant. Mitigation: C2's explicit test + the manual smoke.
- **Self-relaunch orphan/duplicate.** The single-instance mutex (S1/B2) is released when `os._exit` fires,
  so the fresh process acquires it cleanly; verify no double-window in the manual smoke.
- **Key leakage in process table.** Mitigated by passing the key via stdin/env, asserted in tests.
- **A bad Pro build.** Only ever imported at fresh startup; a failed mount degrades to open-core (existing
  `_discover_plugins_safe` isolation) rather than crashing a live session — the whole point of Option A.

## 9. Definition of done

- GUI buyer can activate from the browser: `.pyd` installed **and** encrypted grant written (no CLI needed).
- Key never appears in the process table (passed via stdin/env; asserted).
- Frozen exe with a CLI subcommand runs the CLI, not the window.
- Celebration modal fires on success; self-relaunch returns to the same view with Pro live.
- Settings shows a Pro status card when licensed, the input when not.
- Relaunch failure degrades gracefully (grant persisted; Pro on next launch).
- `model-hub` still never imports `lac_pro` (asserted).
- Full suites green (`model-hub` + `lac-pro` + web typecheck/build); manual E2E smoke recorded.
- Nothing pushed/published without Duan's explicit go; `lac-pro` never gets a remote.
