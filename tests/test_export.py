from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from backend.cookbook.export import (
    _filename,
    export_all,
    export_session,
    export_session_file,
    import_session,
    to_html,
    to_json,
    to_markdown,
    to_opencode_json,
    to_yaml,
)


def _make_session():
    return {
        "id": "abc123def45678",
        "name": "test",
        "model": "llama3.2:3b",
        "system_prompt": "be helpful",
        "workspace": "default",
        "created_at": 1780309800.0,
        "updated_at": 1780310700.0,
        "messages": [
            {"role": "user", "content": "Write fib", "timestamp": 1780309810.0},
            {"role": "assistant", "content": "```python\ndef fib(n): pass\n```", "timestamp": 1780309900.0},
        ],
    }


def test_json_format_is_apt_session_v1():
    out = json.loads(to_json(_make_session()))
    assert out["format"] == "apt-session/v1"
    assert out["session"]["id"] == "abc123def45678"
    assert out["session"]["model"] == "llama3.2:3b"
    assert len(out["session"]["messages"]) == 2
    assert out["subagent_sessions"] == []


def test_yaml_roundtrips_to_same_json():
    s = _make_session()
    parsed = yaml.safe_load(to_yaml(s))
    assert parsed["format"] == "apt-session/v1"
    assert parsed["session"]["id"] == s["id"]


def test_markdown_has_frontmatter_and_roles():
    md = to_markdown(_make_session())
    assert md.startswith("---")
    assert "session_id: abc123def45678" in md
    assert "## User" in md
    assert "## llama3.2:3b" in md
    assert "def fib" in md


def test_html_is_self_contained():
    h = to_html(_make_session())
    assert "<!doctype html>" in h
    assert "abc123de" in h
    assert "def fib" in h
    assert "</html>" in h


def test_opencode_json_structure():
    from backend.cookbook.export import to_opencode_json

    s = _make_session()
    s["messages"][1]["tool_calls"] = [{"function": {"name": "list_files", "arguments": "{}"}}]
    out = json.loads(to_opencode_json(s))
    assert set(out.keys()) == {"info", "messages"}
    assert out["info"]["id"].startswith("ses_")
    assert out["info"]["slug"] == "test"
    assert out["info"]["projectID"] == "default"
    assert len(out["messages"]) == 2
    assert out["messages"][0]["info"]["id"].startswith("msg_")
    assert out["messages"][0]["info"]["role"] == "user"
    assert out["messages"][0]["info"]["parentID"] is None
    assert out["messages"][1]["info"]["parentID"] == out["messages"][0]["info"]["id"]
    assert out["messages"][1]["info"]["modelID"] == "llama3.2:3b"
    parts = out["messages"][1]["parts"]
    assert any(p["type"] == "text" for p in parts)
    assert any(p["type"] == "tool" and p["tool"] == "list_files" and p["state"] == "completed" for p in parts)


def test_opencode_json_no_bom():
    from backend.cookbook.export import to_opencode_json

    raw = to_opencode_json(_make_session())
    assert not raw.startswith("\ufeff")


def test_export_session_dispatch():
    s = _make_session()
    assert "## User" in export_session(s, "md")
    assert json.loads(export_session(s, "json"))["format"] == "apt-session/v1"
    assert "apt-session/v1" in export_session(s, "yaml")
    assert "<html" in export_session(s, "html")


def test_export_session_file_writes(tmp_path):
    p = export_session_file(_make_session(), "json", tmp_path)
    assert p.exists()
    assert p.suffix == ".json"
    assert json.loads(p.read_text())["session"]["id"] == "abc123def45678"


def test_export_all_organizes_by_date(tmp_path):
    s = _make_session()
    from backend.cookbook import persistence

    sid = persistence.create_session(model=s["model"])
    persistence.save_session(sid, model=s["model"], messages=s["messages"])
    written = export_all(tmp_path, "md", include_json=True)
    assert written
    today = time.strftime("%Y-%m-%d", time.gmtime())
    day_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert any(d.name == today for d in day_dirs)
    md_files = list((tmp_dir_for(day_dirs, today)).glob("*.md"))
    assert md_files


def tmp_dir_for(dirs, name):
    for d in dirs:
        if d.name == name:
            return d
    return dirs[0]


def test_import_roundtrip(tmp_path):
    s = _make_session()
    p = export_session_file(s, "json", tmp_path)
    imported = import_session(p)
    assert imported["id"] == s["id"]
    assert imported["model"] == s["model"]
    assert len(imported["messages"]) == 2
    assert imported["messages"][0]["content"] == "Write fib"


def test_import_yaml(tmp_path):
    s = _make_session()
    p = tmp_path / "s.yaml"
    p.write_text(to_yaml(s), encoding="utf-8")
    imported = import_session(p)
    assert imported["id"] == s["id"]
    assert len(imported["messages"]) == 2


def test_import_then_persist(isolated_home, tmp_path):
    from backend.cookbook import persistence

    s = _make_session()
    p = export_session_file(s, "json", tmp_path)
    imported = import_session(p)
    sid = imported["id"] or persistence.create_session(model=imported["model"])
    persistence.save_session(sid, model=imported["model"], messages=imported["messages"])
    got = persistence.get_session(sid)
    assert got is not None
    assert len(got["messages"]) == 2


def test_filename_uses_lac_session_prefix():
    name = _filename({"id": "abc123def45678"}, "json")
    assert name == "lac-session-abc123def4.json"
    assert "apt-session" not in name


def test_markdown_heading_is_lac_session():
    md = to_markdown(_make_session())
    assert "# LAC Session:" in md
    assert "Apt Session" not in md


def test_html_title_and_heading_are_lac_session():
    h = to_html(_make_session())
    assert "<title>LAC Session" in h
    assert "<h1>LAC Session</h1>" in h
    assert "Apt Session" not in h


def test_opencode_json_default_title_is_lac_session():
    s = _make_session()
    s["name"] = ""
    s["messages"] = [{"role": "assistant", "content": "hi", "timestamp": 1.0}]
    out = json.loads(to_opencode_json(s))
    assert out["info"]["title"] == "LAC Session"
