# M2 Depth Plan 1 — Backend Save Path (edit-and-stage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give humans a write path: `POST /api/projects/<id>/file/save` stages + auto-applies through the existing staged-changes store via a hidden per-project "manual edits" session, and wire the orphaned Revert/Apply-all endpoints into the web client.

**Architecture:** One new Flask route composes existing primitives (`_registered_project_root` → `stage_change` → base-hash check → `apply_staged_change`). Human saves land in a lazily created `origin='editor'` session excluded from every session listing. No changes to the agent staging pipeline. Frontend gets three new `api.ts` methods and a minimal hookup (Revert + Apply all) in the existing `StagedChangesPanel`.

**Tech Stack:** Flask + SQLite (backend/cookbook/persistence.py idioms), pytest, React 18 + TypeScript (existing patterns only — no new deps in this plan).

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`, branch `master` — **local-only, NEVER push (patent hold)**.
- All new routes MUST call the loopback guard (`_is_trusted_local_approval_request`) before anything else.
- Strict UTF-8 everywhere; staged-write cap `MAX_STAGED_BYTES = 2 * 1024 * 1024` (import from `backend.agent.staging`); read cap 1 MiB (existing `read_project_text` default — files > 1 MiB are unsaveable because they are unopenable, accepted).
- Error convention: `jsonify({"error": ..., "code": ...})` with 400/403/404/409/413/415; reuse `_project_file_error` for `SensitiveProjectPathError`.
- Python tests: `.venv\Scripts\python.exe -m pytest <file> -v` from repo root. Web gates: `npm run typecheck` (bare — never pipe, it masks the exit code) and `npm run build` in `web/`.
- Commits are auto-signed by repo-local config (already set up). Never `--no-verify`.
- Line anchors below were verified 2026-07-14 against working-tree master (`35bf00c`); re-check ±20 lines if a hunk doesn't match.
- Spec (do not copy into this public repo): workspace repo `docs/superpowers/specs/2026-07-14-lac-m2-diff-editor-design.md`.

---

### Task 1: `origin` column + manual-edits session (persistence)

**Files:**
- Modify: `backend/cookbook/persistence.py` (schema `:60-142`, `create_session` `:342`, `list_sessions` `:377`, `get_session` `:429`)
- Test: `tests/test_manual_session.py` (new)

**Interfaces:**
- Produces: `create_session(..., origin: str = "chat") -> str`; `get_or_create_manual_session(project_id: str) -> str` (returns session id, raises `ValueError("project does not exist")` for unknown project); session dicts now carry `"origin"`; `list_sessions` NEVER returns `origin='editor'` rows.
- Consumed by: Task 2's save route.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manual_session.py
from __future__ import annotations

import sqlite3

import pytest


def test_create_session_defaults_origin_chat(isolated_home):
    from backend.cookbook.persistence import create_session, get_session

    sid = create_session(name="t", model="m", workspace="default")
    assert get_session(sid)["origin"] == "chat"


def test_get_or_create_manual_session_creates_once(isolated_home, tmp_path):
    from backend.cookbook.persistence import (
        create_project,
        get_or_create_manual_session,
        get_session,
    )

    project = create_project("default", "proj", str(tmp_path), "")
    sid1 = get_or_create_manual_session(project["id"])
    sid2 = get_or_create_manual_session(project["id"])
    assert sid1 == sid2
    session = get_session(sid1)
    assert session["origin"] == "editor"
    assert session["project_id"] == project["id"]
    assert session["name"] == "Manual edits"


def test_get_or_create_manual_session_unknown_project(isolated_home):
    from backend.cookbook.persistence import get_or_create_manual_session

    with pytest.raises(ValueError):
        get_or_create_manual_session("0" * 14)


def test_manual_session_unique_per_project(isolated_home, tmp_path):
    from backend.cookbook import persistence

    project = persistence.create_project("default", "proj", str(tmp_path), "")
    persistence.get_or_create_manual_session(project["id"])
    with pytest.raises(sqlite3.IntegrityError):
        persistence.create_session(
            name="dup", workspace="default",
            project_id=project["id"], origin="editor",
        )


def test_list_sessions_excludes_editor_origin(isolated_home, tmp_path):
    from backend.cookbook import persistence

    project = persistence.create_project("default", "proj", str(tmp_path), "")
    chat_sid = persistence.create_session(
        name="chat", workspace="default", project_id=project["id"]
    )
    persistence.get_or_create_manual_session(project["id"])
    rows = persistence.list_sessions(workspace="default", project_id=project["id"])
    assert [r["id"] for r in rows] == [chat_sid]
    rows_all = persistence.list_sessions(workspace="default")
    assert all(r["id"] != "" and r.get("origin", "chat") != "editor" for r in rows_all)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_session.py -v`
