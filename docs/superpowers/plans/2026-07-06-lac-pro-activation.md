# LAC Pro Self-Serve Activation (S2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a GUI buyer activate LAC Pro from the browser — install the `.pyd`, write the license grant, celebrate, and self-relaunch so Pro mounts at a fresh (safe) startup — with a visible Pro status surface and no manual restart.

**Architecture:** Option A (researched, safe): never hot-load the compiled `.pyd` into the running process. The web Activate flow installs the plugin, then writes the grant by running `lac pro activate` in a **throwaway subprocess** (key passed via **stdin**, never argv), then the app **self-relaunches** so entry-point discovery mounts Pro exactly as at any normal startup. The open-core process never imports `lac_pro`.

**Tech Stack:** Python 3.11, Flask, pywebview 6.x, PyInstaller (`build.spec`), React/Vite, pytest. Two repos: `model-hub` (open core) + `lac-pro` (private, no remote).

## Discoveries since the spec (read first)

Planning surfaced two realities the spec's framing didn't capture; the plan handles both:
1. **The frozen exe can't run CLI subcommands.** `server.py` (the exe entry) only parses web-server flags; the CLI (`lac pro activate`, `scan`, …) lives in `cli.py`, only wired as the pip console-script. So `lac.exe pro activate` fails today. **Fix (Task C1):** the exe delegates a CLI-style invocation to `cli.main()`. (Duan approved this — the exe becomes a real CLI too.)
2. **Activation would fail for a real buyer.** `lac_pro/activate.py::_org_id()` returns `""` and hard-fails when `LAC_PRO_ORG_ID` is unset, even though `lac_pro/ls.py` already bakes the real org as a default. **Fix (Task P1):** `activate.py` uses the same baked default.

## Global Constraints

