"""Open-core plugin seam.

Plugins are Python packages exposing an entry point in the ``lac.plugins``
group. The entry point resolves to a plugin object with:

- ``name: str``            display name (falls back to the entry-point name)
- ``version: str``         plugin version (falls back to "?")
- ``register_cli(subparsers)``  optional — add argparse subcommands
- ``register_api(app)``         optional — add Flask routes

A plugin that raises during load or registration must never break core:
every call is isolated and errors are captured on the LoadedPlugin record.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points

GROUP = "lac.plugins"


def _entry_points():
    """Indirection so tests can substitute fake entry points."""
    return list(entry_points(group=GROUP))


@dataclass
class LoadedPlugin:
    name: str
    version: str
    obj: object | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def discover() -> list[LoadedPlugin]:
    """Load all ``apt.plugins`` entry points, isolating per-plugin failures."""
    out: list[LoadedPlugin] = []
    for ep in _entry_points():
        try:
            obj = ep.load()
            # getattr is inside the guard: a raising name/version property
            # must not break core either.
            name = getattr(obj, "name", None) or ep.name
            version = getattr(obj, "version", None) or "?"
        except Exception as exc:  # noqa: BLE001 — a plugin must never break core
            out.append(LoadedPlugin(name=ep.name, version="?", obj=None, error=str(exc)))
            continue
        out.append(LoadedPlugin(name=name, version=version, obj=obj))
    return out
