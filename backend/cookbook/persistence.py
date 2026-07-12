import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from backend.project_paths import inspect_project_root, validate_relative_project_path
from backend.project_security import (
    SensitiveProjectPathError,
    read_project_text,
    resolve_project_path,
)

from .config import ensure_workspace, get_workspace
from .project_coordinator import WORKSPACE_PROJECT_LOCK


DB_DIR = Path.home() / ".model-hub"
DB_PATH = DB_DIR / "cookbook.db"


_MIGRATED = False
_SCHEMA_LOCK = threading.Lock()

MAX_PROJECT_NAME_CHARS = 120
MAX_PROJECT_DESCRIPTION_CHARS = 1000


class ProjectConflictError(ValueError):
    """A registered root conflicts with an existing project identity."""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            workspace   TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            root        TEXT NOT NULL,
            root_key    TEXT NOT NULL UNIQUE,
            root_dev    TEXT NOT NULL,
            root_ino    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'active' CHECK(status = 'active'),
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            UNIQUE(root_dev, root_ino)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            system_prompt TEXT DEFAULT '',
            context     TEXT DEFAULT '{}',
            workspace   TEXT NOT NULL DEFAULT 'default',
            project_id  TEXT REFERENCES projects(id) ON DELETE RESTRICT,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
        """
    )
    session_columns = _table_columns(conn, "sessions")
    if "workspace" not in session_columns:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN workspace TEXT NOT NULL DEFAULT 'default'"
        )
    if "project_id" not in session_columns:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
            content     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            metadata    TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            type        TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            timestamp   REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staged_changes (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id      TEXT NOT NULL,
            root        TEXT NOT NULL,
            path        TEXT NOT NULL,
            base_hash   TEXT,
            old_content TEXT,
            new_content TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_staged_session ON staged_changes(session_id, status)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_staged_pending_unique")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_pending_unique ON staged_changes(session_id, root, path) WHERE status = 'pending'"
    )


def _ensure_db():
    global _MIGRATED
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        with _SCHEMA_LOCK:
            if not _MIGRATED:
                conn.execute("PRAGMA journal_mode=WAL")
                with conn:
                    _migrate_schema(conn)
                _MIGRATED = True
        return conn
    except BaseException:
        conn.close()
        raise


def current_workspace() -> str:
    return ensure_workspace()


_PROJECT_COLUMNS = (
    "id, workspace, name, description, root, root_key, root_dev, root_ino, "
    "status, created_at, updated_at"
)


def _project_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "workspace": row[1],
        "name": row[2],
        "description": row[3],
        "root": row[4],
        "root_key": row[5],
        "root_dev": row[6],
        "root_ino": row[7],
        "status": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


def _validate_project_metadata(name: str, description: str) -> tuple[str, str]:
    if not isinstance(name, str) or not isinstance(description, str):
        raise ValueError("project name and description must be strings")
    if len(name) > MAX_PROJECT_NAME_CHARS:
        raise ValueError("project name must be between 1 and 120 characters")
    if len(description) > MAX_PROJECT_DESCRIPTION_CHARS:
        raise ValueError("project description must be at most 1000 characters")
    if any(
        ord(char) < 32 or ord(char) == 127 for char in name + description
    ):
        raise ValueError("project metadata cannot contain control characters")
    normalized_name = name.strip()
    normalized_description = description.strip()
    if not normalized_name:
        raise ValueError("project name must be between 1 and 120 characters")
    return normalized_name, normalized_description


def create_project(
    workspace: str,
    name: str,
    root: str,
    description: str = "",
) -> dict:
    """Register an existing directory without modifying the directory itself."""

    normalized_name, normalized_description = _validate_project_metadata(
        name, description
    )
    with WORKSPACE_PROJECT_LOCK:
        if (
            not isinstance(workspace, str)
            or not workspace
            or get_workspace(workspace) is None
        ):
            raise ValueError("workspace does not exist")
        identity = inspect_project_root(root, home=Path.home(), data_root=DB_DIR)
        project_id = uuid.uuid4().hex[:14]
        now = time.time()
        conn = _ensure_db()
        try:
            conn.execute(
                f"INSERT INTO projects ({_PROJECT_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    workspace,
                    normalized_name,
                    normalized_description,
                    str(identity.path),
                    identity.root_key,
                    identity.device,
                    identity.inode,
                    "active",
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            message = str(exc).casefold()
            if not (
                "unique constraint failed" in message
                and (
                    "projects.root_key" in message
                    or "projects.root_dev" in message
                    or "projects.root_ino" in message
                )
            ):
                raise
            raise ProjectConflictError(
                "project root is already registered"
            ) from exc
        finally:
            conn.close()
        project = get_project(project_id)
        if project is None:  # pragma: no cover - a committed row must be readable
            raise RuntimeError("registered project could not be reloaded")
        return project


def list_projects(workspace: str) -> list[dict]:
    conn = _ensure_db()
    try:
        rows = conn.execute(
            f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE workspace = ? ORDER BY created_at ASC, rowid ASC",
            (workspace,),
        ).fetchall()
        return [_project_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def get_project(project_id: str) -> Optional[dict]:
    conn = _ensure_db()
    try:
        row = conn.execute(
            f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        return _project_row_to_dict(row) if row else None
    finally:
        conn.close()


def revalidate_project_root(project_or_id: dict | str) -> Path:
    project = (
        get_project(project_or_id)
        if isinstance(project_or_id, str)
        else project_or_id
    )
    if not isinstance(project, dict):
        raise ValueError("project does not exist")
    try:
        stored_root = str(project["root"])
        stored_key = str(project["root_key"])
        stored_dev = str(project["root_dev"])
        stored_ino = str(project["root_ino"])
    except (KeyError, TypeError) as exc:
        raise ValueError("project record is invalid") from exc

    identity = inspect_project_root(
        stored_root,
        home=Path.home(),
        data_root=DB_DIR,
    )
    if (
        str(identity.path) != stored_root
        or identity.root_key != stored_key
        or identity.device != stored_dev
        or identity.inode != stored_ino
    ):
        raise ValueError("project root identity changed (drift detected)")
    return identity.path


def _resolve_new_session_identity(
    workspace: str,
    project_id: str | None,
) -> tuple[str, str | None]:
    normalized_project_id = project_id or None
    if normalized_project_id is None:
        return workspace or current_workspace(), None
    project = get_project(normalized_project_id)
    if project is None:
        raise ValueError("project does not exist")
    project_workspace = str(project["workspace"])
    if workspace and workspace != project_workspace:
        raise ValueError("workspace does not match project")
    return project_workspace, normalized_project_id


def create_session(
    name: str = "",
    model: str = "",
    system_prompt: str = "",
    workspace: str = "",
    project_id: str | None = None,
) -> str:
    conn = _ensure_db()
    session_id = uuid.uuid4().hex[:14]
    now = time.time()
    try:
        ws, bound_project_id = _resolve_new_session_identity(workspace, project_id)
        conn.execute(
            "INSERT INTO sessions (id, name, model, system_prompt, context, workspace, project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                name,
                model,
                system_prompt,
                "{}",
                ws,
                bound_project_id,
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return session_id


def list_sessions(
    workspace: str = "",
    limit: int | None = None,
    project_id: str | None = None,
) -> list[dict]:
    conn = _ensure_db()
    try:
        params: list = []
        where: list[str] = []
        if project_id and project_id != "unassigned":
            project = get_project(project_id)
            if project is None:
                raise ValueError("project does not exist")
            project_workspace = str(project["workspace"])
            if workspace and workspace != project_workspace:
                raise ValueError("workspace does not match project")
            workspace = project_workspace
            where.append("project_id = ?")
            params.append(project_id)
        elif project_id == "unassigned":
            where.append("project_id IS NULL")

        ws = workspace or current_workspace()
        where.insert(0, "workspace = ?")
        params.insert(0, ws)
        sql = (
            "SELECT id, name, model, system_prompt, workspace, project_id, "
            "created_at, updated_at FROM sessions WHERE "
            + " AND ".join(where)
            + " ORDER BY updated_at DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "model": r[2],
                "system_prompt": r[3],
                "workspace": r[4],
                "project_id": r[5],
                "created_at": r[6],
                "updated_at": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_session(session_id: str) -> Optional[dict]:
    conn = _ensure_db()
    row = conn.execute(
        "SELECT id, name, model, system_prompt, context, workspace, project_id, created_at, updated_at FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    messages = conn.execute(
        "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
        (session_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT id, type, payload, timestamp FROM session_events WHERE session_id = ? ORDER BY timestamp ASC, id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return {
        "id": row[0],
        "name": row[1],
        "model": row[2],
        "system_prompt": row[3],
        "context": row[4],
        "workspace": row[5],
        "project_id": row[6],
        "created_at": row[7],
        "updated_at": row[8],
        "messages": [{"role": m[0], "content": m[1], "timestamp": m[2]} for m in messages],
        "events": [
            {
                "id": e[0],
                "type": e[1],
                "payload": json.loads(e[2] or "{}"),
                "timestamp": e[3],
            }
            for e in events
        ],
    }


def save_session(
    session_id: str,
    model: str,
    messages: list[dict],
    name: str = "",
    workspace: str = "",
    project_id: str | None = None,
    create_if_missing: bool = True,
) -> bool:
    conn = _ensure_db()
    now = time.time()
    try:
        existing = conn.execute(
            "SELECT workspace, project_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not existing:
            if not create_if_missing:
                return False
            ws, bound_project_id = _resolve_new_session_identity(workspace, project_id)
            conn.execute(
                "INSERT INTO sessions (id, name, model, system_prompt, context, workspace, project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    name,
                    model,
                    "",
                    "{}",
                    ws,
                    bound_project_id,
                    now,
                    now,
                ),
            )
        else:
            saved_workspace, saved_project_id = existing
            if workspace and workspace != saved_workspace:
                raise ValueError("workspace cannot change for an existing session")
            if project_id is not None and (project_id or None) != saved_project_id:
                raise ValueError("project cannot change for an existing session")
            conn.execute(
                "UPDATE sessions SET name = ?, model = ?, updated_at = ? WHERE id = ?",
                (name, model, now, session_id),
            )

        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        for idx, msg in enumerate(messages):
            timestamp = msg.get("timestamp", now)
            if not isinstance(timestamp, (int, float)):
                timestamp = now + (idx * 0.000001)
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    msg.get("role", "user"),
                    msg.get("content", ""),
                    timestamp,
                ),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_session(session_id: str) -> None:
    conn = _ensure_db()
    conn.execute("DELETE FROM staged_changes WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


def add_session_event(session_id: str, event_type: str, payload: dict) -> int:
    conn = _ensure_db()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO session_events (session_id, type, payload, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, event_type, json.dumps(payload or {}), now),
    )
    conn.commit()
    event_id = int(cur.lastrowid)
    conn.close()
    return event_id


def list_session_events(session_id: str) -> list[dict]:
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT id, type, payload, timestamp FROM session_events WHERE session_id = ? ORDER BY timestamp ASC, id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "payload": json.loads(r[2] or "{}"),
            "timestamp": r[3],
        }
        for r in rows
    ]


_STAGED_COLUMNS = "id, session_id, run_id, root, path, base_hash, old_content, new_content, status, created_at, updated_at"


def _staged_row_to_dict(r: tuple) -> dict:
    return {
        "id": r[0],
        "session_id": r[1],
        "run_id": r[2],
        "root": r[3],
        "path": r[4],
        "base_hash": r[5],
        "old_content": r[6],
        "new_content": r[7],
        "status": r[8],
        "created_at": r[9],
        "updated_at": r[10],
    }


def _bound_project_root_for_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    allow_missing: bool = False,
) -> Path | None:
    session = conn.execute(
        "SELECT workspace, project_id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        if allow_missing:
            return None
        raise ValueError("session does not exist")
    session_workspace, project_id = session
    if project_id is None:
        return None
    row = conn.execute(
        f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise ValueError("session project does not exist")
    project = _project_row_to_dict(row)
    if str(session_workspace) != str(project["workspace"]):
        raise ValueError("session workspace does not match its project")
    return revalidate_project_root(project)


def _assert_staged_root_matches_session(
    conn: sqlite3.Connection,
    session_id: str,
    root: str,
) -> Path | None:
    project_root = _bound_project_root_for_session(conn, session_id)
    if project_root is not None and root != str(project_root):
        raise ValueError("staged change does not match the session project root")
    return project_root


def _resolve_staged_target_for_session(
    conn: sqlite3.Connection,
    session_id: str,
    root: str,
    path: str,
) -> Path:
    """Resolve one staged path under the session's current project boundary."""

    project_root = _assert_staged_root_matches_session(conn, session_id, root)
    if project_root is None:
        return _resolve_staged_target(root, path)
    target, relative = resolve_project_path(project_root, path)
    if relative != path:
        raise ValueError("staged change path is not canonical")
    return target