- **Open-core boundary (non-negotiable):** `model-hub` source must never `import lac_pro` / `from lac_pro …`. It drives the plugin only via the seam (discovery, `register_api`, subprocess CLI). A test asserts this.
- **Key never in the process table:** the license key is passed to the activate subprocess via **stdin**, never as an argv token or env var. Asserted in tests.
- **Never hot-load the `.pyd` into the running process.** Pro mounts only at a fresh startup (self-relaunch). No late Flask route registration on the running app.
- **Never-raise / honest-JSON idiom** for every new endpoint (match `install_pro_plugin`'s `{"state","error_type","message"}` style).
- **Windows-first;** every window/ctypes/relaunch path no-ops cleanly off-Windows.
- **Graceful degradation:** a written grant + a failed relaunch still yields Pro on the next manual launch.
- **Platforms/tests:** `model-hub` via `.venv\Scripts\python.exe -m pytest -q -m "not live"`; `lac-pro` via the same interpreter `-m pytest -q -m "not live and not slow"` run from the `lac-pro` dir. Full suites stay green; new tests RED-first.
- **Nothing pushed/published without Duan's explicit go; `lac-pro` never gets a remote.**
- **S3 out of scope:** the celebration *lists* unlocked features; it does not build the cockpit.

---

# PART P — `lac-pro` seam (private repo: `C:/Users/User/repos/lac-pro`)

### Task P1: Activation works for a real buyer + accepts the key via stdin

**Files:**
- Modify: `lac_pro/activate.py` (`_org_id`, `configure_activate`, `_cmd_activate`; add `import sys`)
- Test: `lac-pro/tests/test_activate.py` (update `test_activate_no_org_id`; add stdin tests)

**Interfaces:**
- Produces: `lac pro activate` now accepts the key as an optional positional OR from stdin; `_org_id()` returns the baked org when `LAC_PRO_ORG_ID` is unset.

- [ ] **Step 1: Write/adjust the failing tests**

```python
# lac-pro/tests/test_activate.py  — replace test_activate_no_org_id and add two tests
import io

def test_activate_uses_baked_org_when_env_unset(monkeypatch):
    # No LAC_PRO_ORG_ID in env -> must NOT fail with "not set"; uses ls baked default.
    monkeypatch.delenv("LAC_PRO_ORG_ID", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", __import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "license.json")
    ok, msg = act.do_activate("K-9", "pc", activate_fn=_activate_fn)
    assert ok, msg
    assert "not set" not in msg


def test_cmd_activate_reads_key_from_stdin(monkeypatch):
    monkeypatch.setenv("LAC_PRO_ORG_ID", _ORG_ID)
    called = {}
    monkeypatch.setattr(act, "do_activate", lambda key, label, **kw: called.setdefault("key", key) or (True, "ok"))
    monkeypatch.setattr(act.sys, "stdin", io.StringIO("KEY-FROM-STDIN\n"))
    ns = type("NS", (), {"key": None})()
    act._cmd_activate(ns)                 # positional omitted -> reads stdin
    assert called["key"] == "KEY-FROM-STDIN"


def test_cmd_activate_prefers_positional_over_stdin(monkeypatch):
    called = {}
    monkeypatch.setattr(act, "do_activate", lambda key, label, **kw: called.setdefault("key", key) or (True, "ok"))
    monkeypatch.setattr(act.sys, "stdin", io.StringIO("STDIN-KEY\n"))
    ns = type("NS", (), {"key": "ARG-KEY"})()
    act._cmd_activate(ns)
    assert called["key"] == "ARG-KEY"
```

- [ ] **Step 2: Run to verify they fail**

Run (from `C:/Users/User/repos/lac-pro`): `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_activate.py -q`
Expected: FAIL — `_cmd_activate` doesn't read stdin; `_org_id` returns "" so `do_activate` fails "not set".

- [ ] **Step 3: Implement**

In `lac_pro/activate.py`, add `import sys` at the top. Change `_org_id`:

```python
def _org_id() -> str:
    """Env override, else the baked org default (same as ls.py) so a GUI buyer
    who never set LAC_PRO_ORG_ID can still activate."""
    from lac_pro.ls import _POLAR_ORG_ID  # noqa: PLC0415
    return os.environ.get("LAC_PRO_ORG_ID") or _POLAR_ORG_ID
```

Change the CLI wiring to accept stdin:

```python
def _cmd_activate(args) -> None:
    key = (args.key or "").strip()
    if not key:
        key = sys.stdin.readline().strip()   # web-activate pipes the key in on stdin
    if not key:
        print("  no license key provided")
        raise SystemExit(1)
    ok, msg = do_activate(key, socket.gethostname())
    print(f"  {msg}")
    if not ok:
        raise SystemExit(1)


def configure_activate(parser) -> None:
    parser.add_argument("key", nargs="?", help="License key (omit to read from stdin)")
    parser.set_defaults(func=_cmd_activate)
```

- [ ] **Step 4: Run to verify they pass**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_activate.py -q`
Expected: PASS.

- [ ] **Step 5: Full lac-pro suite + commit**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live and not slow"`
Expected: PASS.
```bash
git add lac_pro/activate.py tests/test_activate.py
git commit -m "feat(pro): activate uses baked org default + accepts key via stdin (GUI self-serve)"
```

---

### Task P2: `GET /api/pro/status` route

**Files:**
- Modify: `lac_pro/plugin.py` (add a route inside `ProPlugin.register_api`)
- Test: `lac-pro/tests/test_plugin_api.py` (create, or add to the existing plugin-API test module if present — check `tests/` first)

**Interfaces:**
- Produces: `GET /api/pro/status` → `200 {"licensed": bool, "plan": str|None, "expires_human": str|None, "machine": str|None, "checked": str|None}`.

- [ ] **Step 1: Write the failing test**

```python
# lac-pro/tests/test_pro_status_route.py
import flask
import lac_pro.plugin as plugin
import lac_pro.license as lic


def _app():
    app = flask.Flask(__name__)
    plugin.ProPlugin().register_api(app)
    return app.test_client()


def test_status_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    r = _app().get("/api/pro/status")
    assert r.status_code == 200
    assert r.get_json() == {"licensed": False, "plan": None, "expires_human": None, "machine": None, "checked": None}


def test_status_licensed(monkeypatch):
    grant = type("G", (), {"plan": "pro", "expires_human": "while subscribed"})()
    monkeypatch.setattr(lic, "check", lambda: grant)
    monkeypatch.setattr(lic, "_load_raw", lambda: {"instance_id": "abc123", "last_validated_at": 1751000000.0})
    body = _app().get("/api/pro/status").get_json()
    assert body["licensed"] is True
    assert body["plan"] == "pro"
    assert body["expires_human"] == "while subscribed"
    assert body["machine"] == "abc123"
    assert body["checked"]  # a formatted date string
```

- [ ] **Step 2: Run to verify it fails**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_pro_status_route.py -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement** — add inside `ProPlugin.register_api`, alongside the existing routes:

```python
        @app.route("/api/pro/status")
        def _pro_status():
            from datetime import datetime
            from lac_pro.license import check, _load_raw
            grant = check()
            if grant is None:
                return jsonify({"licensed": False, "plan": None,
                                "expires_human": None, "machine": None, "checked": None})
            raw = _load_raw() or {}
            checked = None
            if raw.get("last_validated_at"):
                checked = datetime.fromtimestamp(raw["last_validated_at"]).strftime("%Y-%m-%d")
            return jsonify({"licensed": True, "plan": grant.plan,
                            "expires_human": grant.expires_human,
                            "machine": raw.get("instance_id"), "checked": checked})
```

- [ ] **Step 4: Run to verify it passes**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_pro_status_route.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live and not slow"`
```bash
git add lac_pro/plugin.py tests/test_pro_status_route.py
git commit -m "feat(pro): GET /api/pro/status (licensed state for the web Pro surface)"
```

---

# PART C — `model-hub` backend (`C:/Users/User/repos/model-hub`)

### Task C1: The exe dispatches CLI subcommands to `cli.main()`

**Files:**
- Modify: `server.py` (`main()` — add CLI delegation at the very top; add `_is_cli_invocation`)
- Modify: `build.spec` (ensure `cli` + its imports bundle — add `"cli"` to `hiddenimports`)
- Test: `tests/test_cli_dispatch.py`

**Interfaces:**
- Produces: `server._is_cli_invocation(argv: list[str]) -> bool` — True when argv begins with a non-flag token (a CLI subcommand). When True, `main()` delegates to `cli.main()` and exits.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_dispatch.py
import server


def test_is_cli_invocation_true_for_subcommand():
    assert server._is_cli_invocation(["pro", "activate"]) is True
    assert server._is_cli_invocation(["scan"]) is True


def test_is_cli_invocation_false_for_server_flags_and_empty():
    assert server._is_cli_invocation([]) is False
    assert server._is_cli_invocation(["--window"]) is False
    assert server._is_cli_invocation(["--host", "localhost"]) is False


def test_main_delegates_cli(monkeypatch):
    monkeypatch.setattr(server.sys, "argv", ["lac", "pro", "activate"])
    called = {}
    import cli
    monkeypatch.setattr(cli, "main", lambda: called.setdefault("ran", True))
    with __import__("pytest").raises(SystemExit):
        server.main()
    assert called.get("ran") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_dispatch.py -q`
Expected: FAIL — `_is_cli_invocation` missing; `main()` doesn't delegate.

- [ ] **Step 3: Implement** — in `server.py`, add the helper and delegate at the TOP of `main()` (before the argparse that only knows server flags):

```python
def _is_cli_invocation(argv: list[str]) -> bool:
    """The exe is being used as a CLI when the first token is a subcommand
    (a bare word), not a server flag (--host/--window/...) and not empty."""
    return bool(argv) and not argv[0].startswith("-")
```

At the very start of `main()` (immediately after `import argparse`):

```python
    if _is_cli_invocation(sys.argv[1:]):
        import cli  # bundled into the exe so `lac.exe pro activate` / `lac.exe scan` work
        sys.exit(cli.main())
```

In `build.spec`, add `"cli"` to the `hiddenimports=[...]` list so PyInstaller bundles the CLI module and its import graph.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_dispatch.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q -m "not live"`
```bash
git add server.py build.spec tests/test_cli_dispatch.py
git commit -m "feat(shell): exe dispatches CLI subcommands to cli.main() (lac.exe pro activate / scan)"
```

**Note:** the real proof that `cli` bundles into the exe (`lac.exe pro --help` runs) is in the controller-run manual smoke at the end (a rebuild, like B4). A missing bundle is fixed there by adding the discovered module(s) to `hiddenimports`.

---

### Task C2: `POST /api/pro/activate` — install + write grant via stdin subprocess

**Files:**
- Create: `backend/self_invoke.py` (`cli_prefix()`, `window_prefix()`)
- Modify: `backend/api.py` (add `/api/pro/activate`)
- Test: `tests/test_pro_activate.py`, `tests/test_self_invoke.py`

**Interfaces:**
- Consumes: `install_pro_plugin(key)` (existing); `backend.cookbook.proc.run` (S1).
- Produces:
  - `backend.self_invoke.cli_prefix() -> list[str]` and `window_prefix() -> list[str]`.
  - `POST /api/pro/activate` → `{"state": "activated"}` | `{"state":"install_failed",...}` | `{"state":"activation_failed","message":...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_self_invoke.py
import os, sys
from backend import self_invoke


def test_cli_prefix_dev(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    p = self_invoke.cli_prefix()
    assert p[0] == sys.executable and p[-1].endswith("cli.py")


def test_cli_prefix_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert self_invoke.cli_prefix() == [sys.executable]


def test_window_prefix_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert self_invoke.window_prefix() == [sys.executable, "--window"]
```

```python
# tests/test_pro_activate.py
import backend.api as api


def _client():
    return api.app.test_client()


def test_activate_happy_path(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin", lambda k: {"state": "installed", "path": "x"})
    captured = {}
    class R:  # fake CompletedProcess
        returncode = 0
        stdout = "  activated — LAC Pro unlocked on this machine"
        stderr = ""
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return R()
    monkeypatch.setattr(api.proc, "run", fake_run)
    r = _client().post("/api/pro/activate", json={"key": "SECRET-KEY"})
    assert r.get_json() == {"state": "activated"}
    # key travels via stdin, NEVER in argv:
    assert "SECRET-KEY" not in " ".join(captured["cmd"])
    assert captured["input"].strip() == "SECRET-KEY"
    assert captured["cmd"][-2:] == ["pro", "activate"]


def test_activate_install_failure_passthrough(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin",
                        lambda k: {"state": "failed", "error_type": "invalid_key", "message": "bad key"})
    r = _client().post("/api/pro/activate", json={"key": "x"})
    body = r.get_json()
    assert body["state"] == "install_failed"
    assert body["error_type"] == "invalid_key"


def test_activate_subprocess_failure(monkeypatch):
    monkeypatch.setattr(api, "install_pro_plugin", lambda k: {"state": "installed", "path": "x"})
    class R:
        returncode = 1
        stdout = "  activation rejected (status: expired)"
        stderr = ""
    monkeypatch.setattr(api.proc, "run", lambda cmd, **kw: R())
    body = _client().post("/api/pro/activate", json={"key": "x"}).get_json()
    assert body["state"] == "activation_failed"
    assert "expired" in body["message"] or body["message"]


def test_activate_missing_key():
    assert _client().post("/api/pro/activate", json={}).status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_self_invoke.py tests/test_pro_activate.py -q`
Expected: FAIL — `backend.self_invoke` missing; route missing.

- [ ] **Step 3: Implement `backend/self_invoke.py`**

```python
"""How LAC re-invokes itself in a fresh process — as the CLI (for a throwaway
`lac pro activate`) or as the desktop window (for a self-relaunch). Frozen exe
dispatches CLI subcommands (see server._is_cli_invocation); a dev checkout runs
cli.py / server.py under the interpreter."""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))  # backend/
_REPO = os.path.dirname(_ROOT)


def cli_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(_REPO, "cli.py")]


def window_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--window"]
    return [sys.executable, os.path.join(_REPO, "server.py"), "--window"]
```

- [ ] **Step 4: Implement the route** — in `backend/api.py`, ensure `from backend.cookbook import proc` and `from backend import self_invoke` are imported, then add near the other `/api/pro/*` routes:

```python
@app.route("/api/pro/activate", methods=["POST"])
def api_pro_activate():
    """Self-serve Pro: install the plugin, then write the license grant by
    running `lac pro activate` in a throwaway process with the key on STDIN
    (never argv). Honest JSON states; never raises."""
    data = request.get_json(silent=True)
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "License key required"}), 400
    key = key.strip()

    installed = install_pro_plugin(key)
    if installed.get("state") != "installed":
        return jsonify({"state": "install_failed", **{k: v for k, v in installed.items() if k != "state"}})

    try:
        r = proc.run([*self_invoke.cli_prefix(), "pro", "activate"],
                     input=key + "\n", capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 — subprocess spawn failure
        return jsonify({"state": "activation_failed",
                        "message": f"Could not run activation: {e}"})
    if r.returncode != 0:
        msg = (r.stdout or r.stderr or "activation failed").strip().splitlines()[-1].strip()
        return jsonify({"state": "activation_failed", "message": msg})
    return jsonify({"state": "activated"})
```

- [ ] **Step 5: Run tests + full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_self_invoke.py tests/test_pro_activate.py -q`
Expected: PASS.
Run: `.venv/Scripts/python.exe -m pytest -q -m "not live"`
```bash
git add backend/self_invoke.py backend/api.py tests/test_self_invoke.py tests/test_pro_activate.py
git commit -m "feat(pro): POST /api/pro/activate — install + write grant via stdin subprocess"
```

---

### Task C3: `POST /api/app/relaunch` + window-state persistence

**Files:**
- Modify: `backend/desktop.py` (add `save_window_state`, `relaunch`)
- Modify: `backend/api.py` (add `/api/app/relaunch`)
- Test: `tests/test_relaunch.py`

**Interfaces:**
- Consumes: `backend.self_invoke.window_prefix()`; `backend.cookbook.proc.popen`; `backend.cookbook.config.resolve_under_data_root` (S1/A4).
- Produces:
  - `desktop.WINDOW_STATE_PATH` (a `Path` under `~/.model-hub`).
  - `desktop.save_window_state(bounds: dict | None, view: str | None) -> None`.
  - `desktop.relaunch(view: str | None = None, bounds: dict | None = None) -> bool` — persists state, spawns the window process, `os._exit(0)`; returns `False` (does NOT exit) if spawning fails.
  - `POST /api/app/relaunch` → `{"state":"relaunching"}` (process exits) or `{"state":"failed","message":...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_relaunch.py
import json
import backend.desktop as desktop


def test_save_window_state_writes_under_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "window_state.json")
    desktop.save_window_state({"x": 10, "y": 20, "width": 1200, "height": 800}, "settings")
    data = json.loads((tmp_path / "window_state.json").read_text())
    assert data["view"] == "settings"
    assert data["bounds"]["width"] == 1200


def test_relaunch_spawns_then_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(desktop, "save_window_state", lambda *a, **k: None)
    spawned = {}
    monkeypatch.setattr(desktop.proc, "popen", lambda cmd, **k: spawned.setdefault("cmd", cmd))
    exited = {}
    monkeypatch.setattr(desktop.os, "_exit", lambda code: exited.setdefault("code", code))
    ok = desktop.relaunch(view="browse")
    assert spawned["cmd"][-1] == "--window"
    assert exited["code"] == 0


def test_relaunch_failure_does_not_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "save_window_state", lambda *a, **k: None)
    def boom(cmd, **k):
        raise OSError("spawn failed")
    monkeypatch.setattr(desktop.proc, "popen", boom)
    called = {}
    monkeypatch.setattr(desktop.os, "_exit", lambda code: called.setdefault("code", code))
    assert desktop.relaunch() is False
    assert "code" not in called          # never exits on spawn failure
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_relaunch.py -q`
Expected: FAIL — symbols missing.

- [ ] **Step 3: Implement in `backend/desktop.py`** (add `import os`, `import json` if absent; `from backend.cookbook import proc`; `from backend import self_invoke`):

```python
from backend.cookbook.config import resolve_under_data_root

