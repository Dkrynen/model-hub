from __future__ import annotations

import sqlite3
import time

import pytest

from backend.cookbook import persistence


def test_create_and_get_session(isolated_home):
    sid = persistence.create_session(name="t", model="llama3.2:3b")
    assert sid and len(sid) == 14
    data = persistence.get_session(sid)
    assert data is not None
    assert data["model"] == "llama3.2:3b"
    assert data["messages"] == []


def test_save_and_list_messages(isolated_home):
    sid = persistence.create_session(model="m")
    msgs = [
        {"role": "user", "content": "hi", "timestamp": time.time()},
        {"role": "assistant", "content": "hello", "timestamp": time.time()},
    ]
    persistence.save_session(sid, model="m", messages=msgs)
    data = persistence.get_session(sid)
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "hi"
    assert data["messages"][1]["role"] == "assistant"


def test_list_sessions(isolated_home):
    a = persistence.create_session(name="a", model="m1")
    b = persistence.create_session(name="b", model="m2")
    sessions = persistence.list_sessions()
    ids = [s["id"] for s in sessions]
    assert a in ids and b in ids
    assert sessions[0]["updated_at"] >= sessions[-1]["updated_at"]


def test_list_sessions_limit_returns_recent_rows(isolated_home):
    a = persistence.create_session(name="a", model="m1")
    time.sleep(0.001)
    b = persistence.create_session(name="b", model="m2")
    time.sleep(0.001)
    c = persistence.create_session(name="c", model="m3")

    sessions = persistence.list_sessions(limit=2)

    assert [s["id"] for s in sessions] == [c, b]
    assert a not in [s["id"] for s in sessions]


def test_delete_session(isolated_home):
    sid = persistence.create_session(model="m")
    persistence.save_session(sid, model="m", messages=[{"role": "user", "content": "x"}])
    persistence.delete_session(sid)
    assert persistence.get_session(sid) is None


def test_save_upsert_new_session(isolated_home):
    sid = "fixed12345abc"
    persistence.save_session(sid, model="m", messages=[{"role": "user", "content": "y"}])
    data = persistence.get_session(sid)
    assert data is not None
    assert data["model"] == "m"


def test_save_can_refuse_to_resurrect_a_missing_session(isolated_home):
    saved = persistence.save_session(
        "deleted-session",
        model="m",
        messages=[{"role": "assistant", "content": "late"}],
        create_if_missing=False,
    )

    assert saved is False
    assert persistence.get_session("deleted-session") is None


def test_get_missing_session_returns_none(isolated_home):
    assert persistence.get_session("doesnotexist") is None


def test_session_events_are_persisted_separately(isolated_home):
    sid = persistence.create_session(model="m")
    event_id = persistence.add_session_event(
        sid,
        "tool_result",
        {"name": "list_files", "ok": True, "result": "f api.py"},
    )

    events = persistence.list_session_events(sid)
    session = persistence.get_session(sid)

    assert event_id > 0
    assert events[0]["type"] == "tool_result"
    assert events[0]["payload"]["name"] == "list_files"
    assert session["events"][0]["payload"]["result"] == "f api.py"


def test_schema_migration_is_idempotent_and_enables_foreign_keys(isolated_home):
    conn = sqlite3.connect(str(persistence.DB_PATH))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            system_prompt TEXT DEFAULT '',
            context TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, created_at, updated_at) VALUES ('legacy', 1, 1)"
    )
    conn.commit()
    conn.close()

    first = persistence._ensure_db()
    first_columns = {
        row[1] for row in first.execute("PRAGMA table_info(sessions)").fetchall()
    }
    assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    first.close()

    persistence._MIGRATED = False
    second = persistence._ensure_db()
    second_columns = {
        row[1] for row in second.execute("PRAGMA table_info(sessions)").fetchall()
    }
    project_columns = {
        row[1] for row in second.execute("PRAGMA table_info(projects)").fetchall()
    }
    assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    second.close()

    assert first_columns == second_columns
    assert {"workspace", "project_id"} <= second_columns
    assert {
        "id",
        "workspace",
        "name",
        "description",
        "root",
        "root_key",
        "root_dev",
        "root_ino",
        "status",
        "created_at",
        "updated_at",
    } <= project_columns
    assert persistence.get_session("legacy")["project_id"] is None