Expected: FAIL — `origin` KeyError / `get_or_create_manual_session` AttributeError.

- [ ] **Step 3: Implement**

In `_migrate_schema` (persistence.py): add `origin` to the `CREATE TABLE sessions` DDL (after `project_id`):

```python
            project_id  TEXT REFERENCES projects(id) ON DELETE RESTRICT,
            origin      TEXT NOT NULL DEFAULT 'chat',
```

After the existing `project_id` ALTER block (`:80-83`), add the same idiom + the uniqueness guard for manual sessions:

```python
    if "origin" not in session_columns:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'chat'"
        )
```

Beside the other index creations (`:127-142`):

```python
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_manual_unique ON sessions(project_id) WHERE origin = 'editor'"
    )
```

`create_session` (`:342`): add keyword param `origin: str = "chat"`, add the column to the INSERT column list and `origin` to the values tuple (between `bound_project_id` and `now`), matching positions:

```python
        conn.execute(
            "INSERT INTO sessions (id, name, model, system_prompt, context, workspace, project_id, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, name, model, system_prompt, "{}", ws, bound_project_id, origin, now, now),
        )
```

`list_sessions` (`:377`): after `where.insert(0, "workspace = ?")`, append the standing exclusion (no opt-out — nothing lists editor sessions):

```python
        where.append("origin != 'editor'")
```

`get_session` (`:429`): add `origin` to the SELECT column list and `"origin": row[<idx>]` to the returned dict (keep index arithmetic consistent — origin goes after `project_id`, so `created_at`/`updated_at` indices shift by one; update `messages`/`events` handling untouched). Also add `origin` to `list_sessions`' SELECT and row dict the same way.

New function (place after `get_session`):

```python
def get_or_create_manual_session(project_id: str) -> str:
    """Return the per-project 'manual edits' session id, creating it lazily.

    Human editor saves are audited here; origin='editor' rows are excluded
    from every session listing.
    """
    project = get_project(project_id)
    if project is None:
        raise ValueError("project does not exist")
    conn = _ensure_db()
    try:
        row = conn.execute(
            "SELECT id FROM sessions WHERE project_id = ? AND origin = 'editor'",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row[0]
    try:
        return create_session(
            name="Manual edits",
            workspace=str(project["workspace"]),
            project_id=project_id,
            origin="editor",
        )
    except sqlite3.IntegrityError:
        # Lost a create race; the winner's row exists now.
        conn = _ensure_db()
        try:
            row = conn.execute(
                "SELECT id FROM sessions WHERE project_id = ? AND origin = 'editor'",
                (project_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise
        return row[0]
```

- [ ] **Step 4: Run new tests + neighbors**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_session.py tests/test_staged_changes.py -v`
Expected: all PASS (staged tests prove the schema change didn't disturb the store).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/persistence.py tests/test_manual_session.py
git commit -m "feat(staging): sessions origin column + per-project manual-edits session"
```

---

### Task 2: save route `POST /api/projects/<project_id>/file/save`

**Files:**
- Modify: `backend/api.py` — insert the route directly after `api_project_file` (`:1959-2009`), reusing its guard sequence.
- Test: `tests/test_project_file_save.py` (new)

**Interfaces:**
- Consumes: Task 1's `get_or_create_manual_session`; existing `stage_change`, `apply_staged_change`, `set_staged_status`, `get_staged_change`, `_registered_project_root`, `_project_file_error`, `_project_browser_*` helpers, `MAX_STAGED_BYTES` from `backend.agent.staging`.
- Produces: route contract for the Plan-2 editor —
  Request `{"path": str, "content": str, "base_sha256": str|null}`;
  200 `{"status":"applied","change_id":str,"path":str,"sha256":str,"size":int}`;
  409 `{"error":"conflict","code":"save_conflict","disk_sha256":str|null}`;
  413/415/400/403/404 per Global Constraints.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_project_file_save.py
from __future__ import annotations