WINDOW_STATE_PATH = resolve_under_data_root("window_state.json")


def save_window_state(bounds, view) -> None:
    """Best-effort persist of window geometry + current view. Never raises."""
    try:
        WINDOW_STATE_PATH.write_text(json.dumps({"bounds": bounds or {}, "view": view or ""}))
    except Exception:
        pass


def relaunch(view=None, bounds=None) -> bool:
    """Persist state, spawn a fresh window process, and exit this one so the
    new process cold-boots and mounts Pro via the normal startup seam. Returns
    False WITHOUT exiting if the spawn fails (grant is already on disk, so Pro
    comes up on the next manual launch)."""
    save_window_state(bounds, view)
    try:
        proc.popen(self_invoke.window_prefix())
    except Exception:
        return False
    os._exit(0)
```

- [ ] **Step 4: Implement the route** in `backend/api.py`:

```python
@app.route("/api/app/relaunch", methods=["POST"])
def api_app_relaunch():
    from backend import desktop
    data = request.get_json(silent=True) or {}
    view = data.get("view") if isinstance(data, dict) else None
    bounds = data.get("bounds") if isinstance(data, dict) else None
    ok = desktop.relaunch(view=view, bounds=bounds)   # exits the process on success
    return jsonify({"state": "failed",
                    "message": "Could not relaunch; please restart LAC manually."}) if not ok \
        else jsonify({"state": "relaunching"})
