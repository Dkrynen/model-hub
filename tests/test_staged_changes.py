from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _mk_session():
    from backend.cookbook.persistence import create_session

    return create_session(name="t", model="mock:1b", workspace="default")


def test_stage_change_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "src/new.py", "print('hi')\n")
    assert row["status"] == "pending"
    assert row["path"] == "src/new.py"
    assert row["base_hash"] is None
    assert row["old_content"] is None
    assert row["new_content"] == "print('hi')\n"
    assert row["run_id"] == "run1"
    assert len(row["id"]) == 14
    assert not (tmp_path / "src" / "new.py").exists()  # nothing touched disk


def test_stage_change_existing_file_snapshots_disk(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    assert row["base_hash"] == hashlib.sha256(b"original").hexdigest()
    assert row["old_content"] == "original"
    assert f.read_bytes() == b"original"  # disk untouched


def test_stage_change_upsert_keeps_original_snapshot(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    first = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "v1")
    second = persistence.stage_change(sid, "run2", str(tmp_path), "a.txt", "v2")
    assert second["id"] == first["id"]  # same pending row
    assert second["new_content"] == "v2"
    assert second["run_id"] == "run2"  # provenance stamped to latest run
    assert second["base_hash"] == first["base_hash"]  # ORIGINAL snapshot preserved
    assert second["old_content"] == "original"
    pending = persistence.list_staged_changes(sid, status="pending")
    assert len(pending) == 1


def test_stage_change_jail_escape_raises(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), "../outside.txt", "x")
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), ".", "x")


@pytest.mark.parametrize(
    "invalid_path",
    [
        "src/app.py:payload",
        "NUL",
        "aux.txt",
        "COM1.log",
        "src/trailing.",
        "src/trailing ",
        "src/bad?.txt",
    ],
)
def test_stage_change_refuses_nonportable_windows_aliases(
    isolated_home, tmp_path, invalid_path
):
    from backend.cookbook import persistence

    sid = _mk_session()
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), invalid_path, "x")
    assert persistence.list_staged_changes(sid) == []


