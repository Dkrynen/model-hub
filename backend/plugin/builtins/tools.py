from __future__ import annotations

import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ToolHandler = Callable[[dict, dict], str]


def _read_file(args: dict, ctx: dict) -> str:
    path = Path(args.get("path", ""))
    base = Path(ctx.get("cwd", ".")).resolve()
    target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        return f"error: path outside workspace: {target}"
    if not target.exists() or not target.is_file():
        return f"error: not found: {rel}"
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"error reading: {e}"


def _write_file(args: dict, ctx: dict) -> str:
    path = Path(args.get("path", ""))
    content = args.get("content", "")
    base = Path(ctx.get("cwd", ".")).resolve()
    target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        return f"error: path outside workspace: {target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {rel}"


def _list_files(args: dict, ctx: dict) -> str:
    path = Path(args.get("path", "."))
    base = Path(ctx.get("cwd", ".")).resolve()
    target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not target.exists():
        return f"error: not found: {target}"
    entries = []
    for p in sorted(target.iterdir()):
        kind = "d" if p.is_dir() else "f"
        size = p.stat().st_size if p.is_file() else 0
        entries.append(f"{kind} {size:>10} {p.name}")
    return "\n".join(entries) if entries else "(empty)"


def _run_bash(args: dict, ctx: dict) -> str:
    cmd = args.get("command", "")
    if not cmd:
        return "error: no command"
    cwd = ctx.get("cwd", ".")
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=60
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"[exit {proc.returncode}]\n{out.strip()}"
    except subprocess.TimeoutExpired:
        return "error: command timed out (60s)"
    except Exception as e:
        return f"error: {e}"


def _web_search(args: dict, ctx: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "error: no query"
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "LAC/2.2.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")
        import re

        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html)[:8]
        clean = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
        return "\n".join(f"- {t}" for t in clean) if clean else "(no results)"
    except Exception as e:
        return f"error: {e}"


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a text file relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to workspace."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a shell command and return stdout+stderr. Use for build, test, git.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via DuckDuckGo and return result titles.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "read_file": _read_file,
    "write_file": _write_file,
    "list_files": _list_files,
    "run_bash": _run_bash,
    "web_search": _web_search,
}

WRITE_TOOLS = {"write_file", "run_bash"}
DELETE_TOOLS = set()
NETWORK_TOOLS = {"web_search"}


def setup(host) -> None:
    if host is None:
        return
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        handler = TOOL_HANDLERS.get(name)
        if handler is not None:
            try:
                host.register_tool(name, schema["function"]["description"], schema["function"]["parameters"], handler)
            except Exception:
                pass
