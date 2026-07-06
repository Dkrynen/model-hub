# LAC Shell Safety Audit — Filesystem Blast Radius + Guard (Task A4)

Part of the LAC Shell Hardening (S1) plan (`docs/superpowers/plans/2026-07-06-lac-shell-hardening.md`). Companion to the process/shell hardening delivered in A1-A3 (subprocess wrapper + no-window, PID-scoped kills). This document is the filesystem half: every place LAC writes, deletes, or moves something on disk, whether the destination is provably sandboxed, and what — if anything — is accepted residual risk.

**Method:** ran the plan's Step 1 grep verbatim over the runtime surface —

```
grep -rniE "open\(.+[\"']w|\.write_text|\.write_bytes|os\.makedirs|\.mkdir\(|shutil\.(copy|move|rmtree)|os\.remove|os\.unlink" backend server.py
```

— then read every hit's call chain back to its caller to classify the path as a fixed constant, a validated/sandboxed input, or (none found) an unvalidated input.

## Write sites

| Site | file:line | What it does | Blast radius | Mitigation / residual risk |
|---|---|---|---|---|
| `config.py: _ensure_dir` | `backend/cookbook/config.py:32` | `CONFIG_DIR.mkdir()` — creates `~/.model-hub` | Root of the sandbox itself | Fixed constant (`Path.home() / ".model-hub"`). No input. Safe by construction. |
| `config.py: _workspaces_dir` | `backend/cookbook/config.py:43` | `mkdir()` on `CONFIG_DIR/workspaces` | Container dir for all workspaces | Fixed constant. Safe. |
| `config.py: _sessions_dir` | `backend/cookbook/config.py:47-51` | `mkdir()` on `workspaces/<ws>/sessions` | Would honor whatever `workspace` string is passed | **Dead code** — grepped every caller in the repo; nothing calls `_sessions_dir()`. Not currently reachable. If wired up later it must route the `workspace` argument through `_resolve_within_workspaces` (or the new `resolve_under_data_root`) first — flagged here so a future caller doesn't reintroduce the traversal class already fixed for `create_workspace`/`delete_workspace`. |
| `config.py: _exports_dir` | `backend/cookbook/config.py:54-58` | `mkdir()` on `workspaces/<ws>/exports` | Same as above | Same finding: **dead code**, same guard requirement before it's ever wired up. |
| `config.py: _downloads_cache_dir` | `backend/cookbook/config.py:63` | `mkdir()` on `CONFIG_DIR/downloads` | Download-cache container | Fixed constant. Safe. |
| `config.py: save_config` | `backend/cookbook/config.py:81-82` | `open(CONFIG_FILE, "w")`, writes `config.json` | Overwrites the app's own config file | Fixed constant path (`CONFIG_DIR/config.json`). Content is a `dataclass` the caller built (workspace id, theme, etc.) — not raw user text with path semantics. Safe. |
| `config.py: _create_default_workspace` | `backend/cookbook/config.py:109,113` | `mkdir()` + `write_text()` for `workspaces/default/workspace.json` | Default workspace only | `DEFAULT_WORKSPACE` is a module constant (`"default"`), never caller input. Safe. |
| `config.py: create_workspace` | `backend/cookbook/config.py:151,160,163` | `mkdir()` + `write_text()` under `workspaces/<ws_id>` | Arbitrary dir name derived from the user-supplied workspace **name** | **Already hardened** (pre-existing, predates this task): `ws_id` is resolved through `_resolve_within_workspaces()` (line 149) before any write — resolve-then-`parents`-containment, identical pattern to the new `resolve_under_data_root`. A prior real exploit (`POST /api/workspaces {"name": "../../../../Temp/x"}`) is documented in the docstring at line 131-137 and covered by `tests/test_workspace_path_safety.py`. No residual risk beyond what that guard already closes. |
| `config.py: delete_workspace` | `backend/cookbook/config.py:189` (`shutil.rmtree`) | Recursive delete of a workspace dir | Was the same exploit's delete-side primitive (arbitrary recursive delete) | Same guard (`_resolve_within_workspaces`, line 175) gates it before `rmtree` runs. Already tested (`test_delete_workspace_rejects_path_traversal`). No residual risk. |
| `benchmark.py: log_result` | `backend/cookbook/benchmark.py:67,70` | `mkdir()` + append to `~/.model-hub/benchmarks/results.jsonl` | Benchmark log only | Fully fixed path (`Path.home()/".model-hub"/"benchmarks"`). No caller input in the path. Safe. |
| `downloads.py: log_download` | `backend/cookbook/downloads.py:20,28` | `mkdir()` + append to `~/.model-hub/downloads/history.jsonl` | Download-history log only | Fixed path. `model_name` is written into the JSON **body**, never into the path. Safe. |
| `persistence.py: _ensure_db` | `backend/cookbook/persistence.py:20` | `mkdir()` for `~/.model-hub` (sqlite dir) | Session/message DB | Fixed constant (`DB_DIR = Path.home()/".model-hub"`). All session/message content goes through sqlite parameter binding, not raw file paths. Safe. |
| `recommend.py: register_custom_model` | `backend/cookbook/recommend.py:177,186` | `mkdir()` + `write_text()` on `~/.model-hub/custom_models.json` | Custom-model catalog (LAC Pro HF import feature) | Fixed constant path. `entry_dict["id"]` is only used to filter/replace a dict key inside the JSON array — never concatenated into a filesystem path. The actual HF-artifact download-path traversal risk lives in `lac-pro` (out of this repo per the plan's Global Constraints — "S1 is open-core only, `lac-pro` is untouched") and the plan notes it was already hardened in a prior audit-fix task. Safe within this repo's scope. |
| `permission/engine.py: AlwaysAllowStore.__init__` | `backend/permission/engine.py:72-74` | `mkdir()` for the permissions DB's parent dir | Permission-decision DB | Default `db_path` is `CONFIG_DIR/permissions.db` (fixed constant); every call site in the runtime app (`engine.py:183`) and in tests constructs it with no argument, so the constant path is what's actually used. The constructor does accept an override, but nothing in the shipped app supplies one. Safe as currently wired; would need review if a caller ever passed a non-constant `db_path`. |
| `plugin/builtins/tools.py: _write_file` | `backend/plugin/builtins/tools.py:41-42` | `mkdir()` + `write_text()` — the AI agent's "write file" tool | Writes anywhere the *model* asks, scoped to the tool's own sandbox | **Not scoped to `~/.model-hub` by design** — this is the coding-agent's file tool, sandboxed instead to `ctx["cwd"]` (the active project directory), which is the correct boundary for that feature (an agent that can only write inside `~/.model-hub` couldn't edit the user's project). Already guarded: `target.relative_to(base)` raises and is caught, refusing any `path` that resolves outside `cwd` (mirrors the same resolve-then-containment shape used elsewhere). Residual/accepted risk: relies on `cwd` itself being trustworthy and doesn't defend against symlinks planted inside `cwd` that point back out — out of scope for a data-root guard since the boundary here is intentionally different. |
| `plugin/builtins/tools.py: _run_bash` | `backend/plugin/builtins/tools.py:60-74` | Executes an arbitrary shell command via `proc.run(..., shell=True)` | Full command execution in `cwd`, not a raw fs write, but the widest blast radius in the codebase | By design — this is the agent's bash tool. A2 already routed it through `proc.run` (console-hidden, no behavior change). Accepted risk: intentional arbitrary-command execution is the feature; the mitigation is a 60s timeout and that it only runs when the user has enabled/invoked the bash tool for the agent, same trust model as any local dev tool that can run shell commands on your own machine. Not a filesystem-sandbox problem to "fix" — flagged here for completeness per the plan's instruction to cover every shell-out. |
| `pro_install.py: _install` / `_move_contents` | `backend/pro_install.py:132,135,141,147` | `rmtree`/`unlink`/`move`/`mkdir` while installing a licensed plugin artifact into `~/.model-hub/plugins` | Extract-and-replace of the plugin directory | **Already hardened** (pre-existing): `PLUGIN_DIR` is a fixed constant under `CONFIG_DIR`, and every archive member is validated in `_validate_archive` (line 98-122) with the *exact same* resolve-then-`parents`-containment pattern before any extraction happens — a zip-slip guard. Extraction lands in a `tempfile.mkdtemp` staging dir first, moved into place only after validation. No residual traversal risk found. |
| `api.py: _scrape_library` | `backend/api.py:466-469` | `open(cache_path, "w")` — writes `library_cache.json` | Library-browse cache | **Residual note (not a security risk, a scope note):** `cache_path = Path(__file__).parent / "cookbook" / "data" / "library_cache.json"` is a fixed constant with no caller input, so there is no traversal risk — but it is **not** under `~/.model-hub`; it's inside the installed package directory. In a dev checkout this is writable; in a frozen/installed build (e.g. under `Program Files`) this write could silently fail. It already fails soft (`except Exception: pass`, line 470-471) so a permission failure just means the cache doesn't warm — no crash, no data loss. Not remediated in this task (out of scope: not a blast-radius issue, a packaging-location nit); worth a follow-up ticket to move it under `CONFIG_DIR` if the installed-build write-failure ever surfaces in practice. |
| `openapi_gen.py: write_openapi` | `backend/openapi_gen.py:115-120` | `open(path, "w")` — writes an OpenAPI spec JSON | Wherever the caller points it | `path` is caller-supplied, but the only caller (`cli.py:642`, the `lac openapi --out <path>` dev command) is a **local CLI arg** — the same trust boundary as any command-line tool a user runs on their own machine. Not remotely reachable (no HTTP route calls `write_openapi`). Accepted: by-design "write wherever the user points the CLI," not a `~/.model-hub` sandbox violation. |
| `cookbook/export.py: export_session_file` / `export_all` | `backend/cookbook/export.py:248,251,257,264` | `mkdir()` + `write_text()` — exports a session to a user-chosen directory | Wherever the caller points it | Same category as `write_openapi`: `out_dir` comes from `cli.py:671,681`'s `args.out`, a **local CLI flag** (`lac export --out <dir>`), never exposed over the web API (grepped `backend/api.py` for "export" — no route). By design, a "Save As"-style export is meant to write outside the sandbox to wherever the user asks. Not remediated — this is not the same threat class as the workspace-name traversal (no untrusted remote input reaches `out_dir`). |
| `cookbook/generate_models.py` | `backend/cookbook/generate_models.py:204` | `open(out_path, "w")` — regenerates the shipped model catalog | Repo source file | **Not part of the runtime app** — grepped for importers; nothing imports this module. It's a standalone developer codegen script run manually to refresh `backend/cookbook/data/models.json` before a release. Out of the runtime attack surface entirely. |

## Shell-outs (Task A1/A2 — routed through `backend/cookbook/proc.py`)

| Site | file:line | Command | Notes |
|---|---|---|---|
| `server.py: find_port_pids` | `server.py:57` | `netstat -ano` | Read-only, console hidden via `proc.run`. |
| `server.py: kill_pids` | `server.py:112` | `taskkill /F /T /PID <pid>` | PID-scoped (A3, see below); console hidden. |
| `backend/api.py` | `backend/api.py:410` | `<ollama path> --version` | Console hidden. |
| `backend/update.py` | `backend/update.py:125,134` | `git pull`, an upgrade command | Console hidden. |
| `backend/plugin/builtins/tools.py: _run_bash` | `backend/plugin/builtins/tools.py:66` | Arbitrary agent-issued shell command, `shell=True` | Console hidden; see the `_run_bash` write-table entry above for the accepted-risk framing (full command execution is the intended feature). |
| `backend/cookbook/hardware.py` | `backend/cookbook/hardware.py:67,293` | Hardware-probe commands + a PowerShell block | Console hidden. |

`tests/test_no_raw_subprocess.py` is a standing grep-gate: any future `subprocess.run/Popen/check_output/check_call/call` outside `proc.py` fails the suite. Verified passing (see Test evidence below).

## Process kills (Task A3 — PID-scoped, `server.py`)

| Site | file:line | Behavior |
|---|---|---|
| `server.py: _process_is_ours` | `server.py:76` | True only if the PID is in our in-process spawn registry (`proc.is_ours`) or `tasklist` shows its image name is `lac.exe`. |
| `server.py: kill_pids` | `server.py:104` | Filters to `_process_is_ours` before ever calling `taskkill`; refuses (prints, skips) anything else. |
| `server.py: clear_port` | `server.py:121` | Splits held PIDs into ours/foreign; if any foreign PID holds the port, refuses and returns `False` without touching it — never kills a process it doesn't own, even to free its own port. |

Covered by `tests/test_kill_safety.py` (foreign-process refusal, stale-LAC kill, kill_pids filtering, registry lookup) — all passing (see below).

## New guard: `resolve_under_data_root`

Added to `backend/cookbook/config.py` (the module that owns the `~/.model-hub` root constant, `CONFIG_DIR`):

```python
def resolve_under_data_root(name: str) -> Path:
    """Resolve `name` under the LAC data root, rejecting any path that escapes it."""
    root = CONFIG_DIR.resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes the LAC data root: {name!r}")
    return candidate
```

**Finding:** every write site that currently builds a path from *any* caller-influenced input already has its own equivalent guard (`create_workspace`/`delete_workspace` via `_resolve_within_workspaces`; `pro_install.py`'s zip-slip check via `_validate_archive`; `_write_file`'s `cwd`-relative containment). No currently-wired call site needed rewiring to use `resolve_under_data_root` — every fixed-constant write site needs no guard at all, and every input-influenced write site already had one. Per the plan's scope note ("If the audit finds every write already uses a fixed constant path, the guard is still added and unit-tested for future use, and the audit records that finding") the guard is added and unit-tested now so the next `~/.model-hub`-scoped write (e.g. wiring up the currently-dead `_sessions_dir`/`_exports_dir`, or any new feature) has a one-line, already-tested way to stay inside the sandbox, instead of hand-rolling the resolve-then-`parents` pattern a fourth time. Wiring it into existing call sites is explicitly out of scope for this task.

## Test evidence

`tests/test_fs_sandbox.py` (new, RED->GREEN):

- RED: `AttributeError: module 'backend.cookbook.config' has no attribute 'resolve_under_data_root'` (3 failures) before the guard was added.
- GREEN after implementation: 3 passed — allows a child path (`downloads/model.bin`), rejects a `../../` traversal escape, rejects an absolute path (`C:/Windows/evil.dll`).

Full non-live suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"` — 300 collected/passed, 0 failures, 0 errors (297 pre-existing + 3 new). No regressions.

## Summary

- **No unguarded write site was found.** Every write either uses a fixed constant path under `~/.model-hub`, or (workspace create/delete, plugin install, agent file-write) already resolves caller input through a resolve-then-containment guard equivalent to the one added here.
- **Two write sites are intentionally outside `~/.model-hub`** by design and only reachable via local CLI flags, never HTTP: `export_session_file`/`export_all` (session export destination) and `write_openapi` (dev spec dump). Not a sandbox violation — there is no sandbox promise for a "save wherever you point me" CLI feature.
- **One write site (`library_cache.json`) sits outside `~/.model-hub`** as a packaging nit, not a security issue — fixed path, fails soft, flagged as a possible follow-up.
- **Two helpers are dead code** (`_sessions_dir`, `_exports_dir`) — flagged so a future caller wires them through `resolve_under_data_root` instead of writing a fourth ad hoc guard.
- `resolve_under_data_root` is added and unit-tested (3/3 passing) as the reusable primitive for all of the above, per the plan's Task A4 scope.