```

- [ ] **Step 5: Run tests + full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_relaunch.py -q`
Run: `.venv/Scripts/python.exe -m pytest -q -m "not live"`
```bash
git add backend/desktop.py backend/api.py tests/test_relaunch.py
git commit -m "feat(shell): POST /api/app/relaunch + window-state persistence (safe self-relaunch)"
```

---

### Task C4: Restore window geometry + view on launch

**Files:**
- Modify: `backend/desktop.py` (`launch_desktop` / `_open_window` read `WINDOW_STATE_PATH`)
- Test: `tests/test_window_restore.py`

**Interfaces:**
- Produces: `desktop.load_window_state() -> dict` → `{"bounds": {...}, "view": str}` (empty defaults if missing/corrupt); `_open_window` passes saved geometry to `create_window` and the saved `view` as a URL query.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_window_restore.py
import json, sys, types
import backend.desktop as desktop


def test_load_window_state_missing_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", tmp_path / "nope.json")
    assert desktop.load_window_state() == {"bounds": {}, "view": ""}


def test_load_window_state_corrupt_returns_defaults(tmp_path, monkeypatch):
    p = tmp_path / "s.json"; p.write_text("{ not json")
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", p)
    assert desktop.load_window_state() == {"bounds": {}, "view": ""}


def test_open_window_applies_saved_view(monkeypatch, tmp_path):
    p = tmp_path / "s.json"; p.write_text(json.dumps({"bounds": {"x": 5, "y": 6, "width": 900, "height": 700}, "view": "settings"}))
    monkeypatch.setattr(desktop, "WINDOW_STATE_PATH", p)
    calls = {}
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: calls.setdefault("args", (a, k))
    fake.start = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    desktop._open_window("127.0.0.1", 5050)
    (title, url), kw = calls["args"]
    assert "view=settings" in url
    assert kw.get("x") == 5 and kw.get("width") == 900
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_window_restore.py -q`
Expected: FAIL — `load_window_state` missing; `_open_window` ignores saved state.

- [ ] **Step 3: Implement** in `backend/desktop.py`:

```python
def load_window_state() -> dict:
    try:
        data = json.loads(WINDOW_STATE_PATH.read_text())
        return {"bounds": dict(data.get("bounds") or {}), "view": str(data.get("view") or "")}
    except Exception:
        return {"bounds": {}, "view": ""}
