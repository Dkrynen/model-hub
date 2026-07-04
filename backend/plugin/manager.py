from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import resolve_config
from ..cookbook.config import CONFIG_DIR
from .base import PluginHost, PluginManifest

BUILTIN_DIR = Path(__file__).resolve().parent / "builtins"


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: Any
    error: str | None = None


class PluginManager:
    def __init__(self, host: PluginHost | None = None, start_dir: Path | None = None):
        self.host = host
        self.start_dir = Path(start_dir) if start_dir else Path.cwd()
        self._discovered: list[PluginManifest] = []
        self._loaded: list[LoadedPlugin] = []

    def discover(self) -> list[PluginManifest]:
        self._discovered = []
        self._discovered.extend(self._discover_builtins())
        self._discovered.extend(self._discover_dir(self.start_dir / ".apt" / "plugin"))
        self._discovered.extend(self._discover_dir(CONFIG_DIR / "plugins"))
        self._discovered.extend(self._discover_entry_points())
        return list(self._discovered)

    def _discover_builtins(self) -> list[PluginManifest]:
        out = []
        if not BUILTIN_DIR.exists():
            return out
        for entry in sorted(BUILTIN_DIR.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").exists():
                out.append(
                    PluginManifest(
                        name=entry.name,
                        type="tool",
                        entry="setup",
                        path=str(entry),
                    )
                )
            elif entry.suffix == ".py" and entry.name != "__init__.py":
                out.append(
                    PluginManifest(
                        name=entry.stem,
                        type="tool",
                        entry="setup",
                        path=str(entry),
                    )
                )
        return out

    def _discover_dir(self, base: Path) -> list[PluginManifest]:
        out = []
        if not base.exists():
            return out
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "plugin.json").exists():
                out.append(self._load_manifest_dir(entry))
            elif entry.suffix == ".py":
                out.append(
                    PluginManifest(
                        name=entry.stem,
                        type="tool",
                        entry="setup",
                        path=str(entry),
                    )
                )
        return out

    def _load_manifest_dir(self, d: Path) -> PluginManifest:
        try:
            data = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return PluginManifest(
            name=data.get("name", d.name),
            type=data.get("type", "tool"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            entry=data.get("entry", "setup"),
            path=str(d),
            enabled=data.get("enabled", True),
            options=data.get("options", {}),
        )

    def _discover_entry_points(self) -> list[PluginManifest]:
        out = []
        try:
            eps = importlib.metadata.entry_points()
        except Exception:
            return out
        # "lac.tools" = TUI agent-tool plugins. The "lac.plugins" group belongs to
        # the app-level open-core seam (backend/plugins.py) — a different contract.
        try:
            group = eps.select(group="lac.tools")
        except Exception:
            group = [e for e in eps if e.group == "lac.tools"] if hasattr(eps, "__iter__") else []
        for ep in group:
            out.append(
                PluginManifest(
                    name=ep.name,
                    type="tool",
                    entry=ep.value,
                    path=f"entrypoint:{ep.value}",
                )
            )
        return out

    def load_all(self) -> list[LoadedPlugin]:
        if not self._discovered:
            self.discover()
        seen = set()
        for mf in self._discovered:
            if not mf.enabled or mf.name in seen:
                continue
            seen.add(mf.name)
            self._loaded.append(self._load_one(mf))
        return list(self._loaded)

    def _load_one(self, manifest: PluginManifest) -> LoadedPlugin:
        try:
            module = self._import_manifest(manifest)
        except Exception as e:
            return LoadedPlugin(manifest=manifest, module=None, error=f"import: {e}")

        setup = getattr(module, manifest.entry, None) or getattr(module, "setup", None)
        if setup is None:
            return LoadedPlugin(manifest=manifest, module=module, error=f"no setup() in {manifest.name}")

        try:
            if self.host is not None:
                setup(self.host)
            else:
                setup(None)
        except Exception as e:
            return LoadedPlugin(manifest=manifest, module=module, error=f"setup: {e}")

        return LoadedPlugin(manifest=manifest, module=module)

    def _import_manifest(self, manifest: PluginManifest) -> Any:
        path = manifest.path
        if path.startswith("entrypoint:"):
            mod_ref = path.split(":", 1)[1]
            mod_name, _, attr = mod_ref.partition(":")
            return importlib.import_module(mod_name)

        p = Path(path)
        if p.is_dir() and (p / "__init__.py").exists():
            mod_name = f"_apt_plugin_{p.name}"
            if mod_name in sys.modules:
                return sys.modules[mod_name]
            spec = importlib.util.spec_from_file_location(mod_name, p / "__init__.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            return module

        mod_name = f"_apt_plugin_{p.stem}"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(mod_name, p)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    def loaded(self) -> list[LoadedPlugin]:
        return list(self._loaded)

    def errors(self) -> list[LoadedPlugin]:
        return [p for p in self._loaded if p.error]

    def names(self) -> list[str]:
        return [p.manifest.name for p in self._loaded]


class PluginHostImpl:
    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.commands: dict[str, Any] = {}
        self.themes: dict[str, Any] = {}
        self.providers: dict[str, Any] = {}

    def register_tool(self, name: str, description: str, parameters: dict, handler: Any) -> None:
        self.tools[name] = {"name": name, "description": description, "parameters": parameters, "handler": handler}

    def register_command(self, name: str, handler: Any) -> None:
        self.commands[name] = handler

    def register_provider(self, name: str, factory: Any) -> None:
        self.providers[name] = factory

    def register_theme(self, theme_id: str, theme: Any) -> None:
        self.themes[theme_id] = theme


def load_plugins(host: PluginHost | None = None, start_dir: Path | None = None) -> PluginManager:
    mgr = PluginManager(host=host, start_dir=start_dir)
    mgr.discover()
    mgr.load_all()
    return mgr
