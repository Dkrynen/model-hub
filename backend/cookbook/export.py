from __future__ import annotations

import datetime as _dt
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml

from .persistence import get_session, list_sessions

FORMATS = {"md", "markdown", "json", "yaml", "yml", "html", "opencode-json"}


def _iso(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).isoformat()
    except Exception:
        return ""


def _date_str(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _ymd(ts: float | None) -> str:
    if not ts:
        return "unknown"
    try:
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def _role_label(role: str, model: str = "") -> str:
    if role == "user":
        return "User"
    if role == "assistant":
        return model or "Assistant"
    if role == "system":
        return "System"
    return role.capitalize()


def to_json(session: dict) -> str:
    payload = {
        "format": "apt-session/v1",
        "session": {
            "id": session.get("id", ""),
            "model": session.get("model", ""),
            "provider": "ollama",
            "name": session.get("name", ""),
            "system_prompt": session.get("system_prompt", ""),
            "workspace": session.get("workspace", ""),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "messages": [
                {
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp"),
                    "tool_calls": m.get("tool_calls"),
                }
                for m in session.get("messages", [])
            ],
        },
        "subagent_sessions": [],
    }
    return json.dumps(payload, indent=2)


def to_yaml(session: dict) -> str:
    payload = json.loads(to_json(session))
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def to_markdown(session: dict) -> str:
    sid = session.get("id", "")
    model = session.get("model", "")
    front = {
        "session_id": sid,
        "model": model,
        "created": _iso(session.get("created_at")),
        "updated": _iso(session.get("updated_at")),
        "workspace": session.get("workspace", ""),
        "messages": len(session.get("messages", [])),
    }
    lines = ["---", yaml.safe_dump(front, sort_keys=False, default_flow_style=False).strip(), "---", ""]
    lines.append(f"# LAC Session: {sid[:10] if sid else 'unknown'}")
    lines.append("")
    lines.append(f"**Model:** {model or 'n/a'}  ")
    lines.append(f"**Created:** {_date_str(session.get('created_at'))}  ")
    lines.append(f"**Messages:** {len(session.get('messages', []))}")
    lines.append("")
    lines.append("---")
    lines.append("")
    for m in session.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        lines.append(f"## {_role_label(role, model)}")
        lines.append("")
        lines.append(content if content else "_(empty)_")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _ulid() -> str:
    return f"{int(time.time() * 1000):010d}{os.urandom(8).hex()[:16]}"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "session"


def to_opencode_json(session: dict) -> str:
    sid = session.get("id", "") or _ulid()
    title = session.get("name") or next((m.get("content", "")[:60] for m in session.get("messages", []) if m.get("role") == "user"), "LAC Session")
    model = session.get("model", "")
    messages_out = []
    parent_id = None
    for m in session.get("messages", []):
        mid = "msg_" + _ulid()
        parts = []
        content = m.get("content", "") or ""
        if content:
            parts.append({"type": "text", "text": content})
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", tc) if isinstance(tc, dict) else {}
            parts.append({
                "type": "tool",
                "tool": fn.get("name", "unknown"),
                "callID": "pro_" + _ulid(),
                "state": "completed",
                "input": fn.get("arguments", {}),
                "output": "",
            })
        if not parts:
            parts.append({"type": "text", "text": ""})
        messages_out.append({
            "info": {
                "id": mid,
                "role": m.get("role", "user"),
                "time": m.get("timestamp") or session.get("created_at"),
                "parentID": parent_id,
                "modelID": model if m.get("role") == "assistant" else "",
                "providerID": "ollama" if m.get("role") == "assistant" else "",
                "agent": "",
                "cost": 0,
                "tokens": 0,
            },
            "parts": parts,
        })
        parent_id = mid
    payload = {
        "info": {
            "id": "ses_" + sid,
            "slug": _slug(title),
            "projectID": session.get("workspace", "default"),
            "title": title,
            "version": "1",
            "time": session.get("created_at"),
            "summary": "",
        },
        "messages": messages_out,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def to_html(session: dict) -> str:
    sid = session.get("id", "")
    model = session.get("model", "")
    rows = []
    for m in session.get("messages", []):
        role = m.get("role", "user")
        cls = "user" if role == "user" else ("assistant" if role == "assistant" else "system")
        label = html.escape(_role_label(role, model))
        body = html.escape(m.get("content", ""))
        rows.append(
            f'<div class="msg {cls}"><div class="role">{label}</div><div class="body"><pre>{body}</pre></div></div>'
        )
    msgs = "\n".join(rows)
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>LAC Session {html.escape(sid[:10])}</title>
<style>
body {{ background:#0f1117; color:#e1e4ec; font-family:system-ui,sans-serif; max-width:880px; margin:2rem auto; padding:0 1rem; }}
h1 {{ color:#6c8cff; font-size:1.3rem; }}
.meta {{ color:#7a7f9a; font-size:.85rem; margin-bottom:1.5rem; }}
.msg {{ border-left:3px solid #2a2e3e; padding:.5rem 0 .5rem 1rem; margin:1rem 0; }}
.msg.user {{ border-color:#6c8cff; }}
.msg.assistant {{ border-color:#22c55e; }}
.msg.system {{ border-color:#7a7f9a; }}
.role {{ font-weight:700; margin-bottom:.25rem; }}
.msg.user .role {{ color:#6c8cff; }}
.msg.assistant .role {{ color:#22c55e; }}
.body pre {{ white-space:pre-wrap; word-wrap:break-word; margin:0; font-family:ui-monospace,Consolas,monospace; font-size:.9rem; }}
</style></head>
<body>
<h1>LAC Session</h1>
<div class="meta">id: {html.escape(sid)} &middot; model: {html.escape(model)} &middot; created: {html.escape(_date_str(session.get('created_at')))}</div>
{msgs}
</body></html>"""


def export_session(session: dict, fmt: str = "md") -> str:
    fmt = fmt.lower()
    if fmt in ("md", "markdown"):
        return to_markdown(session)
    if fmt == "json":
        return to_json(session)
    if fmt in ("yaml", "yml"):
        return to_yaml(session)
    if fmt == "html":
        return to_html(session)
    if fmt == "opencode-json":
        return to_opencode_json(session)
    raise ValueError(f"unknown format: {fmt!r} (use md/json/yaml/html/opencode-json)")


def _filename(session: dict, fmt: str) -> str:
    sid = session.get("id", "session") or "session"
    short = sid[:10]
    ext = {"md": "md", "markdown": "md", "json": "json", "yaml": "yaml", "yml": "yaml", "html": "html", "opencode-json": "json"}[fmt.lower()]
    return f"lac-session-{short}.{ext}"


def export_session_file(session: dict, fmt: str, out_dir: str | Path | None = None, base: str | None = None) -> Path:
    fmt = fmt.lower()
    if fmt in ("md", "markdown"):
        fmt = "md"
    elif fmt in ("yaml", "yml"):
        fmt = "yaml"
    out_dir = Path(out_dir) if out_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = base or _filename(session, fmt)
    path = out_dir / name
    path.write_text(export_session(session, fmt), encoding="utf-8")
    return path


def export_all(out_dir: str | Path, fmt: str = "md", include_json: bool = True) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for meta in list_sessions():
        session = get_session(meta["id"])
        if not session:
            continue
        day_dir = out_dir / _ymd(session.get("created_at"))
        day_dir.mkdir(parents=True, exist_ok=True)
        if fmt in ("md", "markdown"):
            written.append(export_session_file(session, "md", day_dir))
            if include_json:
                written.append(export_session_file(session, "json", day_dir))
        else:
            written.append(export_session_file(session, fmt, day_dir))
    return written


def import_session(path: str | Path) -> dict:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".json",):
        payload = json.loads(raw)
    elif path.suffix.lower() in (".yaml", ".yml"):
        payload = yaml.safe_load(raw)
    else:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = yaml.safe_load(raw)
    sess = payload.get("session", payload) if isinstance(payload, dict) else {}
    return {
        "id": sess.get("id", ""),
        "model": sess.get("model", ""),
        "name": sess.get("name", ""),
        "system_prompt": sess.get("system_prompt", ""),
        "workspace": sess.get("workspace", ""),
        "created_at": sess.get("created_at"),
        "updated_at": sess.get("updated_at"),
        "messages": [
            {"role": m.get("role", "user"), "content": m.get("content", ""), "timestamp": m.get("timestamp")}
            for m in sess.get("messages", [])
        ],
    }