```

Update `_open_window` (the happy path, before the fallback) to apply saved state:

```python
def _open_window(host: str, port: int) -> int:
    state = load_window_state()
    url = f"http://{host}:{port}"
    view = state.get("view")
    if view:
        url += f"/?view={view}"
    geom = {}
    b = state.get("bounds") or {}
    for k in ("x", "y", "width", "height"):
        if isinstance(b.get(k), int):
            geom[k] = b[k]
    try:
        import webview
        if webview is None:
            raise ImportError("webview unavailable")
        webview.create_window(WINDOW_TITLE, url, min_size=(1024, 700), **geom)
        webview.start()
        return 0
    except Exception as e:
        return _fallback_to_browser(host, port, str(e))
```

(The frontend reads `?view=` on boot — Task F2 — to route to the saved page.)

- [ ] **Step 4: Run tests + full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_window_restore.py tests/test_desktop.py tests/test_desktop_fallback.py -q`
Run: `.venv/Scripts/python.exe -m pytest -q -m "not live"`
```bash
git add backend/desktop.py tests/test_window_restore.py
git commit -m "feat(shell): restore window geometry + view on launch (relaunch continuity)"
```

---

### Task C5: Open-core boundary guard test

**Files:**
- Test: `tests/test_boundary_no_lac_pro_import.py`

