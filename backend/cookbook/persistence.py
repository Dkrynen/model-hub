import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from .config import ensure_workspace


DB_DIR = Path.home() / ".model-hub"
DB_PATH = DB_DIR / "cookbook.db"


_MIGRATED = False


def _ensure_db():
    global _MIGRATED
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            system_prompt TEXT DEFAULT '',
            context     TEXT DEFAULT '{}',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
            content     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            metadata    TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            type        TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            timestamp   REAL NOT NULL
        )
    """)
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_staged_session ON staged_changes(session_id, status)"
    )
    if not _MIGRATED:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace TEXT NOT NULL DEFAULT 'default'")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, timestamp)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("DROP INDEX IF EXISTS idx_staged_pending_unique")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_pending_unique ON staged_changes(session_id, root, path) WHERE status = 'pending'"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        _MIGRATED = True
    return conn


def current_workspace() -> str:
    return ensure_workspace()


def create_session(name: str = "", model: str = "", system_prompt: str = "", workspace: str = "") -> str:
    conn = _ensure_db()
    session_id = uuid.uuid4().hex[:14]
    now = time.time()
    ws = workspace or current_workspace()
    conn.execute(
        "INSERT INTO sessions (id, name, model, system_prompt, context, workspace, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, name, model, system_prompt, "{}", ws, now, now),
    )
    conn.commit()
    conn.close()
    return session_id


def list_sessions(workspace: str = "", limit: int | None = None) -> list[dict]:
    conn = _ensure_db()
    ws = workspace or current_workspace()
    sql = "SELECT id, name, model, system_prompt, workspace, created_at, updated_at FROM sessions WHERE workspace = ? ORDER BY updated_at DESC"
    params: tuple = (ws,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (ws, max(1, int(limit)))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "model": r[2],
            "system_prompt": r[3],
            "workspace": r[4],
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]


def get_session(session_id: str) -> Optional[dict]:
    conn = _ensure_db()
    row = conn.execute(
        "SELECT id, name, model, system_prompt, context, workspace, created_at, updated_at FROM sessions WHERE id = ?",
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
        "created_at": row[6],
        "updated_at": row[7],
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


def save_session(session_id: str, model: str, messages: list[dict], name: str = "", workspace: str = "") -> None:
    conn = _ensure_db()
    now = time.time()
    ws = workspace or current_workspace()

    existing = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO sessions (id, name, model, system_prompt, context, workspace, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, name, model, "", "{}", ws, now, now),
        )
    else:
        conn.execute(
            "UPDATE sessions SET name = ?, model = ?, workspace = ?, updated_at = ? WHERE id = ?",
            (name, model, ws, now, session_id),
        )

    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    for idx, msg in enumerate(messages):
        timestamp = msg.get("timestamp", now)
        if not isinstance(timestamp, (int, float)):
            timestamp = now + (idx * 0.000001)
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, msg.get("role", "user"), msg.get("content", ""), timestamp),
        )
    conn.commit()
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


def _resolve_staged_target(root: str, path: str) -> Path:
    """Re-jail a staged path under its recorded root. Raises ValueError on escape."""
    base = Path(root).resolve()
    target = (base / path).resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        raise ValueError(f"path escapes project root: {path!r}")
    if str(rel) == ".":
        raise ValueError("path is the project root itself")
    return target


def stage_change(session_id: str, run_id: str, root: str, path: str, new_content: str) -> dict:
    """Upsert the session's pending row for this path (latest-wins, original snapshot kept)."""
    base = Path(root).resolve()
    target = _resolve_staged_target(root, path)
    rel = target.relative_to(base).as_posix()
    conn = _ensure_db()
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
        if target.exists() and target.is_file():
            data = target.read_bytes()
            base_hash = hashlib.sha256(data).hexdigest()
            try:
                old_content = data.decode("utf-8")
            except UnicodeDecodeError:
                conn.close()
                raise ValueError(f"cannot stage changes to a non-UTF-8 file: {path}")
        else:
            base_hash = None
            old_content = None
        conn.execute(
            f"INSERT INTO staged_changes ({_STAGED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (change_id, session_id, run_id, str(base), rel, base_hash, old_content, new_content, "pending", now, now),
        )
    conn.commit()
    conn.close()
    return get_staged_change(change_id)


def list_staged_changes(session_id: str, run_id: str | None = None, status: str | None = None) -> list[dict]:
    conn = _ensure_db()
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
    conn.close()
    return [_staged_row_to_dict(r) for r in rows]


def get_staged_change(change_id: str) -> Optional[dict]:
    conn = _ensure_db()
    row = conn.execute(
        f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()
    conn.close()
    return _staged_row_to_dict(row) if row else None


def set_staged_status(change_id: str, status: str) -> None:
    conn = _ensure_db()
    conn.execute(
        "UPDATE staged_changes SET status = ?, updated_at = ? WHERE id = ?",
        (status, time.time(), change_id),
    )
    conn.commit()
    conn.close()


def _atomic_write(target: Path, data: bytes) -> None:
    """Write via a sibling tmp file + os.replace so a crash never leaves a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.lac-tmp"
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _disk_hash(target: Path) -> Optional[str]:
    if target.exists() and target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    return None


def apply_staged_change(change_id: str) -> dict:
    """The only disk-write path for staged changes: re-jail, conflict-check, atomic write."""
    row = get_staged_change(change_id)
    if row is None:
        return {"status": "not_found"}
    if row["status"] != "pending":
        return {"status": "not_pending", "current": row["status"]}
    try:
        target = _resolve_staged_target(row["root"], row["path"])
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    disk_hash = _disk_hash(target)
    if disk_hash != row["base_hash"]:
        set_staged_status(change_id, "conflict")
        return {"status": "conflict", "disk_hash": disk_hash, "base_hash": row["base_hash"]}
    _atomic_write(target, row["new_content"].encode("utf-8"))
    set_staged_status(change_id, "applied")
    return {"status": "applied", "path": row["path"]}


def revert_applied_change(change_id: str) -> dict:
    """Undo an applied change from the retained snapshot, guarded by a hash of what apply wrote."""
    row = get_staged_change(change_id)
    if row is None:
        return {"status": "not_found"}
    if row["status"] != "applied":
        return {"status": "not_applied", "current": row["status"]}
    try:
        target = _resolve_staged_target(row["root"], row["path"])
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    expected = hashlib.sha256(row["new_content"].encode("utf-8")).hexdigest()
    disk_hash = _disk_hash(target)
    if disk_hash != expected:
        return {"status": "conflict", "disk_hash": disk_hash, "expected_hash": expected}
    if row["base_hash"] is None:
        target.unlink(missing_ok=True)
    else:
        _atomic_write(target, (row["old_content"] or "").encode("utf-8"))
    set_staged_status(change_id, "reverted")
    return {"status": "reverted", "path": row["path"]}
