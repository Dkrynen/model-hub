import json
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


def list_sessions(workspace: str = "") -> list[dict]:
    conn = _ensure_db()
    ws = workspace or current_workspace()
    rows = conn.execute(
        "SELECT id, name, model, system_prompt, workspace, created_at, updated_at FROM sessions WHERE workspace = ? ORDER BY updated_at DESC",
        (ws,),
    ).fetchall()
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
    for msg in messages:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, msg.get("role", "user"), msg.get("content", ""), msg.get("timestamp", now)),
        )
    conn.commit()
    conn.close()


def delete_session(session_id: str) -> None:
    conn = _ensure_db()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