def test_list_staged_changes_filters(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    persistence.stage_change(sid, "runA", str(tmp_path), "one.txt", "1")
    row_b = persistence.stage_change(sid, "runB", str(tmp_path), "two.txt", "2")
    persistence.set_staged_status(row_b["id"], "rejected")

    assert [r["path"] for r in persistence.list_staged_changes(sid)] == ["one.txt", "two.txt"]
    assert [r["path"] for r in persistence.list_staged_changes(sid, status="pending")] == ["one.txt"]
    assert [r["path"] for r in persistence.list_staged_changes(sid, run_id="runB")] == ["two.txt"]
    assert persistence.list_staged_changes("nosuchsession") == []


def test_get_and_set_status(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    assert persistence.get_staged_change("nope") is None
    persistence.set_staged_status(row["id"], "rejected")
    assert persistence.get_staged_change(row["id"])["status"] == "rejected"


def test_delete_session_removes_staged_rows(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    persistence.delete_session(sid)
    assert persistence.get_staged_change(row["id"]) is None


def test_stage_change_non_utf8_file_refuses(isolated_home, tmp_path):
    import pytest

    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\xff\xfe\x00\x01binary")
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), "bin.dat", "changed")
    assert persistence.list_staged_changes(sid) == []


def test_stage_change_non_utf8_target_absent_still_works(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    # target path doesn't exist yet -- staging a NEW file must still work
    row = persistence.stage_change(sid, "run1", str(tmp_path), "bin.dat", "print('hi')\n")
    assert row["status"] == "pending"
    assert row["old_content"] is None


def test_stage_change_same_path_different_roots_keyed_separately(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    proj_a = tmp_path / "projA"
    proj_b = tmp_path / "projB"
    proj_a.mkdir()
    proj_b.mkdir()

    persistence.stage_change(sid, "run1", str(proj_a), "src/x.py", "a-content")
    persistence.stage_change(sid, "run2", str(proj_b), "src/x.py", "b-content")

    pending = persistence.list_staged_changes(sid, status="pending")
    assert len(pending) == 2
    roots = {r["root"] for r in pending}
    assert roots == {str(proj_a.resolve()), str(proj_b.resolve())}


def test_apply_happy_path_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "src/new.py", "print('hi')\n")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "applied"
    assert (tmp_path / "src" / "new.py").read_bytes() == b"print('hi')\n"
    assert persistence.get_staged_change(row["id"])["status"] == "applied"


def test_apply_happy_path_existing_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "applied"
    assert f.read_bytes() == b"changed"


def test_apply_conflict_when_disk_changed(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    f.write_bytes(b"hand-edited meanwhile")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "conflict"
    assert result["base_hash"] == row["base_hash"]
    assert result["disk_hash"] != row["base_hash"]
    assert f.read_bytes() == b"hand-edited meanwhile"  # no partial write
    assert persistence.get_staged_change(row["id"])["status"] == "conflict"


def test_apply_conflict_when_new_file_now_exists(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "new.txt", "staged")
    (tmp_path / "new.txt").write_bytes(b"someone else created this")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "conflict"
    assert result["base_hash"] is None


def test_apply_rejects_non_pending_and_unknown(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    persistence.set_staged_status(row["id"], "rejected")
    assert persistence.apply_staged_change(row["id"])["status"] == "not_pending"
    assert persistence.apply_staged_change("nope")["status"] == "not_found"


def test_apply_rejail_blocks_tampered_path(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    conn = persistence._ensure_db()
    conn.execute("UPDATE staged_changes SET path = '../evil.txt' WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "error"
    assert not (tmp_path.parent / "evil.txt").exists()


@pytest.mark.parametrize("tampered_path", ["a.txt:payload", "NUL", "src/trailing."])
def test_apply_rejail_blocks_tampered_windows_alias(
    isolated_home, tmp_path, tampered_path
):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    conn = persistence._ensure_db()
    conn.execute(
        "UPDATE staged_changes SET path = ? WHERE id = ?",
        (tampered_path, row["id"]),
    )
    conn.commit()
    conn.close()

    result = persistence.apply_staged_change(row["id"])

    assert result["status"] == "error"


def test_revert_restores_original(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    persistence.apply_staged_change(row["id"])
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "reverted"
    assert f.read_bytes() == b"original"
    assert persistence.get_staged_change(row["id"])["status"] == "reverted"


def test_revert_deletes_applied_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "new.txt", "staged")
    persistence.apply_staged_change(row["id"])
    assert (tmp_path / "new.txt").exists()
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "reverted"
    assert not (tmp_path / "new.txt").exists()


def test_revert_conflict_when_disk_hand_edited_after_apply(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    persistence.apply_staged_change(row["id"])
    f.write_bytes(b"hand-edited after apply")
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "conflict"
    assert f.read_bytes() == b"hand-edited after apply"


def test_revert_rejects_non_applied(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    assert persistence.revert_applied_change(row["id"])["status"] == "not_applied"
    assert persistence.revert_applied_change("nope")["status"] == "not_found"


def test_apply_error_when_target_is_directory(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "adir", "content")
    (tmp_path / "adir").mkdir()
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "error"
    assert persistence.get_staged_change(row["id"])["status"] == "pending"
    assert list(tmp_path.glob(".*.lac-tmp")) == []


def test_revert_rejail_blocks_tampered_path(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    persistence.apply_staged_change(row["id"])
    conn = persistence._ensure_db()
    conn.execute("UPDATE staged_changes SET path = '../evil.txt' WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "error"
    assert not (tmp_path.parent / "evil.txt").exists()


def _build_handlers(sid, run_id="run1", event_queue=None):
    from backend.agent.staging import build_staged_handlers
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    return build_staged_handlers(
        TOOL_HANDLERS, session_id=sid, run_id=run_id, event_queue=event_queue
    )


def test_staged_write_stages_instead_of_writing(isolated_home, tmp_path):
    import queue

    from backend.cookbook import persistence

    sid = _mk_session()
    q = queue.Queue()
    handlers = _build_handlers(sid, event_queue=q)
    ctx = {"cwd": str(tmp_path)}

    result = handlers["write_file"]({"path": "src/x.py", "content": "pass\n"}, ctx)
    assert "staged" in result and "not yet applied" in result
    assert not (tmp_path / "src" / "x.py").exists()
    rows = persistence.list_staged_changes(sid, status="pending")
    assert [r["path"] for r in rows] == ["src/x.py"]
    ev = q.get_nowait()
    assert ev["type"] == "staged_change"
    assert ev["session_id"] == sid
    assert ev["run_id"] == "run1"
    assert ev["change_id"] == rows[0]["id"]
    assert ev["path"] == "src/x.py"


def test_staged_write_jail_and_size_cap(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}

    assert handlers["write_file"]({"path": "../evil.txt", "content": "x"}, ctx).startswith("error:")
    for invalid_path in ("src/app.py:payload", "NUL", "src/trailing."):
        assert handlers["write_file"](
            {"path": invalid_path, "content": "x"}, ctx
        ).startswith("error:")
    big = "x" * (2 * 1024 * 1024 + 1)
    assert "2 MB" in handlers["write_file"]({"path": "big.txt", "content": big}, ctx)


def test_read_overlay_returns_staged_content(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}
    (tmp_path / "a.txt").write_text("disk version", encoding="utf-8")

    handlers["write_file"]({"path": "a.txt", "content": "staged version"}, ctx)
    assert handlers["read_file"]({"path": "a.txt"}, ctx) == "staged version"
    # un-staged file falls through to disk
    (tmp_path / "b.txt").write_text("plain", encoding="utf-8")
    assert handlers["read_file"]({"path": "b.txt"}, ctx) == "plain"


def test_list_overlay_shows_staged_new_files(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")

    handlers["write_file"]({"path": "ghost.txt", "content": "staged only"}, ctx)
    listing = handlers["list_files"]({"path": "."}, ctx)
    assert "real.txt" in listing
    assert "ghost.txt" in listing
    assert "(staged)" in listing
    # staged file in a directory that only exists via staging
    handlers["write_file"]({"path": "newdir/inner.txt", "content": "y"}, ctx)
    inner = handlers["list_files"]({"path": "newdir"}, ctx)
    assert "inner.txt" in inner


def test_other_handlers_pass_through_untouched(isolated_home):
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    sid = _mk_session()
    handlers = _build_handlers(sid)
    assert handlers["run_bash"] is TOOL_HANDLERS["run_bash"]
    assert handlers["web_search"] is TOOL_HANDLERS["web_search"]
    assert TOOL_HANDLERS["write_file"] is not handlers["write_file"]  # original untouched


def test_staged_read_list_root_isolated_same_session(isolated_home, tmp_path):
    """Pending rows are keyed (session_id, root, path); ensure staging in projA doesn't leak into projB."""
    from backend.cookbook import persistence

    sid = _mk_session()
    proj_a = tmp_path / "projA"
    proj_b = tmp_path / "projB"
    proj_a.mkdir()
    proj_b.mkdir()

    # Stage "same.txt" under projA with staged content
    handlers_a = _build_handlers(sid)
    ctx_a = {"cwd": str(proj_a)}
    handlers_a["write_file"]({"path": "same.txt", "content": "projA staged"}, ctx_a)

    # Create a real same.txt on disk under projB with different content
    (proj_b / "same.txt").write_text("projB disk", encoding="utf-8")

    # Build handlers for projB and verify read returns disk content, not projA staged
    handlers_b = _build_handlers(sid)
    ctx_b = {"cwd": str(proj_b)}
    result = handlers_b["read_file"]({"path": "same.txt"}, ctx_b)
    assert result == "projB disk", f"Expected 'projB disk' but got '{result}'"

    # Verify projA staged file doesn't appear in projB listing
    listing_b = handlers_b["list_files"]({"path": "."}, ctx_b)
    assert "same.txt" in listing_b
    # Count occurrences to ensure it's only the disk file
    staged_lines = [line for line in listing_b.split("\n") if "same.txt" in line and "(staged)" in line]
    assert len(staged_lines) == 0, f"projA staged 'same.txt' leaked into projB: {listing_b}"

    # Verify projA still sees its staged content
    result_a = handlers_a["read_file"]({"path": "same.txt"}, ctx_a)
    assert result_a == "projA staged"


def test_list_overlay_synthesizes_nested_staged_dirs(isolated_home, tmp_path):
    """A staged-only path nested >1 level deep (a/b/c.txt) must surface synthesized
    intermediate directories, not fall through to 'error: not found' for 'a'."""
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}

    handlers["write_file"]({"path": "a/b/c.txt", "content": "nested staged"}, ctx)

    listing_a = handlers["list_files"]({"path": "a"}, ctx)
    assert not listing_a.startswith("error:")
    assert "b" in listing_a
    assert "(staged)" in listing_a

    listing_ab = handlers["list_files"]({"path": "a/b"}, ctx)
    assert "c.txt (staged)" in listing_ab

    listing_root = handlers["list_files"]({"path": "."}, ctx)
    assert "d" in listing_root
    assert "a (staged)" in listing_root


def test_list_overlay_no_duplicate_for_disk_dirs(isolated_home, tmp_path):
    """When a directory already exists on disk, the synthesized-dir overlay must not
    duplicate it with a second '(staged)' entry."""
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}
    (tmp_path / "real").mkdir()

    handlers["write_file"]({"path": "real/new.txt", "content": "staged in real dir"}, ctx)

    listing_root = handlers["list_files"]({"path": "."}, ctx)
    real_lines = [line for line in listing_root.split("\n") if " real" in line or line.endswith("real")]
    assert len([line for line in listing_root.split("\n") if "real" in line]) == 1, (
        f"'real' should appear exactly once: {listing_root}"
    )
    assert "(staged)" not in [line for line in listing_root.split("\n") if "real" in line][0]

    listing_real = handlers["list_files"]({"path": "real"}, ctx)
    assert "new.txt (staged)" in listing_real