**Interfaces:** none (guard only).

- [ ] **Step 1: Write the failing-then-passing guard**

```python
# tests/test_boundary_no_lac_pro_import.py
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAT = re.compile(r"^\s*(import\s+lac_pro|from\s+lac_pro)\b", re.M)


def test_model_hub_never_imports_lac_pro():
    offenders = []
    for f in list((ROOT / "backend").rglob("*.py")) + [ROOT / "server.py", ROOT / "cli.py"]:
        if "__pycache__" in f.parts:
            continue
        if PAT.search(f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(ROOT)))
    assert offenders == [], f"open-core must never import lac_pro: {offenders}"
```

- [ ] **Step 2: Run it** — Expected: PASS immediately (this task documents + locks the boundary; it is RED only if a prior task violated it, which would be a real bug to fix).

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_no_lac_pro_import.py -q`

- [ ] **Step 3: Commit**

```bash
git add tests/test_boundary_no_lac_pro_import.py
git commit -m "test(pro): lock the open-core boundary (model-hub never imports lac_pro)"
```

---

# PART F — Frontend (`model-hub/web`)

### Task F1: API client methods

**Files:**
- Modify: `web/src/lib/api.ts` (add `proStatus`, `activatePro`, `appRelaunch`; keep `unlockPro`)
- Test: covered via the component test in F2 (api.ts is a thin fetch wrapper; no standalone test unless the file has an existing test harness — check `web/src/lib/`).

**Interfaces:**
- Produces:
  - `api.proStatus(): Promise<{licensed:boolean; plan:string|null; expires_human:string|null; machine:string|null; checked:string|null}>` — treats a 404 as `{licensed:false,...nulls}`.
  - `api.activatePro(key: string): Promise<{state:"activated"} | {state:"install_failed"|"activation_failed"; message?:string; error_type?:string}>`.
  - `api.appRelaunch(view: string): Promise<{state:string; message?:string}>`.

- [ ] **Step 1: Implement** (match the existing fetch-wrapper style in `api.ts`):

```ts
proStatus: async () => {
  const r = await fetch("/api/pro/status");
  if (r.status === 404) return { licensed: false, plan: null, expires_human: null, machine: null, checked: null };
  return r.json();
},
activatePro: (key: string) =>
  fetch("/api/pro/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  }).then((r) => r.json()),
appRelaunch: (view: string) =>
  fetch("/api/app/relaunch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ view }),
  }).then((r) => r.json()),