def test_schema_migration_preserves_plan4_rows(isolated_home):
    conn = sqlite3.connect(str(persistence.DB_PATH))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            system_prompt TEXT DEFAULT '',
            context TEXT DEFAULT '{}',
            workspace TEXT NOT NULL DEFAULT 'default',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        );
        CREATE TABLE session_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            timestamp REAL NOT NULL
        );
        CREATE TABLE staged_changes (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            root TEXT NOT NULL,
            path TEXT NOT NULL,
            base_hash TEXT,
            old_content TEXT,
            new_content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_sessions_workspace ON sessions(workspace);
        CREATE INDEX idx_session_events_session
            ON session_events(session_id, timestamp);
        CREATE INDEX idx_staged_session
            ON staged_changes(session_id, status);
        CREATE UNIQUE INDEX idx_staged_pending_unique
            ON staged_changes(session_id, root, path)
            WHERE status = 'pending';
        """
    )
    conn.execute(
        """
        INSERT INTO sessions
            (id, name, model, system_prompt, context, workspace, created_at, updated_at)
        VALUES ('plan4', 'Plan 4 thread', 'model:1b', 'system', '{"kept":true}',
                'client-a', 10, 20)
        """
    )
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, timestamp, metadata)
        VALUES ('plan4', 'user', 'keep the message', 11, '{"source":"legacy"}')
        """
    )
    conn.execute(
        """
        INSERT INTO session_events (session_id, type, payload, timestamp)
        VALUES ('plan4', 'tool_result', '{"result":"kept"}', 12)
        """
    )
    conn.execute(
        """
        INSERT INTO staged_changes
            (id, session_id, run_id, root, path, base_hash, old_content,
             new_content, status, created_at, updated_at)
        VALUES ('plan4-change', 'plan4', 'run-4', 'C:/legacy-project',
                'src/kept.py', NULL, NULL, 'new content', 'pending', 13, 14)
        """
    )
    conn.commit()
    conn.close()

    first = persistence._ensure_db()
    assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert "project_id" in {
        row[1] for row in first.execute("PRAGMA table_info(sessions)").fetchall()
    }
    first.close()

    persistence._MIGRATED = False
    second = persistence._ensure_db()
    assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    second.close()

    session = persistence.get_session("plan4")
    assert session is not None
    assert session["name"] == "Plan 4 thread"
    assert session["model"] == "model:1b"
    assert session["system_prompt"] == "system"
    assert session["context"] == '{"kept":true}'
    assert session["workspace"] == "client-a"
    assert session["project_id"] is None
    assert session["messages"] == [
        {"role": "user", "content": "keep the message", "timestamp": 11.0}
    ]
    assert session["events"][0]["type"] == "tool_result"
    assert session["events"][0]["payload"] == {"result": "kept"}

    raw = persistence._ensure_db()
    try:
        assert raw.execute(
            "SELECT metadata FROM messages WHERE session_id = 'plan4'"
        ).fetchone()[0] == '{"source":"legacy"}'
    finally:
        raw.close()

    staged = persistence.list_staged_changes("plan4")
    assert len(staged) == 1
    assert staged[0]["id"] == "plan4-change"
    assert staged[0]["run_id"] == "run-4"
    assert staged[0]["root"] == "C:/legacy-project"
    assert staged[0]["path"] == "src/kept.py"
    assert staged[0]["new_content"] == "new content"
    assert staged[0]["status"] == "pending"


def test_schema_migration_does_not_swallow_unrelated_errors(
    isolated_home, monkeypatch
):
    def fail_migration(_conn):
        raise sqlite3.DatabaseError("synthetic migration failure")

    monkeypatch.setattr(persistence, "_migrate_schema", fail_migration)

    with pytest.raises(sqlite3.DatabaseError, match="synthetic migration failure"):
        persistence._ensure_db()
    assert persistence._MIGRATED is False


def test_project_bound_session_derives_workspace_and_lists_by_project(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    project = persistence.create_project("default", "Project", str(root))

    bound = persistence.create_session(model="m", project_id=project["id"])
    legacy = persistence.create_session(model="m", workspace="default")
    session = persistence.get_session(bound)

    assert session["workspace"] == "default"
    assert session["project_id"] == project["id"]
    assert [row["id"] for row in persistence.list_sessions(project_id=project["id"])] == [
        bound
    ]
    assert [row["id"] for row in persistence.list_sessions(project_id="unassigned")] == [
        legacy
    ]


def test_project_bound_session_rejects_mismatched_workspace_and_cannot_move(
    isolated_home, tmp_path
):
    from backend.cookbook.config import create_workspace, ensure_workspace

    ensure_workspace()
    create_workspace("Other")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    root_a.mkdir()
    root_b.mkdir()
    project_a = persistence.create_project("default", "A", str(root_a))
    project_b = persistence.create_project("other", "B", str(root_b))

    with pytest.raises(ValueError, match="workspace"):
        persistence.create_session(
            model="m", workspace="other", project_id=project_a["id"]
        )

    sid = persistence.create_session(model="m", project_id=project_a["id"])
    persistence.save_session(
        sid,
        model="m2",
        messages=[{"role": "user", "content": "kept"}],
    )
    with pytest.raises(ValueError, match="workspace"):
        persistence.save_session(sid, model="m", messages=[], workspace="other")
    with pytest.raises(ValueError, match="project"):
        persistence.save_session(
            sid, model="m", messages=[], project_id=project_b["id"]
        )

    session = persistence.get_session(sid)
    assert session["workspace"] == "default"
    assert session["project_id"] == project_a["id"]
    assert session["messages"][0]["content"] == "kept"