def _resolve_staged_target(root: str, path: str) -> Path:
    """Re-jail a staged path under its recorded root. Raises ValueError on escape."""
    base = Path(root).resolve()
    rel_path = validate_relative_project_path(path)
    target = base.joinpath(*rel_path.split("/")).resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        raise ValueError(f"path escapes project root: {path!r}")
    if str(rel) == ".":
        raise ValueError("path is the project root itself")
    return target


def stage_change(session_id: str, run_id: str, root: str, path: str, new_content: str) -> dict:
    """Upsert the session's pending row for this path (latest-wins, original snapshot kept)."""
    conn = _ensure_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        resolved_requested_root = str(Path(root).resolve())
        project_root = _assert_staged_root_matches_session(
            conn, session_id, resolved_requested_root
        )
        base = project_root or Path(resolved_requested_root)
        target = _resolve_staged_target_for_session(
            conn, session_id, str(base), path
        )
        rel = target.relative_to(base).as_posix()
        now = time.time()
        row = conn.execute(
            "SELECT id FROM staged_changes WHERE session_id = ? AND root = ? AND path = ? AND status = 'pending'",
            (session_id, str(base), rel),
        ).fetchone()
        if row:
            change_id = row[0]
            conn.execute(
                "UPDATE staged_changes SET new_content = ?, run_id = ?, updated_at = ? WHERE id = ?",
                (new_content, run_id, now, change_id),
            )
        else:
            change_id = uuid.uuid4().hex[:14]
            if project_root is not None:
                try:
                    read_relative, old_content = read_project_text(
                        project_root, rel
                    )
                except SensitiveProjectPathError as exc:
                    if exc.code != "project_file_not_found":
                        raise
                    # A missing file is a valid create only after a fresh
                    # no-link check closes the resolve/read race.
                    target, read_relative = resolve_project_path(
                        project_root, rel
                    )
                    base_hash = None
                    old_content = None
                else:
                    if read_relative != rel:
                        raise ValueError("staged change path is not canonical")
                    data = old_content.encode("utf-8")
                    base_hash = hashlib.sha256(data).hexdigest()
            elif target.exists() and target.is_file():
                data = target.read_bytes()
                base_hash = hashlib.sha256(data).hexdigest()
                try:
                    old_content = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        f"cannot stage changes to a non-UTF-8 file: {path}"
                    ) from exc
            else:
                base_hash = None
                old_content = None
            conn.execute(
                f"INSERT INTO staged_changes ({_STAGED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    change_id,
                    session_id,
                    run_id,
                    str(base),
                    rel,
                    base_hash,
                    old_content,
                    new_content,
                    "pending",
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_staged_change(change_id)


def list_staged_changes(session_id: str, run_id: str | None = None, status: str | None = None) -> list[dict]:
    conn = _ensure_db()
    try:
        project_root = _bound_project_root_for_session(
            conn, session_id, allow_missing=True
        )
        sql = f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE session_id = ?"
        params: list = [session_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        # rowid tie-break: time.time() can collide on Windows; insertion order must hold
        sql += " ORDER BY created_at ASC, rowid ASC"
        rows = conn.execute(sql, params).fetchall()
        result = [_staged_row_to_dict(r) for r in rows]
        for row in result:
            if project_root is not None:
                _resolve_staged_target_for_session(
                    conn, session_id, row["root"], row["path"]
                )
        return result
    finally:
        conn.close()


def list_staged_changes_for_root_bounded(
    session_id: str,
    root: str,
    *,
    status: str = "pending",
    max_rows: int,
    max_content_bytes: int,
    should_abort: Callable[[], bool] | None = None,
) -> tuple[list[dict], int, int]:
    """Read one exact-root overlay only after DB-side count/byte bounds."""

    conn = _ensure_db()
    if should_abort is not None:
        conn.set_progress_handler(lambda: 1 if should_abort() else 0, 1000)
    try:
        requested_root = str(Path(root).resolve())
        project_root = _assert_staged_root_matches_session(
            conn, session_id, requested_root
        )
        exact_root = str(project_root) if project_root is not None else requested_root
        conn.execute("BEGIN")
        if project_root is not None:
            mismatched = conn.execute(
                """
                SELECT root
                FROM staged_changes
                WHERE session_id = ? AND status = ? AND root <> ?
                LIMIT 1
                """,
                (session_id, status, exact_root),
            ).fetchone()
            if mismatched is not None:
                _assert_staged_root_matches_session(
                    conn, session_id, str(mismatched[0])
                )
        count, content_bytes = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(new_content AS BLOB))), 0)
            FROM staged_changes
            WHERE session_id = ? AND root = ? AND status = ?
            """,
            (session_id, exact_root, status),
        ).fetchone()
        count = int(count or 0)
        content_bytes = int(content_bytes or 0)
        if count > max_rows or content_bytes > max_content_bytes:
            return [], count, content_bytes
        rows = conn.execute(
            f"""
            SELECT {_STAGED_COLUMNS}
            FROM staged_changes
            WHERE session_id = ? AND root = ? AND status = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (session_id, exact_root, status, max_rows + 1),
        ).fetchall()
        result = [_staged_row_to_dict(row) for row in rows]
        if project_root is not None:
            for row in result:
                _resolve_staged_target_for_session(
                    conn, session_id, row["root"], row["path"]
                )
        return result, count, content_bytes
    except sqlite3.OperationalError as exc:
        if should_abort is not None and should_abort():
            raise InterruptedError("staged change query cancelled") from exc
        raise
    finally:
        conn.close()


