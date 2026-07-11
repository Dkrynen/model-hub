"""Staged tool-handler overlays for build mode.

Swaps write_file/read_file/list_files with session-scoped staging closures:
writes go to the staged_changes store (never disk), reads and listings see
pending staged state so the agent never chases stale disk. Built per run in
the web build branch (Plan 3); CLI/TUI keep the untouched builtins.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.cookbook import persistence
from backend.project_paths import validate_relative_project_path

ToolHandler = Callable[[dict, dict], str]

MAX_STAGED_BYTES = 2 * 1024 * 1024


def _jail(args: dict, ctx: dict, default_path: str = "") -> tuple[Path, str] | str:
    """Replicate the builtin tools' path jail. Returns (base, rel_posix) or an error string."""
    path = Path(args.get("path", default_path))
    base = Path(ctx.get("cwd", ".")).resolve()
    target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        return f"error: path outside workspace: {target}"
    rel_posix = rel.as_posix()
    if rel_posix == ".":
        return base, rel_posix
    try:
        return base, validate_relative_project_path(rel_posix)
    except ValueError as exc:
        return f"error: {exc}"


def build_staged_handlers(
    base_handlers: dict[str, ToolHandler],
    *,
    session_id: str,
    run_id: str,
    event_queue: Any | None = None,
) -> dict[str, ToolHandler]:
    handlers = dict(base_handlers)
    base_read = base_handlers["read_file"]
    base_list = base_handlers["list_files"]

    def _pending(base: Path) -> list[dict]:
        """Return pending staged changes for this specific root only."""
        return [
            r
            for r in persistence.list_staged_changes(session_id, status="pending")
            if r["root"] == str(base)
        ]

    def staged_write(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx)
        if isinstance(jailed, str):
            return jailed
        base, rel = jailed
        content = args.get("content", "")
        n_bytes = len(content.encode("utf-8"))
        if n_bytes > MAX_STAGED_BYTES:
            return "error: content exceeds the 2 MB staging limit"
        try:
            row = persistence.stage_change(session_id, run_id, str(base), rel, content)
        except ValueError as e:
            return f"error: {e}"
        if event_queue is not None:
            event_queue.put(
                {
                    "type": "staged_change",
                    "session_id": session_id,
                    "run_id": run_id,
                    "change_id": row["id"],
                    "path": row["path"],
                }
            )
        return f"staged {n_bytes} bytes to {rel} (change {row['id']}) - not yet applied"

    def read_overlay(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx)
        if isinstance(jailed, str):
            return jailed
        base, rel = jailed
        for row in _pending(base):
            if row["path"] == rel:
                return row["new_content"]
        return base_read(args, ctx)

    def list_overlay(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx, default_path=".")
        if isinstance(jailed, str):
            return jailed
        base, rel = jailed
        dir_rel = "" if rel == "." else rel
        prefix = dir_rel + "/" if dir_rel else ""
        synth_dirs: set[str] = set()
        staged_files: list[str] = []
        for row in _pending(base):
            path = row["path"]
            if prefix and not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
            if not remainder:
                continue
            if "/" not in remainder:
                if (base / path).exists():
                    continue  # exists on disk; base listing already shows it
                size = len(row["new_content"].encode("utf-8"))
                staged_files.append(f"f {size:>10} {remainder} (staged)")
            else:
                seg = remainder.split("/", 1)[0]
                if (base / (prefix + seg)).exists():
                    continue  # real dir already listed by the base handler
                synth_dirs.add(seg)
        extra = [f"d {0:>10} {seg} (staged)" for seg in sorted(synth_dirs)] + staged_files
        listing = base_list(args, ctx)
        if not extra:
            return listing
        if listing.startswith("error: not found") or listing == "(empty)":
            return "\n".join(extra)
        return listing + "\n" + "\n".join(extra)

    handlers["write_file"] = staged_write
    handlers["read_file"] = read_overlay
    handlers["list_files"] = list_overlay
    return handlers