```

- [ ] **Step 2: Typecheck** — Run (in `web/`): `npm run typecheck` → exit 0.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/api.ts
git commit -m "feat(web): api.proStatus / activatePro / appRelaunch"
```

---

### Task F2: Pro status surface + activation moment + relaunch overlay

**Files:**
- Create: `web/src/components/pro-activation.tsx` (the Pro card: status vs. activate flow + celebration modal + relaunch overlay)
- Modify: `web/src/pages/settings.tsx` (replace the inline LAC Pro card with `<ProActivation />`)
- Modify: `web/src/main.tsx` or the router entry (read `?view=` on boot and navigate) — check where routing/initial view is set
- Test: `web/src/components/pro-activation.test.tsx` (if the web app has a test runner configured — check `web/package.json`; if none, this task is verified by typecheck + build + the manual smoke, and the test step is skipped with a note)

**Interfaces:**
- Consumes: `api.proStatus`, `api.activatePro`, `api.appRelaunch` (F1).

- [ ] **Step 1: Build the component** — `web/src/components/pro-activation.tsx`:
  - On mount, `api.proStatus()`.
  - **Licensed** → a status card: "LAC Pro — active", `plan`, `expires_human`, `machine`, `checked`.
  - **Not licensed** → the license-key input + "Activate Pro" button (reuse the existing markup moved out of `settings.tsx`).
  - On Activate: `api.activatePro(key)`.
    - `activated` → open the **celebration modal**: "You're Pro 🎉 — here's what just unlocked" with a static list (Autopilot auto-tuning · Model tuning cockpit · Custom Hugging Face import · Calibration insights) and one primary button **"Enter Pro"**.
    - `install_failed` / `activation_failed` → inline error (`message`), keep the input, no modal.
  - "Enter Pro" → show a full-card **"Activating Pro…" overlay**, then `api.appRelaunch(currentView)` (pass `"settings"`). The process exits mid-request; the overlay simply persists until the window relaunches.

