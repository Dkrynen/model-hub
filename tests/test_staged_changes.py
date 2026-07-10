from __future__ import annotations

import hashlib
from pathlib import Path


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
    import pytest

    from backend.cookbook import persistence

    sid = _mk_session()
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), "../outside.txt", "x")
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), ".", "x")


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