def get_staged_change(change_id: str) -> Optional[dict]:
    conn = _ensure_db()
    try:
        row = conn.execute(
            f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE id = ?", (change_id,)
        ).fetchone()
        if row is None:
            return None
        result = _staged_row_to_dict(row)
        project_root = _bound_project_root_for_session(
            conn, result["session_id"]
        )
        if project_root is not None:
            _resolve_staged_target_for_session(
                conn, result["session_id"], result["root"], result["path"]
            )
        return result
    finally:
        conn.close()


def set_staged_status(change_id: str, status: str) -> None:
    conn = _ensure_db()
    try:
        expected_status = {
            "rejected": "pending",
            "conflict": "pending",
            "applied": "pending",
            "reverted": "applied",
        }.get(status)
        if expected_status is None:
            raise ValueError("invalid staged change status transition")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE id = ?", (change_id,)
        ).fetchone()
        if row is not None:
            current = _staged_row_to_dict(row)
            project_root = _bound_project_root_for_session(
                conn, current["session_id"]
            )
            if project_root is not None:
                _resolve_staged_target_for_session(
                    conn,
                    current["session_id"],
                    current["root"],
                    current["path"],
                )
            if current["status"] != expected_status:
                raise ValueError(
                    f"staged change is no longer {expected_status}"
                )
            _compare_and_set_staged_status(
                conn, current, status
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _atomic_write(
    target: Path,
    data: bytes,
    *,
    revalidate_target: Callable[[], Path] | None = None,
) -> None:
    """Write via a sibling tmp file + os.replace so a crash never leaves a partial file."""
    if revalidate_target is not None and revalidate_target() != target:
        raise ValueError("staged change target changed before directory creation")
    target.parent.mkdir(parents=True, exist_ok=True)
    if revalidate_target is not None and revalidate_target() != target:
        raise ValueError("staged change target changed before write")
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.lac-tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= int(getattr(os, "O_BINARY", 0))
    fd: int | None = None
    try:
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if revalidate_target is not None and revalidate_target() != target:
            raise ValueError("staged change target changed before replace")
        os.replace(tmp, target)
    except (OSError, ValueError):
        if fd is not None:
            os.close(fd)
        tmp.unlink(missing_ok=True)
        raise


def _load_staged_change(conn: sqlite3.Connection, change_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()
    return _staged_row_to_dict(row) if row is not None else None


def _session_has_project_binding(
    conn: sqlite3.Connection,
    session_id: str,
) -> bool:
    row = conn.execute(
        "SELECT project_id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row is not None and row[0] is not None


def _revalidate_staged_change_target(
    conn: sqlite3.Connection,
    expected: dict,
) -> Path:
    """Re-read the complete mutation identity before every disk operation."""

    current = _load_staged_change(conn, expected["id"])
    if current != expected:
        raise ValueError("staged change changed before disk mutation")
    return _resolve_staged_target_for_session(
        conn,
        expected["session_id"],
        expected["root"],
        expected["path"],
    )


def _compare_and_set_staged_status(
    conn: sqlite3.Connection,
    expected: dict,
    status: str,
) -> None:
    """Transition only the exact row revision that the caller inspected."""

    updated = conn.execute(
        """
        UPDATE staged_changes
        SET status = ?, updated_at = ?
        WHERE id IS ?
          AND session_id IS ?
          AND run_id IS ?
          AND root IS ?
          AND path IS ?
          AND base_hash IS ?
          AND old_content IS ?
          AND new_content IS ?
          AND status IS ?
          AND created_at IS ?
          AND updated_at IS ?
        """,
        (
            status,
            time.time(),
            expected["id"],
            expected["session_id"],
            expected["run_id"],
            expected["root"],
            expected["path"],
            expected["base_hash"],
            expected["old_content"],
            expected["new_content"],
            expected["status"],
            expected["created_at"],
            expected["updated_at"],
        ),
    )
    if updated.rowcount != 1:
        raise ValueError("staged change changed before status transition")


def _disk_hash(target: Path) -> Optional[str]:
    if target.exists() and target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    return None


def apply_staged_change(change_id: str) -> dict:
    """The only disk-write path for staged changes: re-jail, conflict-check, atomic write."""
    conn = _ensure_db()
    project_bound = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _load_staged_change(conn, change_id)
        if row is None:
            conn.rollback()
            return {"status": "not_found"}
        project_bound = _session_has_project_binding(conn, row["session_id"])
        target = _revalidate_staged_change_target(conn, row)
        if row["status"] != "pending":
            conn.rollback()
            return {"status": "not_pending", "current": row["status"]}
        disk_hash = _disk_hash(target)
        if disk_hash != row["base_hash"]:
            _compare_and_set_staged_status(conn, row, "conflict")
            conn.commit()
            return {
                "status": "conflict",
                "disk_hash": disk_hash,
                "base_hash": row["base_hash"],
            }
        target = _revalidate_staged_change_target(conn, row)
        _atomic_write(
            target,
            row["new_content"].encode("utf-8"),
            revalidate_target=lambda: _revalidate_staged_change_target(conn, row),
        )
        _compare_and_set_staged_status(conn, row, "applied")
        conn.commit()
    except (OSError, ValueError) as e:
        conn.rollback()
        if project_bound and isinstance(e, ValueError):
            raise
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()
    return {"status": "applied", "path": row["path"]}


def revert_applied_change(change_id: str) -> dict:
    """Undo an applied change from the retained snapshot, guarded by a hash of what apply wrote."""
    conn = _ensure_db()
    project_bound = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _load_staged_change(conn, change_id)
        if row is None:
            conn.rollback()
            return {"status": "not_found"}
        project_bound = _session_has_project_binding(conn, row["session_id"])
        target = _revalidate_staged_change_target(conn, row)
        if row["status"] != "applied":
            conn.rollback()
            return {"status": "not_applied", "current": row["status"]}
        expected_hash = hashlib.sha256(
            row["new_content"].encode("utf-8")
        ).hexdigest()
        disk_hash = _disk_hash(target)
        if disk_hash != expected_hash:
            conn.rollback()
            return {
                "status": "conflict",
                "disk_hash": disk_hash,
                "expected_hash": expected_hash,
            }
        target = _revalidate_staged_change_target(conn, row)
        if row["base_hash"] is None:
            target.unlink(missing_ok=True)
        else:
            _atomic_write(
                target,
                (row["old_content"] or "").encode("utf-8"),
                revalidate_target=lambda: _revalidate_staged_change_target(conn, row),
            )
        _compare_and_set_staged_status(conn, row, "reverted")
        conn.commit()
    except (OSError, ValueError) as e:
        conn.rollback()
        if project_bound and isinstance(e, ValueError):
            raise
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()
    return {"status": "reverted", "path": row["path"]}