Use the existing `Card`/`Button`/`Input`/modal primitives already in `web/src/components/ui/`. Match the current Pro card's classes/spacing.

- [ ] **Step 2: Wire into settings.tsx** — replace the `{/* LAC Pro */}` `<Card>…</Card>` block (currently `settings.tsx:120-142`) with `<ProActivation />`, and remove the now-unused `licenseKey`/`unlocking`/`unlock` state + `api.unlockPro` usage from `settings.tsx` (they move into the component). Keep the old `unlockPro` in `api.ts` for the CLI/back-comfort path.

- [ ] **Step 3: Read `?view=` on boot** — where the app selects its initial route/view, read `new URLSearchParams(location.search).get("view")` and navigate there if present, so a relaunch lands on the saved page. (Find the router/initial-view logic; keep the change minimal.)

- [ ] **Step 4: Verify**
  - Run (in `web/`): `npm run typecheck && npm run build` → exit 0.
  - If a web test runner exists (`vitest`/`jest` in `web/package.json`): add `pro-activation.test.tsx` asserting (a) licensed status renders the status card, (b) `activated` opens the celebration modal, (c) a failure keeps the input and shows the message. Run it green. If no runner is configured, note that in the report; the flow is covered by the manual smoke.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/pro-activation.tsx web/src/pages/settings.tsx web/src/main.tsx
git commit -m "feat(web): Pro status surface + activation celebration + relaunch overlay"
```

---

## Final: manual E2E smoke (controller-run; record in `.superpowers/sdd/progress.md`)

A GUI window + real subprocess + real relaunch can't be unit-tested. After all tasks, rebuild and run on the packaged exe:

1. [ ] `cd web && npm run build && cd .. && .venv/Scripts/pyinstaller build.spec` → `dist/lac.exe` builds with **zero missing-import warnings for `cli`** (fix `build.spec` `hiddenimports` if any surface — the C1 bundling proof).
2. [ ] `dist\lac.exe pro --help` runs (proves the exe now dispatches CLI).
3. [ ] With a **real license key**: launch the window → Settings → enter key → Activate → the grant is written as the **encrypted** `~/.model-hub/license.json` (key NOT in cleartext) → celebration modal → "Enter Pro" → the app **self-relaunches** and lands back on Settings → the Pro card now shows **active** status → a Pro feature works (e.g. `/api/pro/status` returns licensed; Autopilot fires on a model install).
4. [ ] A bad/expired key surfaces `activation_failed` **before** any celebration.
5. [ ] Confirm no console flashes and a single instance across the relaunch (S1 guarantees hold).

---

## Self-review (completed by plan author)

**Spec coverage** — §4 Workstream P → P1 (activate/org) + P2 (status route); Workstream C → C1 (exe→CLI, the discovered prerequisite), C2 (activate endpoint + `self_invoke`), C3 (relaunch + state persist), C4 (restore), C5 (boundary guard); Workstream F → F1 (api), F2 (status surface + celebration + relaunch overlay + view restore). §6 error handling → honest states in C2/C3 + F2 inline errors. §7 testing → per-task automated + the final manual smoke + the boundary guard (C5) + key-not-in-argv assertion (C2).

**Placeholder scan** — no TBD/TODO. The two environment-dependent spots (whether `lac-pro`/`web` have existing test harnesses at the exact paths named) are flagged with a concrete "check `tests/` / `package.json` first" instruction, not left vague; the deliverable code is fully specified.

**Type consistency** — `cli_prefix()`/`window_prefix()` (self_invoke) used identically in C2/C3; `WINDOW_STATE_PATH`/`save_window_state`/`load_window_state`/`relaunch` consistent C3→C4; `/api/pro/activate` states (`activated`/`install_failed`/`activation_failed`) consistent backend C2 ↔ frontend F2; `/api/pro/status` shape consistent P2 ↔ F1 ↔ F2.

**Known assumptions to verify during execution** (flagged, non-blocking): `lac-pro` test-module naming/harness (P2 says check `tests/` first); the web test-runner presence (F2 Step 4 branches on it); the exact router/initial-view file for the `?view=` read (F2 Step 3 says "find the router logic"); `cli` bundling into the exe (proved in the final smoke, fixed via `hiddenimports` if needed).