import hashlib
import json


def _register(client, tmp_path, name="proj"):
    resp = client.post(
        "/api/workspaces/default/projects",
        json={"name": name, "root": str(tmp_path)},
    )
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["id"]


def _save(client, project_id, path, content, base):
    return client.post(
        f"/api/projects/{project_id}/file/save",
        json={"path": path, "content": content, "base_sha256": base},
    )


def test_save_update_happy_path(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, tmp_path)
    base = hashlib.sha256(b"original").hexdigest()
    resp = _save(client, pid, "a.txt", "changed", base)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "applied"
    assert body["path"] == "a.txt"
    assert body["sha256"] == hashlib.sha256(b"changed").hexdigest()
    assert body["size"] == len(b"changed")
    assert f.read_bytes() == b"changed"


def test_save_create_happy_path(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    resp = _save(client, pid, "src/new.py", "print('hi')\n", None)
    assert resp.status_code == 200, resp.get_json()
    assert (tmp_path / "src" / "new.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_save_drift_conflict_409(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    f = tmp_path / "a.txt"
    f.write_bytes(b"v2-on-disk")
    pid = _register(client, tmp_path)
    stale = hashlib.sha256(b"v1-the-editor-loaded").hexdigest()
    resp = _save(client, pid, "a.txt", "mine", stale)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "save_conflict"
    assert body["disk_sha256"] == hashlib.sha256(b"v2-on-disk").hexdigest()
    assert f.read_bytes() == b"v2-on-disk"  # disk untouched


def test_save_create_collision_409(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    f = tmp_path / "a.txt"
    f.write_bytes(b"surprise")
    pid = _register(client, tmp_path)
    resp = _save(client, pid, "a.txt", "mine", None)
    assert resp.status_code == 409
    assert resp.get_json()["disk_sha256"] == hashlib.sha256(b"surprise").hexdigest()


def test_save_conflict_leaves_no_pending_row(flask_app, isolated_home, tmp_path):
    from backend.cookbook.persistence import get_or_create_manual_session, list_staged_changes

    client = flask_app.test_client()
    (tmp_path / "a.txt").write_bytes(b"v2")
    pid = _register(client, tmp_path)
    _save(client, pid, "a.txt", "mine", hashlib.sha256(b"v1").hexdigest())
    sid = get_or_create_manual_session(pid)
    assert list_staged_changes(sid, status="pending") == []


def test_save_too_large_413(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    resp = _save(client, pid, "big.txt", "x" * (2 * 1024 * 1024 + 1), None)
    assert resp.status_code == 413


def test_save_over_binary_target_415(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    (tmp_path / "blob.bin").write_bytes(b"\x00\xff\x00\xff")
    pid = _register(client, tmp_path)
    base = hashlib.sha256(b"\x00\xff\x00\xff").hexdigest()
    resp = _save(client, pid, "blob.bin", "text", base)
    assert resp.status_code == 415


def test_save_jail_escape_400(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    resp = _save(client, pid, "../outside.txt", "x", None)
    assert resp.status_code in (400, 403, 404)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_save_invalid_body_400(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    assert client.post(f"/api/projects/{pid}/file/save", json=[1]).status_code == 400
    assert _save(client, pid, "a.txt", 42, None).status_code == 400
    assert _save(client, pid, 42, "x", None).status_code == 400
    assert _save(client, pid, "a.txt", "x", "not-hex").status_code == 400


def test_save_routes_through_manual_session_and_reuses_it(flask_app, isolated_home, tmp_path):
    from backend.cookbook.persistence import get_or_create_manual_session, list_staged_changes

    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    _save(client, pid, "one.txt", "1", None)
    _save(client, pid, "two.txt", "2", None)
    sid = get_or_create_manual_session(pid)
    rows = list_staged_changes(sid, status="applied")
    assert sorted(r["path"] for r in rows) == ["one.txt", "two.txt"]


def test_sessions_listing_hides_manual_session(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    _save(client, pid, "one.txt", "1", None)
    resp = client.get(f"/api/sessions?project_id={pid}")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_save_does_not_touch_pending_agent_row(flask_app, isolated_home, tmp_path):
    from backend.cookbook import persistence

    client = flask_app.test_client()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, tmp_path)
    chat_sid = persistence.create_session(
        name="chat", workspace="default", project_id=pid
    )
    agent_row = persistence.stage_change(
        chat_sid, "run1", str(tmp_path), "a.txt", "agent-version"
    )
    base = hashlib.sha256(b"original").hexdigest()
    resp = _save(client, pid, "a.txt", "human-version", base)
    assert resp.status_code == 200
    # Agent row untouched and still pending with its original snapshot.
    fresh = persistence.get_staged_change(agent_row["id"])
    assert fresh["status"] == "pending"
    assert fresh["base_hash"] == base
    # Its apply now conflicts, correctly.
    result = persistence.apply_staged_change(agent_row["id"])
    assert result["status"] == "conflict"


def test_save_then_revert_restores_disk(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, tmp_path)
    base = hashlib.sha256(b"original").hexdigest()
    change_id = _save(client, pid, "a.txt", "changed", base).get_json()["change_id"]
    resp = client.post(f"/api/agent/changes/{change_id}/revert")
    assert resp.status_code == 200
    assert f.read_bytes() == b"original"


def test_save_rejected_off_machine(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    pid = _register(client, tmp_path)
    resp = client.post(
        f"/api/projects/{pid}/file/save",
        json={"path": "a.txt", "content": "x", "base_sha256": None},
        headers={"Host": "evil.example.com"},
    )
    assert resp.status_code == 403
```

Fixture note for the implementer: mirror the exact fixture names/idioms used by the existing staged-route tests (see `tests/test_staged_changes.py` and the flask-client fixture used by `tests/test_api_agent_chat.py:28-53` / project-browser route tests). If the app fixture is named differently than `flask_app`, rename accordingly in this file — do not invent a new fixture.

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_file_save.py -v`
Expected: FAIL — 404s (route does not exist).

- [ ] **Step 3: Implement the route**

Insert after `api_project_file` (`api.py:2009`):

```python
_SAVE_RUN_ID = "editor-save"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@app.route("/api/projects/<project_id>/file/save", methods=["POST"])
def api_project_file_save(project_id):
    """Human edit-and-stage save: stage + auto-apply through the manual-edits session."""

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    if not _PROJECT_BROWSER_ID_PATTERN.fullmatch(project_id):
        return jsonify({"error": "invalid project identity", "code": "invalid_project_id"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required", "code": "invalid_body"}), 400
    rel = data.get("path")
    content = data.get("content")
    base_sha256 = data.get("base_sha256")
    if not isinstance(rel, str) or not rel:
        return jsonify({"error": "path must be a non-empty string", "code": "invalid_body"}), 400
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string", "code": "invalid_body"}), 400
    if base_sha256 is not None and (
        not isinstance(base_sha256, str) or not _SHA256_HEX.fullmatch(base_sha256)
    ):
        return jsonify({"error": "base_sha256 must be null or 64 lowercase hex chars", "code": "invalid_body"}), 400
    if not _project_browser_text_is_previewable(rel):
        return jsonify({"error": "path contains unsupported text controls", "code": "invalid_query"}), 400
    if _project_browser_path_is_skipped(rel):
        return jsonify({"error": "project path is not previewable", "code": "project_path_not_previewable"}), 403
    if not _project_browser_text_is_previewable(content, allow_leading_bom=True):
        return jsonify({
            "error": "content is not supported as previewable text",
            "code": "project_file_not_previewable",
        }), 415
    from .agent.staging import MAX_STAGED_BYTES

    if len(content.encode("utf-8")) > MAX_STAGED_BYTES:
        return jsonify({
            "error": "content exceeds the 2 MiB staged-change limit",
            "code": "project_file_too_large",
        }), 413
    try:
        _project, root = _registered_project_root(project_id)
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    from .cookbook.persistence import (
        apply_staged_change,
        get_or_create_manual_session,
        get_staged_change,
        set_staged_status,
        stage_change,
    )
    from .project_security import SensitiveProjectPathError

    try:
        session_id = get_or_create_manual_session(project_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    try:
        row = stage_change(session_id, _SAVE_RUN_ID, str(root), rel, content)
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    except ValueError as e:
        message = str(e)
        if "non-UTF-8" in message:
            return jsonify({"error": message, "code": "project_file_not_previewable"}), 415
        return jsonify({"error": message, "code": "invalid_path"}), 400

    # The editor's base must match the snapshot stage_change just took from
    # disk — this closes the load->save drift window. A leftover pending row
    # (crashed earlier save) carries a stale snapshot and lands here too,
    # self-healing on the user's retry.
    if row["base_hash"] != base_sha256:
        set_staged_status(row["id"], "rejected")
        return jsonify({
            "error": "conflict",
            "code": "save_conflict",
            "disk_sha256": row["base_hash"],
        }), 409

    result = apply_staged_change(row["id"])
    if result["status"] == "applied":
        applied = get_staged_change(row["id"])
        payload = applied["new_content"].encode("utf-8")
        return jsonify({
            "status": "applied",
            "change_id": row["id"],
            "path": row["path"],
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        })
    if result["status"] == "conflict":
        return jsonify({
            "error": "conflict",
            "code": "save_conflict",
            "disk_sha256": result.get("disk_hash"),
        }), 409
    return jsonify({"error": result.get("error") or result["status"], "code": "save_failed"}), 500
```

(`re` and `hashlib` are already imported at the top of `api.py` — verify, don't duplicate.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_file_save.py tests/test_manual_session.py tests/test_staged_changes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api.py tests/test_project_file_save.py
git commit -m "feat(api): edit-and-stage save route via manual-edits session"
```

---

### Task 3: web client wiring — save/revert/apply-all + minimal panel hookup

**Files:**
- Modify: `web/src/lib/api.ts:426-451` (after `rejectStagedChange`)
- Modify: `web/src/lib/types.ts:481-488` (after `StagedChangeActionResponse`)
- Modify: `web/src/lib/agent-workbench.ts` (`stagedActionFailure` action union)
- Modify: `web/src/pages/chat.tsx` (`revertChange` handler beside `rejectChange` `:1060`; `applyAllChanges` handler; `StagedChangesPanel` `:1685`)

**Interfaces:**
- Consumes: Task 2's route contract; existing endpoints `/api/agent/changes/<id>/revert` and `/api/agent/sessions/<sid>/changes/apply`.
- Produces (Plan 2 codes against these): `api.saveProjectFile(projectId, body: ProjectFileSaveRequest) -> ProjectFileSaveResponse`; `api.revertStagedChange(changeId)`; `api.applyAllStagedChanges(sessionId, ids?)` → `StagedBatchApplyResponse`.

- [ ] **Step 1: types.ts additions**

```typescript
export interface StagedBatchApplyResponse {
  applied: string[];
  conflicts: string[];
  errors: { id: string; error: string }[];
}

export interface ProjectFileSaveRequest {
  path: string;
  content: string;
  base_sha256: string | null;
}

export interface ProjectFileSaveResponse {
  status: "applied";
  change_id: string;
  path: string;
  sha256: string;
  size: number;
}

export interface ProjectFileSaveConflict {
  error: "conflict";
  code: "save_conflict";
  disk_sha256: string | null;
}
```

- [ ] **Step 2: api.ts additions** (after `rejectStagedChange`, same style)

```typescript
  revertStagedChange: (changeId: string) =>
    postJSON<import("./types").StagedChangeActionResponse>(
      `/api/agent/changes/${encodeURIComponent(changeId)}/revert`,
      {}
    ),
  applyAllStagedChanges: (sessionId: string, ids?: string[]) =>
    postJSON<import("./types").StagedBatchApplyResponse>(
      `/api/agent/sessions/${encodeURIComponent(sessionId)}/changes/apply`,
      ids ? { ids } : {}
    ),
  saveProjectFile: (projectId: string, body: import("./types").ProjectFileSaveRequest) =>
    postJSON<import("./types").ProjectFileSaveResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/file/save`,
      body
    ),
```

- [ ] **Step 3: agent-workbench.ts** — widen `stagedActionFailure`'s first-parameter union with `"revert" | "apply-all"` (match the file's existing union literal style; add matching title strings alongside the existing apply/reject copy, e.g. `"Could not revert staged change"` / `"Could not apply all staged changes"`).

- [ ] **Step 4: chat.tsx — `revertChange` + `applyAllChanges` handlers**

Add `revertChange` directly after `rejectChange` (`:1104`), mirroring its exact shape (identity guard → `window.confirm` → busyKey single-flight → api call → toast → `refreshStagedChanges` → re-fetch detail):

```typescript
  const revertChange = async (change: StagedChangeSummary) => {
    const identity = {
      sessionId: change.session_id,
      generation: sessionGenerationRef.current,
    };
    if (!isActiveSessionAction(identity)) return;
    const fullPath = stagedFullPath(change.root, change.path);
    if (!window.confirm(
      `Revert this applied change?\n\nPath: ${fullPath}\nRun: ${change.run_id}\n\nDisk is restored to the snapshot taken at staging.`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:revert:${change.id}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      await api.revertStagedChange(change.id);
      if (!isActiveSessionAction(identity)) return;
      toast.success("Applied change reverted", {
        description: `${fullPath}\nRun: ${change.run_id}`,
      });
      await refreshStagedChanges(change.session_id);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("revert", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not revert staged change", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(change.session_id, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };

  const applyAllChanges = async () => {
    const sid = sessionId;
    if (!sid) return;
    const identity = { sessionId: sid, generation: sessionGenerationRef.current };
    if (!isActiveSessionAction(identity)) return;
    const pendingCount = stagedChanges.filter((c) => c.status === "pending").length;
    if (!window.confirm(
      `Apply all ${pendingCount} pending staged changes to disk?\n\nConflicting changes are skipped and marked.`
    )) return;
    if (!isActiveSessionAction(identity)) return;
    const busyKey = `${identity.generation}:apply-all:${sid}`;
    if (changeBusyRef.current) return;
    changeBusyRef.current = busyKey;
    try {
      setChangeBusy(busyKey);
      const result = await api.applyAllStagedChanges(sid);
      if (!isActiveSessionAction(identity)) return;
      const parts = [`${result.applied.length} applied`];
      if (result.conflicts.length) parts.push(`${result.conflicts.length} conflicts`);
      if (result.errors.length) parts.push(`${result.errors.length} errors`);
      (result.conflicts.length || result.errors.length ? toast.warning : toast.success)(
        "Apply all finished", { description: parts.join(" · ") }
      );
      await refreshStagedChanges(sid);
    } catch (error) {
      if (isActiveSessionAction(identity)) {
        const failure = error instanceof ApiError
          ? stagedActionFailure("apply-all", error.status, error.body)
          : null;
        toast.error(failure?.title ?? "Could not apply staged changes", {
          description: failure?.description ?? (error instanceof Error ? error.message : String(error)),
        });
        await refreshStagedChanges(sid, true);
      }
    } finally {
      if (changeBusyRef.current === busyKey) {
        changeBusyRef.current = "";
        if (mountedRef.current) setChangeBusy("");
      }
    }
  };
```

Note: `sessionId` / `stagedChanges` are the existing state names in `Chat()` — verify at `:184-260` and adjust if the local names differ (e.g. `activeSessionId`).

- [ ] **Step 5: chat.tsx — panel wiring**

Extend the `StagedChangesPanel` props with `onRevert: (change: StagedChangeSummary) => void` and `onApplyAll: () => void`; pass `revertChange` / `applyAllChanges` at the call site. In the panel:

Header (inside the existing flex row, after the Badge):

```tsx
        {pending > 1 && (
          <Button size="sm" variant="ghost" onClick={onApplyAll}>
            Apply all
          </Button>
        )}
```

Per-row actions (after the existing pending-only block):

```tsx
                {change.status === "applied" && (
                  <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onRevert(change)}>
                    Revert
                  </Button>
                )}
```

- [ ] **Step 6: Gates**

Run in `web/`: `npm run typecheck` then `npm run build`
Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/types.ts web/src/lib/agent-workbench.ts web/src/pages/chat.tsx
git commit -m "feat(web): wire revert + apply-all + save client into the staged panel"
```

---

### Task 4: full verification + ledger

- [ ] **Step 1: Full non-live suite**

Run: `.venv\Scripts\python.exe -m pytest -m "not live" -q`
Expected: everything green except the ONE known pre-existing red on master: `tests/test_release_readiness.py::test_build_workflow_verifies_source_version_and_uploads_checksum` (documented pre-existing; NOT ours — do not fix, do not mask).

- [ ] **Step 2: Web gates once more**

Run in `web/`: `npm run typecheck` && `npm run build` — both exit 0.

- [ ] **Step 3: Ledger + commit**

Append a `# M2 depth plan 1 (2026-07-14)` section to `.superpowers/sdd/progress.md` recording task commits + test counts, then:

```bash
git add .superpowers/sdd/progress.md
git commit -m "docs(sdd): m2 depth plan 1 ledger"
```
