import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


CONFIG_DIR = Path.home() / ".model-hub"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_WORKSPACE = "default"


@dataclass
class Workspace:
    id: str
    name: str
    description: str = ""
    created_at: float = 0.0


@dataclass
class AppConfig:
    workspace: str = DEFAULT_WORKSPACE
    ollama_host: str = "http://localhost:11434"
    theme: str = "dark"
    default_model: str = ""
    default_context: int = 4096


def _ensure_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _config_path() -> Path:
    _ensure_dir()
    return CONFIG_FILE


def _workspaces_dir() -> Path:
    d = CONFIG_DIR / "workspaces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sessions_dir(workspace: str = "") -> Path:
    ws = workspace or load_config().workspace
    d = _workspaces_dir() / ws / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exports_dir(workspace: str = "") -> Path:
    ws = workspace or load_config().workspace
    d = _workspaces_dir() / ws / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _downloads_cache_dir() -> Path:
    d = CONFIG_DIR / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config() -> AppConfig:
    path = _config_path()
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
        except Exception:
            pass
    return AppConfig()


def save_config(config: AppConfig) -> None:
    path = _config_path()
    with open(path, "w") as f:
        json.dump(asdict(config), f, indent=2)


def list_workspaces() -> list[Workspace]:
    ws_dir = _workspaces_dir()
    workspaces = []
    for entry in sorted(ws_dir.iterdir()):
        if entry.is_dir():
            meta_file = entry / "workspace.json"
            name = entry.name
            desc = ""
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                    name = meta.get("name", entry.name)
                    desc = meta.get("description", "")
                except Exception:
                    pass
            workspaces.append(Workspace(id=entry.name, name=name, description=desc))
    if not workspaces:
        _create_default_workspace()
        workspaces = [Workspace(id=DEFAULT_WORKSPACE, name="Default Workspace", description="Default workspace")]
    return workspaces


def _create_default_workspace() -> None:
    ws_dir = _workspaces_dir() / DEFAULT_WORKSPACE
    ws_dir.mkdir(parents=True, exist_ok=True)
    meta = ws_dir / "workspace.json"
    if not meta.exists():
        import time
        meta.write_text(json.dumps({
            "name": "Default Workspace",
            "description": "Default workspace",
            "created_at": time.time(),
        }, indent=2))


def get_workspace(workspace_id: str) -> Optional[Workspace]:
    for w in list_workspaces():
        if w.id == workspace_id:
            return w
    return None


def _resolve_within_workspaces(ws_id: str) -> Path:
    """Resolve ws_id under _workspaces_dir() and refuse a path that would
    escape the sandbox via '/', '\\', '..', or an absolute path.

    Proven exploit (pre-launch audit): POST /api/workspaces
    {"name": "../../../../Temp/x"} created a real directory outside
    ~/.model-hub/workspaces (invisible to list_workspaces(), which only
    lists direct children). delete_workspace() had the identical
    unsanitized join before shutil.rmtree() -- an equally real arbitrary
    recursive-delete primitive. Raises ValueError if ws_id would not land
    strictly inside _workspaces_dir().
    """
    base = _workspaces_dir().resolve()
    candidate = (base / ws_id).resolve()
    if candidate == base or base not in candidate.parents:
        raise ValueError(f"invalid workspace id: {ws_id!r}")
    return candidate


def create_workspace(name: str, description: str = "") -> Workspace:
    import time
    ws_id = name.lower().replace(" ", "-").replace("_", "-")
    ws_dir = _resolve_within_workspaces(ws_id)
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Windows refuses to create directories named after reserved device
        # names (con, nul, aux, prn, com1-9, lpt1-9) even though they pass
        # the path-traversal guard above -- mkdir() raises OSError, not
        # ValueError. Re-raise as ValueError so both call sites (api.py's
        # api_create_workspace() and cli.py's create-workspace handler),
        # which already catch ValueError from _resolve_within_workspaces,
        # reject this cleanly instead of surfacing an unhandled 500/traceback.
        raise ValueError(f"Could not create workspace directory: {e}")
    now = time.time()
    meta = ws_dir / "workspace.json"
    meta.write_text(json.dumps({
        "name": name,
        "description": description,
        "created_at": now,
    }, indent=2))
    return Workspace(id=ws_id, name=name, description=description, created_at=now)


def delete_workspace(workspace_id: str) -> bool:
    if workspace_id == DEFAULT_WORKSPACE:
        return False
    try:
        ws_dir = _resolve_within_workspaces(workspace_id)
    except ValueError:
        return False
    if ws_dir.exists():
        import shutil
        shutil.rmtree(ws_dir)
        return True
    return False


def switch_workspace(workspace_id: str) -> bool:
    if not get_workspace(workspace_id):
        return False
    config = load_config()
    config.workspace = workspace_id
    save_config(config)
    return True


def ensure_workspace() -> str:
    config = load_config()
    ws = get_workspace(config.workspace)
    if not ws:
        _create_default_workspace()
        config.workspace = DEFAULT_WORKSPACE
        save_config(config)
    return config.workspace
