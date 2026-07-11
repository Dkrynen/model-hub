"""Canonical cross-platform project-path validation for staged operations."""
from __future__ import annotations

import re


MAX_PROJECT_RELATIVE_PATH_CHARS = 512
_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f\x7f]')
_RESERVED_WINDOWS_STEMS = frozenset(
    {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
)
_RESERVED_WINDOWS_PORT = re.compile(
    r"^(?:com|lpt)(?:[1-9]|\u00b9|\u00b2|\u00b3)$", re.IGNORECASE
)


def validate_relative_project_path(value: str) -> str:
    """Return one canonical POSIX relative path or raise ``ValueError``."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PROJECT_RELATIVE_PATH_CHARS
        or value.startswith("/")
        or _INVALID_WINDOWS_CHARS.search(value)
    ):
        raise ValueError("path must be a bounded portable relative path")
    parts = value.split("/")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((".", " ")):
            raise ValueError("path contains a non-canonical component")
        stem = part.split(".", 1)[0].casefold()
        if stem in _RESERVED_WINDOWS_STEMS or _RESERVED_WINDOWS_PORT.fullmatch(stem):
            raise ValueError("path contains a reserved Windows component")
    return "/".join(parts)


__all__ = ["MAX_PROJECT_RELATIVE_PATH_CHARS", "validate_relative_project_path"]
