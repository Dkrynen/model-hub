import asyncio
import copy
import hashlib
import ipaddress
import json
import os
import platform
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, Response, jsonify, request, stream_with_context

from . import self_invoke
from .agent import Agent, AgentRunner, FULL_PERMISSIONS, READONLY_PERMISSIONS
from .agent.runner import PreparedToolCall
from .agent.sandbox import DockerTaskBroker, SandboxError, probe_project_sandbox
from .agent.staging import build_staged_handlers
from .config import UnsafeProjectConfigError, resolve_config
from .cookbook import proc
from .cookbook.config import load_config
from .cookbook.downloads import download_history
from .cookbook.hardware import detect, print_system
from .cookbook.recommend import recommend, load_models
from .cloud_session import CloudSession, CloudSessionError
from .cloud_tokens import SecureTokenStoreError
from .plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
from .permission import PermissionEngine
from .pro_install import install_pro_plugin
from .provider.registry import default_provider
from .provider.ollama import OllamaProvider
from .update import is_newer, select_release_download_url

try:
    from .version import __version__ as APP_VERSION, __github_url__, __download_url__
except ImportError:
    APP_VERSION = "0.0.0"
    __github_url__ = "https://github.com/Dkrynen/lac"
    __download_url__ = "https://github.com/Dkrynen/lac/releases"

# Serve the built web app (web/dist) when present, else the legacy frontend/.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
_STATIC = str(_DIST) if (_DIST / "index.html").exists() else str(_FRONTEND)
app = Flask(__name__, static_folder=_STATIC, static_url_path="", template_folder=_STATIC)
_cloud_session = CloudSession()

_TRUSTED_BROWSER_HOSTNAMES = {
    "localhost",
    str(platform.node() or "").strip().lower().rstrip("."),
}
_TRUSTED_BROWSER_HOSTNAMES.discard("")


def _trusted_authority(value: str) -> tuple[str, int | None] | None:
    """Return a normalized trusted Host authority, rejecting DNS-rebind names."""

    raw = str(value or "").strip()
    if not raw or any(char in raw for char in "/\\?#@"):
        return None
    try:
        parsed = urlsplit(f"//{raw}")
        if parsed.username is not None or parsed.password is not None:
            return None
        host = str(parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    if host in _TRUSTED_BROWSER_HOSTNAMES:
        return host, port
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host, port


def _trusted_origin(value: str) -> tuple[str, str, int | None] | None:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        authority = _trusted_authority(parsed.netloc)
    except ValueError:
        return None
    if authority is None:
        return None
    return parsed.scheme, authority[0], authority[1]


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return 443 if scheme == "https" else 80 if scheme == "http" else None


@app.before_request
def _enforce_trusted_browser_request():
    """Block DNS rebinding and cross-origin browser access to the local API."""

    authority = _trusted_authority(request.host)
    if authority is None:
        return jsonify({"error": "Untrusted request host"}), 403
    origin_raw = request.headers.get("Origin")
    if not origin_raw:
        return None
    origin = _trusted_origin(origin_raw)
    if origin is None:
        return jsonify({"error": "Untrusted request origin"}), 403
    origin_scheme, origin_host, origin_port = origin
    if (
        origin_scheme != request.scheme
        or origin_host != authority[0]
        or _effective_port(origin_scheme, origin_port)
        != _effective_port(request.scheme, authority[1])
    ):
        return jsonify({"error": "Cross-origin request rejected"}), 403
    return None


def _configure_trusted_server_host(host: str) -> None:
    """Allow an explicit named bind host while retaining DNS-rebinding checks."""

    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized and normalized not in {"0.0.0.0", "::", "[::]"}:
        try:
            ipaddress.ip_address(normalized.strip("[]"))
        except ValueError:
            _TRUSTED_BROWSER_HOSTNAMES.add(normalized)

PULL_PROGRESS = {}
PULL_PROGRESS_LOCK = threading.Lock()
_PULL_TERMINAL_STATES = {"completed", "failed", "cancelled"}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
INTERACTIVE_CONTEXT_FALLBACK = 4096


def _set_pull_progress(model_name: str, **patch) -> dict:
    now = time.time()
    with PULL_PROGRESS_LOCK:
        entry = dict(PULL_PROGRESS.get(model_name) or {
            "model": model_name,
            "state": "starting",
            "status": "starting",
            "completed": 0,
            "total": 0,
            "percent": 0,
            "started_at": now,
        })
        entry.update(patch)
        entry["model"] = model_name
        entry["updated_at"] = now
        PULL_PROGRESS[model_name] = entry
        return dict(entry)


def _pull_progress_snapshot(model_name: str | None = None) -> dict:
    with PULL_PROGRESS_LOCK:
        if model_name:
            entry = PULL_PROGRESS.get(model_name)
            return dict(entry) if entry else {"model": model_name, "state": "idle"}
        pulls = [dict(v) for v in PULL_PROGRESS.values()]
    pulls.sort(key=lambda x: float(x.get("updated_at") or 0), reverse=True)
    return {
        "active": sum(1 for p in pulls if p.get("state") not in _PULL_TERMINAL_STATES),
        "pulls": pulls,
    }


ASK_TIMEOUT = 300.0
ANSWER_ACK_TIMEOUT = 5.0
HEARTBEAT_INTERVAL = 15.0
WORKER_JOIN_TIMEOUT = 2.0
AGENT_EVENT_QUEUE_MAX = 256
QUEUE_PUT_TIMEOUT = 0.1
MAX_AGENT_RUNS = 32
_RUN_SENTINEL = object()
_ASK_COMMIT_FAILED = object()


@dataclass(frozen=True)
class _PersistedAgentEvent:
    """Queue wrapper for an event already committed to the session timeline."""

    payload: dict


@dataclass(frozen=True)
class _TerminalAgentEvent:
    """Queue wrapper for a terminal event journaled independently by its route."""

    payload: dict


class _PersistedRunEventSink:
    """Persist handler-originated events before publishing them to SSE."""

    def __init__(self, run: "_AgentRun"):
        self.run = run

    def put(self, event: dict) -> None:
        payload = dict(event)
        _persist_run_event(self.run, payload)
        _put_run_item(self.run, _PersistedAgentEvent(payload))


@dataclass
class _AgentRun:
    """Registry entry bridging a streaming agent run and the answer endpoint."""

    ask_event: threading.Event
    queue: "queue.Queue"
    session_id: str
    created_at: float
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    answer: object | None = None       # Decision | None; None at timeout/disconnect => DENY
    remember: bool = False
    pending_ask: dict | None = None
    cancelled: bool = False
    thread: threading.Thread | None = None
    approval_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    cancel_reason: str | None = None
    cancel_announced: bool = False
    cancel_journaled: bool = False
    cancel_journal_in_progress: bool = False
    worker_done: threading.Event = field(default_factory=threading.Event, repr=False)
    persistence_done: threading.Event = field(default_factory=threading.Event, repr=False)
    state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_AGENT_RUNS: dict[str, _AgentRun] = {}
_AGENT_RUNS_LOCK = threading.Lock()


class _AgentSessionBusyError(RuntimeError):
    pass


def _register_agent_run(run_id: str, run: _AgentRun) -> None:
    now = time.time()
    with _AGENT_RUNS_LOCK:
        stale = [
            rid
            for rid, r in _AGENT_RUNS.items()
            if now - r.created_at > 3600 and (r.thread is None or not r.thread.is_alive())
        ]
        for rid in stale:
            _AGENT_RUNS.pop(rid, None)
        if any(existing.session_id == run.session_id for existing in _AGENT_RUNS.values()):
            raise _AgentSessionBusyError("Thread already has an active run")
        if len(_AGENT_RUNS) >= MAX_AGENT_RUNS:
            raise RuntimeError("Too many active agent runs")
        _AGENT_RUNS[run_id] = run


def _release_agent_run_if_finished(run_id: str, run: _AgentRun) -> None:
    """Release a thread lease only after both worker and persistence settle."""

    if not (run.worker_done.is_set() and run.persistence_done.is_set()):
        return
    with _AGENT_RUNS_LOCK:
        if _AGENT_RUNS.get(run_id) is run:
            _AGENT_RUNS.pop(run_id, None)


def _persist_run_event(run: _AgentRun, event: dict) -> None:
    from .cookbook.persistence import add_session_event

    add_session_event(run.session_id, str(event["type"]), event)


def _put_run_item(run: _AgentRun, item: object, *, terminal: bool = False) -> bool:
    """Bound a run buffer while guaranteeing room for terminal cancellation."""

    if terminal:
        # A cancelled producer cannot rely on the browser draining a full
        # queue. Drop the oldest now-irrelevant SSE item until the durable
        # cancellation event or sentinel fits; persisted events remain in the
        # session journal even when displaced from the wire.
        while True:
            try:
                run.queue.put_nowait(item)
                return True
            except queue.Full:
                try:
                    run.queue.get_nowait()
                except queue.Empty:
                    continue

    while not run.cancelled:
        try:
            run.queue.put(item, timeout=QUEUE_PUT_TIMEOUT)
            return True
        except queue.Full:
            continue
    return False


def _ask_is_rememberable(tool_name: str, target: object, key: str) -> bool:
    """Return the backend's single source of truth for remembered approvals."""

    from .permission.engine import is_dangerous

    if tool_name == "run_task":
        # A named task can execute different project code as staged content
        # evolves, even though its argv is fixed. It must always be allow-once.
        return False
    return bool(
        key != "doom_loop"
        and target is not None
        and not is_dangerous(tool_name, target)
    )


def _make_web_ask(run_id: str, run: _AgentRun):
    """Bridge an async runner ask to SSE output and a separate answer POST."""

    async def on_ask(agent_name: str, tool_name: str, target, key: str):
        from .agent.runner import AskResult
        from .permission import Decision

        ask_id = uuid.uuid4().hex
        rememberable = _ask_is_rememberable(tool_name, target, key)
        ask_event = {
            "type": "ask",
            "run_id": run_id,
            "ask_id": ask_id,
            "tool": tool_name,
            "target": target,
            "key": key,
            "doom_loop": key == "doom_loop",
            "rememberable": rememberable,
        }
        with run.state_lock:
            if run.cancelled:
                return AskResult(decision=Decision.DENY)
            if run.pending_ask is not None:
                raise RuntimeError("agent run already has a pending ask")
            # Persist while holding the state lock, before publishing
            # pending_ask. Disconnect can therefore never journal a terminal
            # resolution ahead of the ask it resolves.
            _persist_run_event(run, ask_event)
            run.answer = None
            run.remember = False
            run.ask_event.clear()
            run.cancel_reason = None
            run.pending_ask = {
                "ask_id": ask_id,
                "tool": tool_name,
                "target": target,
                "key": key,
                "consumed_event": threading.Event(),
                "result": None,
            }
        _put_run_item(run, _PersistedAgentEvent(dict(ask_event)))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, run.ask_event.wait, ASK_TIMEOUT)

        with run.state_lock:
            pending = run.pending_ask
            matches = pending is not None and pending.get("ask_id") == ask_id
            if not matches:
                return AskResult(decision=Decision.DENY)
            if run.cancelled:
                decision = Decision.DENY
                remember = False
            elif run.ask_event.is_set():
                decision = run.answer if isinstance(run.answer, Decision) else Decision.DENY
                remember = bool(
                    run.remember and decision is Decision.ALLOW and key != "doom_loop"
                )
            else:
                # Close the ask before returning to AgentRunner so an answer
                # arriving just after the timeout cannot become executable.
                decision = Decision.DENY
                remember = False
                pending["timed_out"] = True
                run.ask_event.set()
            pending["claimed"] = True

        def commit_answer(
            remember_action,
            rollback_action,
            grant_id,
            remember_allowed=True,
        ):
            resolution = None
            failure = None
            failure_reason = None
            remembered = False
            persisted_events = []
            commit_exception = None
            with run.state_lock:
                pending = run.pending_ask
                matches = pending is not None and pending.get("ask_id") == ask_id
                if not matches:
                    return Decision.DENY

                if run.cancelled:
                    actual = Decision.DENY
                    actual_remember = False
                    resolution = {
                        "type": "ask_resolved",
                        "run_id": run_id,
                        "ask_id": ask_id,
                        "tool": tool_name,
                        "decision": "deny",
                        "remember": False,
                        "reason": run.cancel_reason or "cancelled",
                    }
                elif pending.get("timed_out"):
                    actual = Decision.DENY
                    actual_remember = False
                    resolution = {
                        "type": "ask_timeout",
                        "run_id": run_id,
                        "ask_id": ask_id,
                        "tool": tool_name,
                    }
                else:
                    actual = run.answer if isinstance(run.answer, Decision) else Decision.DENY
                    if run.cancel_reason == "answer_ack_timeout":
                        actual = Decision.DENY
                    requested_remember = bool(
                        remember_allowed
                        and rememberable
                        and run.remember
                        and actual is Decision.ALLOW
                        and key != "doom_loop"
                    )
                    actual_remember = requested_remember
                    if requested_remember:
                        if remember_action is None or not grant_id:
                            failure = RuntimeError(
                                "remembered approval has no persistence action or grant id"
                            )
                            failure_reason = "remember_persistence_unavailable"
                            actual = Decision.DENY
                            actual_remember = False
                        else:
                            remember_started = {
                                "type": "ask_remember_started",
                                "run_id": run_id,
                                "ask_id": ask_id,
                                "grant_id": grant_id,
                                "agent": agent_name,
                                "tool": tool_name,
                                "target": target,
                                "key": key,
                                "decision": "allow",
                                "remember": True,
                            }
                            try:
                                # Load-bearing ordering invariant: an active
                                # remembered permission must always have a
                                # durable audit record that predates it.
                                _persist_run_event(run, remember_started)
                                persisted_events.append(remember_started)
                            except Exception as e:
                                failure = e
                                failure_reason = "remember_audit_start_failed"
                                actual = Decision.DENY
                                actual_remember = False
                            if failure is None:
                                try:
                                    committed_grant_id = remember_action()
                                    if committed_grant_id != grant_id:
                                        raise RuntimeError(
                                            "permission store committed a different grant id"
                                        )
                                    remembered = True
                                except Exception as e:
                                    failure = e
                                    failure_reason = "remember_persistence_failed"
                                    if rollback_action is not None:
                                        try:
                                            rollback_action()
                                        except Exception as rollback_error:
                                            failure = RuntimeError(
                                                f"remember failed and rollback failed: {e}; {rollback_error}"
                                            )
                                    actual = Decision.DENY
                                    actual_remember = False
                    resolution = {
                        "type": "ask_resolved",
                        "run_id": run_id,
                        "ask_id": ask_id,
                        "tool": tool_name,
                        "decision": actual.value,
                        "remember": actual_remember,
                    }
                    if requested_remember and grant_id:
                        resolution["grant_id"] = grant_id
                    if run.cancel_reason == "answer_ack_timeout":
                        resolution["reason"] = "answer_ack_timeout"
                    elif failure is not None:
                        resolution["reason"] = failure_reason or "remember_persistence_failed"

                try:
                    _persist_run_event(run, resolution)
                except Exception as e:
                    if remembered and rollback_action is not None:
                        try:
                            rollback_action()
                        except Exception as rollback_error:
                            e = RuntimeError(
                                f"approval audit failed and rollback failed: {e}; {rollback_error}"
                            )
                    commit_exception = e
                else:
                    persisted_events.append(resolution)

                if commit_exception is not None or failure is not None:
                    pending["result"] = _ASK_COMMIT_FAILED
                    pending["error"] = str(commit_exception or failure)
                else:
                    pending["result"] = actual
                pending["consumed_event"].set()
                run.pending_ask = None
                run.answer = None
                run.remember = False

            for persisted_event in persisted_events:
                _put_run_item(run, _PersistedAgentEvent(dict(persisted_event)))
            if commit_exception is not None:
                raise commit_exception
            if failure is not None:
                raise RuntimeError(f"failed to persist remembered approval: {failure}")
            return actual

        return AskResult(decision=decision, remember=remember, _commit=commit_answer)

    return on_ask

MODEL_WEIGHT_EXTS = {".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth"}
HF_IMPORT_TMP_ENV = "LAC_HF_IMPORT_TMP"
HF_IMPORT_TMP_LEGACY_ENV = "LAC_IMPORT_TMP"
HF_DETAIL_CACHE_TTL_S = 10 * 60
HF_DETAIL_CACHE_MAX = 256
_HF_DETAIL_CACHE: dict[str, tuple[float, dict | None]] = {}
_HF_DETAIL_CACHE_LOCK = threading.Lock()


def _safe_dir_size(path: Path) -> int | None:
    if not path.exists():
        return 0
    try:
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return None


def _default_ollama_models_dir() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured).expanduser()
    if platform.system().lower() == "linux":
        return Path("/usr/share/ollama/.ollama/models")
    return Path.home() / ".ollama" / "models"


def _hf_import_scratch_root() -> Path:
    configured = os.environ.get(HF_IMPORT_TMP_ENV) or os.environ.get(HF_IMPORT_TMP_LEGACY_ENV)
    if configured:
        return Path(configured).expanduser()
    ollama_models = os.environ.get("OLLAMA_MODELS")
    if ollama_models:
        return Path(ollama_models).expanduser().parent / "lac-hf-import-tmp"
    return Path.home() / ".model-hub" / "hf-import-tmp"


def _disk_usage_target(path: Path) -> Path:
    current = path.expanduser()
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _storage_volume_identity(path: Path) -> int:
    return int(os.stat(_disk_usage_target(path)).st_dev)


def _disk_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(_disk_usage_target(path)).free
    except OSError:
        return None


def _disk_usage_payload(path: Path) -> dict:
    try:
        usage = shutil.disk_usage(_disk_usage_target(path))
        return {
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_gb": _bytes_to_gb_display(usage.free),
            "total_gb": _bytes_to_gb_display(usage.total),
            "used_gb": _bytes_to_gb_display(usage.used),
        }
    except OSError:
        return {
            "free_bytes": None,
            "total_bytes": None,
            "used_bytes": None,
            "free_gb": None,
            "total_gb": None,
            "used_gb": None,
        }


def _count_dir_entries(path: Path) -> int | None:
    if not path.exists():
        return 0
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return None


def _is_safe_import_scratch_dir(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    if resolved.parent == resolved:
        return False
    if len(resolved.parts) < 3:
        return False
    lowered = {part.lower() for part in resolved.parts}
    name = resolved.name.lower()
    if ".model-hub" in lowered and ("import" in name or "scratch" in name or "tmp" in name):
        return True
    return name in {"lac-hf-import-tmp", "hf-import-tmp", "lac-hf-import"} or (
        name.startswith("lac-") and ("import" in name or "scratch" in name or "tmp" in name)
    )


def _clear_directory_contents(path: Path) -> dict:
    if not path.exists():
        return {"deleted_entries": 0, "deleted_bytes": 0}
    deleted_entries = 0
    deleted_bytes = _safe_dir_size(path) or 0
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        deleted_entries += 1
    return {"deleted_entries": deleted_entries, "deleted_bytes": deleted_bytes}


def _read_user_env_var(name: str) -> str | None:
    if platform.system().lower() != "windows":
        return os.environ.get(name)
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _write_user_env_var(name: str, value: str | None) -> None:
    if platform.system().lower() != "windows":
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        return

    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            None,
        )
    except Exception:
        pass


def _model_location_payload(restart_required: bool = False) -> dict:
    user_value = _read_user_env_var("OLLAMA_MODELS")
    process_value = os.environ.get("OLLAMA_MODELS")
    default_dir = Path.home() / ".ollama" / "models"
    if platform.system().lower() == "linux":
        default_dir = Path("/usr/share/ollama/.ollama/models")
    effective_after_restart = Path(user_value).expanduser() if user_value else default_dir
    current_process_dir = _default_ollama_models_dir()
    return {
        "state": "ok",
        "platform": platform.system().lower(),
        "env_var": "OLLAMA_MODELS",
        "configured": bool(user_value),
        "configured_dir": str(Path(user_value).expanduser()) if user_value else None,
        "process_configured": bool(process_value),
        "process_dir": str(current_process_dir),
        "default_dir": str(default_dir),
        "effective_after_restart": str(effective_after_restart),
        "current_size_bytes": _safe_dir_size(current_process_dir),
        "configured_size_bytes": _safe_dir_size(effective_after_restart),
        "restart_ollama_required": restart_required or (user_value != process_value),
        "restart_lac_required": user_value != process_value,
        "moves_existing_models": False,
    }


def _app_payload_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_model_weight_files(path: Path, limit: int = 10) -> list[dict]:
    if not getattr(sys, "frozen", False) or not path.exists():
        return []
    found = []
    try:
        for child in path.rglob("*"):
            if child.is_file() and child.suffix.lower() in MODEL_WEIGHT_EXTS:
                found.append({
                    "path": str(child.relative_to(path)),
                    "size_bytes": child.stat().st_size,
                })
                if len(found) >= limit:
                    break
    except OSError:
        return found
    return found


def _serialize_split_plan(plan) -> dict:
    """Serialize a SplitPlan dataclass to a JSON-safe dict for the API."""
    return {
        "run_mode": plan.run_mode,
        "summary": plan.summary,
        "total_model_gb": plan.total_model_gb,
        "total_layers": plan.total_layers,
        "gpu_layers": plan.gpu_layers,
        "env_vars": plan.env_vars,
        "tiers": [
            {
                "kind": a.kind, "name": a.name, "memory_gb": a.memory_gb,
                "allocated_gb": a.allocated_gb, "backend": a.backend,
                "device_index": a.device_index, "bandwidth": a.bandwidth,
                "layers": a.layers,
            }
            for a in plan.tiers
        ],
    }


def _ollama_request(
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    stream: bool = False,
    timeout: int = 30,
):
    import urllib.request
    import urllib.error
    url = f"{OLLAMA_HOST}{path}"
    data = json.dumps(json_body).encode() if json_body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        if stream:
            return resp
        raw = resp.read().decode()
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    except urllib.error.HTTPError as e:
        try:
            return {"error": f"Ollama HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception:
            return {"error": f"Ollama HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to Ollama at {OLLAMA_HOST}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _interactive_context() -> int:
    """Context used by interactive warm/chat paths.

    The recommendation and calibration stack assumes a 4k default context.
    Large Ollama models can advertise 128k+ contexts, which is great when a
    user asks for it, but painful as an implicit chat/warm default.
    """
    try:
        from .config import resolve_config
        ctx = resolve_config().default_context
    except Exception:  # noqa: BLE001
        try:
            from .cookbook.config import load_config
            ctx = load_config().default_context
        except Exception:  # noqa: BLE001
            ctx = INTERACTIVE_CONTEXT_FALLBACK
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        return INTERACTIVE_CONTEXT_FALLBACK
    return ctx if ctx > 0 else INTERACTIVE_CONTEXT_FALLBACK

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/docs")
@app.route("/docs/api")
@app.route("/docs/guide")
def docs_page():
    # Docs is a client-side route in the new web app.
    return app.send_static_file("index.html")


@app.route("/api/openapi.json")
def openapi_spec():
    from .openapi_gen import generate_openapi

    return jsonify(generate_openapi(app, f"http://127.0.0.1:5050"))


@app.route("/api/scan")
def api_scan():
    info = detect()
    return jsonify({
        "os": info.os,
        "cpu": info.cpu,
        "cores": info.cpu_cores,
        "ram_gb": info.ram_gb,
        "gpus": [{"name": g.name, "vram_gb": g.vram_gb, "backend": g.backend,
                  "tier": g.tier, "device_index": g.device_index} for g in info.gpus],
        "total_vram_gb": info.total_vram_gb,
        "combined_vram_gb": info.combined_vram_gb,
        "compute_tiers": [
            {"name": t.name, "memory_gb": t.memory_gb, "backend": t.backend,
             "kind": t.kind, "device_index": t.device_index}
            for t in info.compute_tiers
        ],
        "is_apple_silicon": info.is_apple_silicon,
        "in_container": info.in_container,
    })


@app.route("/api/recommend")
def api_recommend():
    vram = request.args.get("vram", type=float, default=0)
    use_case = request.args.get("use_case", default="coding")
    top_k = request.args.get("top_k", type=int, default=5)
    no_calibration = request.args.get("no_calibration", type=int, default=0)
    gpu_mask_raw = request.args.get("gpu_mask", "")
    allow_spill = request.args.get("allow_spill", type=int, default=1)

    info = detect()
    if vram and vram > 0:
        info.total_vram_gb = vram
        for gpu in info.gpus:
            if "radeon" in gpu.name.lower() or "amd" in gpu.name.lower():
                gpu.vram_gb = vram
        if not info.gpus:
            from .cookbook.hardware import GPUInfo
            info.gpus = [GPUInfo(name=f"Manual ({vram} GB)", vram_gb=vram, backend="cuda")]
        # Manual override updates fit-scoring via total_vram_gb/gpus above,
        # but combined_vram_gb is a separate display field detect() already
        # computed pre-override -- keep it in sync or the UI shows a stale
        # number next to the correctly-overridden one.
        info.combined_vram_gb = round(sum(g.vram_gb for g in info.gpus), 1)

    mask = {int(x) for x in gpu_mask_raw.split(",") if x.strip().isdigit()} if gpu_mask_raw else set()
    if mask:
        masked_gpus = [g for g in info.gpus if g.device_index in mask]
        if masked_gpus:  # fail-safe: a mask matching no GPU is ignored, never a zero-GPU result
            info.gpus = masked_gpus
            info.compute_tiers = [t for t in info.compute_tiers if t.kind == "ram" or t.device_index in mask]
            gpu_vrams = [g.vram_gb for g in info.gpus]
            info.total_vram_gb = round(max(gpu_vrams), 1)
            info.combined_vram_gb = round(sum(gpu_vrams), 1)

    if not allow_spill:
        info.compute_tiers = [t for t in info.compute_tiers if t.kind != "ram"]
        info.ram_gb = 0.0

    # Build the per-machine calibration from benchmarked results (mirrors cli.cmd_recommend).
    if no_calibration:
        _cal = None
    else:
        from .cookbook.calibration import load_calibration, detect_stack
        _stack = detect_stack(info=info)
        _results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
        _cal = load_calibration(info, _stack, _results)

    recs = recommend(info, use_case=use_case, top_k=top_k, calibration=_cal)
    return jsonify({
        "vram_gb": info.total_vram_gb,
        "combined_vram_gb": info.combined_vram_gb,
        "ram_gb": info.ram_gb,
        "recommendations": [
            {
                "name": r.model.name,
                "model_id": r.model.id,
                "provider": r.model.provider,
                "params_b": r.model.params_b,
                "quant": r.quant,
                "score": r.score,
                "vram_gb": r.vram_gb,
                "context": r.context_used,
                "run_mode": r.run_mode,
                "ollama_cmd": r.ollama_cmd,
                "speed_source": r.speed_source,
                "speed_band_pct": r.speed_band_pct,
                "scores": {
                    "quality": r.quality_score,
                    "speed": r.speed_score,
                    "fit": r.fit_score,
                    "context": r.context_score,
                },
                "split_plan": _serialize_split_plan(r.split_plan) if r.split_plan else None,
            }
            for r in recs
        ],
    })


@app.route("/api/models")
def api_models():
    all_models = load_models()
    return jsonify([
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "params_b": m.params_b,
            "arch": m.arch,
            "context": m.context,
            "use_cases": m.use_cases,
            "is_moe": m.is_moe,
            "vram_q4": m.vram_q4,
            "vram_q8": m.vram_q8,
            "vram_f16": m.vram_f16,
        }
        for m in all_models
    ])


@app.route("/api/ollama/status")
def ollama_status():
    resp = _ollama_request("GET", "/api/version")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify({"running": False, "version": None, "error": resp.get("error") if isinstance(resp, dict) else None})
    return jsonify({
        "running": True,
        "version": resp.get("version", "unknown"),
    })


@app.route("/api/ollama/models")
def ollama_models():
    resp = _ollama_request("GET", "/api/tags")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify({"error": "Ollama model inventory unavailable"}), 502
    models = []
    for m in resp.get("models", []):
        digest = m.get("digest", "")
        models.append({
            "name": m.get("name"),
            "size_gb": round(m.get("size", 0) / (1024**3), 2),
            "modified": m.get("modified_at", ""),
            "digest_short": digest[:12] if digest else "",
        })
    return jsonify(sorted(models, key=lambda x: x["name"]))


_OLLAMA_PROFILE_MAX_MODELS = 2
_OLLAMA_MODEL_INFO_SCAN_LIMIT = 256
_OLLAMA_CONTEXT_LENGTH_MAX = 16_777_216


def _nullable_ollama_string(value: object) -> str | None:
    """Keep Ollama-reported strings exact while normalizing missing values."""
    return value if isinstance(value, str) and value else None


def _normalize_ollama_profile(tag: object) -> dict | None:
    """Project one /api/tags row onto the public model-profile allowlist."""
    if not isinstance(tag, dict):
        return None
    name = tag.get("name")
    if not isinstance(name, str) or not name:
        return None

    details = tag.get("details")
    if not isinstance(details, dict):
        details = {}

    families_raw = details.get("families")
    families = None
    if isinstance(families_raw, list):
        normalized_families = [
            family for family in families_raw[:16]
            if isinstance(family, str) and family
        ]
        families = normalized_families or None

    size = tag.get("size")
    size_gb = 0.0
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size >= 0:
        size_gb = round(float(size) / (1024**3), 2)

    digest = tag.get("digest")
    if not isinstance(digest, str):
        digest = ""
    modified = tag.get("modified_at")
    if not isinstance(modified, str):
        modified = ""

    return {
        "name": name,
        "size_gb": size_gb,
        "modified": modified,
        "digest": digest,
        "digest_short": digest[:12] if digest else "",
        "format": _nullable_ollama_string(details.get("format")),
        "family": _nullable_ollama_string(details.get("family")),
        "families": families,
        "parameter_size": _nullable_ollama_string(details.get("parameter_size")),
        "quantization_level": _nullable_ollama_string(details.get("quantization_level")),
        "context_length": None,
    }


def _extract_ollama_context_length(model_info: object) -> int | None:
    """Extract one unambiguous, bounded ``*.context_length`` value.

    Ollama prefixes model-info keys with the architecture name.  Matching the
    suffix avoids an architecture allowlist. Oversized input is rejected rather
    than partially scanned, so a conflicting value can never hide past the
    endpoint's processing bound.
    """
    if not isinstance(model_info, dict):
        return None
    if len(model_info) > _OLLAMA_MODEL_INFO_SCAN_LIMIT:
        return None

    values: set[int] = set()
    for key, value in model_info.items():
        if not isinstance(key, str) or not key.endswith(".context_length"):
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if not 0 < value <= _OLLAMA_CONTEXT_LENGTH_MAX:
            continue
        values.add(value)
        if len(values) > 1:
            return None
    return next(iter(values)) if values else None


@app.route("/api/ollama/model-profiles", methods=["POST"])
def ollama_model_profiles():
    """Return safe local evidence for one or two exact installed model names."""
    data = request.get_json(silent=True)
    requested = data.get("models") if isinstance(data, dict) else None
    if (
        not isinstance(requested, list)
        or not 1 <= len(requested) <= _OLLAMA_PROFILE_MAX_MODELS
        or any(
            not isinstance(name, str) or not name or name != name.strip()
            for name in requested
        )
    ):
        return jsonify({"error": "models must contain one or two exact model names"}), 400
    if len(set(requested)) != len(requested):
        return jsonify({"error": "models must be unique"}), 400

    tags_response = _ollama_request("GET", "/api/tags")
    if not isinstance(tags_response, dict) or "error" in tags_response:
        return jsonify({"error": "Ollama model inventory unavailable"}), 502

    installed: dict[str, dict] = {}
    tag_rows = tags_response.get("models")
    if isinstance(tag_rows, list):
        for row in tag_rows:
            profile = _normalize_ollama_profile(row)
            if profile is not None:
                installed[profile["name"]] = profile

    missing = [name for name in requested if name not in installed]
    if missing:
        return jsonify({"error": "model is not installed", "models": missing}), 404

    profiles = []
    for name in requested:
        profile = installed[name]
        show_response = _ollama_request(
            "POST", "/api/show", {"model": name}, timeout=10,
        )
        if isinstance(show_response, dict) and "error" not in show_response:
            profile["context_length"] = _extract_ollama_context_length(
                show_response.get("model_info")
            )
        profiles.append(profile)

    return jsonify({"profiles": profiles})


@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        from .cookbook.downloads import log_download
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last_total = 0
        last_completed = 0
        _set_pull_progress(model_name, state="starting", status="starting")
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    try:
                        chunk = json.loads(decoded)
                    except json.JSONDecodeError:
                        chunk = {}
                    completed = int(chunk.get("completed") or last_completed or 0)
                    total = int(chunk.get("total") or last_total or 0)
                    if completed:
                        last_completed = completed
                    if total:
                        last_total = total
                    status = str(chunk.get("status") or "running")
                    state = "completed" if status == "success" else "running"
                    percent = min(100, round((completed / total) * 100)) if total > 0 else 0
                    _set_pull_progress(
                        model_name,
                        state=state,
                        status=status,
                        completed=completed,
                        total=total,
                        percent=100 if state == "completed" else percent,
                    )
                    yield f"data: {decoded}\n\n"
                    if chunk.get("status") == "success":
                        size_gb = round(last_total / (1024**3), 2) if last_total else 0
                        log_download(model_name, "completed", size_gb)
                        _notify_model_installed_async(model_name)
        except GeneratorExit:
            size_gb = round(last_total / (1024**3), 2) if last_total else 0
            _set_pull_progress(model_name, state="cancelled", status="cancelled",
                               completed=last_completed, total=last_total)
            log_download(model_name, "cancelled", size_gb)
            raise
        except urllib.error.HTTPError as e:
            message = str(e)
            _set_pull_progress(model_name, state="failed", status="failed", error=message,
                               completed=last_completed, total=last_total)
            log_download(model_name, "failed", 0)
            yield f"data: {json.dumps({'error': message})}\n\n"
        except Exception as e:
            message = str(e)
            _set_pull_progress(model_name, state="failed", status="failed", error=message,
                               completed=last_completed, total=last_total)
            log_download(model_name, "failed", 0)
            yield f"data: {json.dumps({'error': message})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/ollama/pull-status")
def ollama_pull_status():
    model_name = request.args.get("model", "").strip()
    return jsonify(_pull_progress_snapshot(model_name or None))


@app.route("/api/ollama/delete", methods=["POST"])
def ollama_delete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    result = _ollama_request("DELETE", f"/api/delete", {"name": model_name}, timeout=120)
    if isinstance(result, dict) and "error" in result:
        return jsonify({"error": result["error"]}), 500
    return jsonify({"success": True})


def _warm_ollama(model: str) -> dict:
    """Load `model` into VRAM (no generation) and keep it resident. Never raises."""
    import urllib.request
    try:
        body = json.dumps({
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": _interactive_context()},
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        raw = urllib.request.urlopen(req, timeout=600).read()
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:  # noqa: BLE001 - warming should not fail on a bad metrics body
            data = {}
        return {
            "state": "warm",
            "model": model,
            "load_ms": round((data.get("load_duration") or 0) / 1e6, 1),
            "total_ms": round((data.get("total_duration") or 0) / 1e6, 1),
        }
    except Exception as exc:  # noqa: BLE001 - caller decides whether to surface this
        return {"state": "failed", "model": model, "error": str(exc)}


@app.route("/api/ollama/warm", methods=["POST"])
def ollama_warm():
    """Preload a model into VRAM off the chat critical path so the first message
    doesn't pay the cold-load penalty. By default this is fire-and-forget; pass
    {"wait": true} when the UI needs to block sending until the model is loaded."""
    data = request.get_json(silent=True)
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, str) or not model.strip():
        return jsonify({"error": "model required"}), 400
    wait = bool(data.get("wait")) if isinstance(data, dict) else False
    if wait:
        return jsonify(_warm_ollama(model.strip())), 200
    threading.Thread(target=_warm_ollama, args=(model.strip(),), daemon=True).start()
    return jsonify({"accepted": True}), 200


def _unmeasured_performance_diagnosis(summary: str) -> dict:
    return {
        "state": "unmeasured",
        "summary": summary,
        "signals": [],
        "actions": [
            {"kind": "probe", "label": "Run a latency probe"},
            {"kind": "benchmark", "label": "Run Pro tuning for deeper GPU-offload data"},
        ],
    }


def _diagnose_performance(metrics: dict | None) -> dict:
    if not metrics:
        return _unmeasured_performance_diagnosis("No Ollama measurement yet.")

    tps = float(metrics.get("tokens_per_second") or 0)
    pre_generation_ms = float(metrics.get("time_to_first_token_ms") or 0)
    load_ms = float(metrics.get("load_duration_ms") or 0)
    prompt_ms = float(metrics.get("prompt_eval_duration_ms") or 0)
    total_ms = float(metrics.get("total_duration_ms") or 0)
    eval_ms = float(metrics.get("eval_duration_ms") or 0)
    eval_count = float(metrics.get("eval_count") or 0)
    if not any(value > 0 for value in (
        tps,
        pre_generation_ms,
        load_ms,
        prompt_ms,
        total_ms,
        eval_ms,
        eval_count,
    )):
        return _unmeasured_performance_diagnosis(
            "No usable Ollama measurement was reported."
        )

    signals = []
    actions = []

    if load_ms >= 3000:
        signals.append({
            "kind": "cold_load",
            "severity": "warning",
            "label": "Cold load dominates first response",
            "value_ms": round(load_ms, 1),
        })
        actions.append({"kind": "warm", "label": "Warm the model before chat and keep it resident"})
    if pre_generation_ms >= 1500 and load_ms < 3000:
        signals.append({
            "kind": "pre_generation",
            "severity": "warning",
            "label": "Pre-generation is slow",
            "value_ms": round(pre_generation_ms, 1),
        })
        actions.append({"kind": "context", "label": "Use a smaller context or shorter prompt"})
    if prompt_ms >= 1000:
        signals.append({
            "kind": "prompt_eval",
            "severity": "warning",
            "label": "Prompt prefill is heavy",
            "value_ms": round(prompt_ms, 1),
        })
    if tps and tps < 20:
        signals.append({
            "kind": "generation",
            "severity": "danger",
            "label": "Generation is slow",
            "tokens_per_second": round(tps, 2),
        })
        actions.append({"kind": "smaller_model", "label": "Try a smaller model or lower quant"})
        actions.append({"kind": "tune", "label": "Run Pro tuning to find better GPU layers"})
    elif tps and tps < 60:
        signals.append({
            "kind": "generation",
            "severity": "warning",
            "label": "Generation is moderate",
            "tokens_per_second": round(tps, 2),
        })
        actions.append({"kind": "tune", "label": "Run Pro tuning if the model feels sluggish"})
    elif tps >= 100 and pre_generation_ms >= 1000:
        signals.append({
            "kind": "fast_after_start",
            "severity": "info",
            "label": "Generation is fast after setup",
            "tokens_per_second": round(tps, 2),
        })
        actions.append({"kind": "warm", "label": "Focus on warmup and prompt prefill, not raw generation speed"})

    if not signals:
        signals.append({
            "kind": "healthy",
            "severity": "success",
            "label": "Latency profile looks healthy",
            "tokens_per_second": round(tps, 2) if tps else None,
        })
        actions.append({"kind": "none", "label": "No immediate performance action needed"})

    severe = {s.get("severity") for s in signals}
    state = "slow" if "danger" in severe else "watch" if "warning" in severe else "ok"
    if any(s.get("kind") == "fast_after_start" for s in signals):
        summary = "Generation is fast; measured latency is mostly before generation."
    elif state == "slow":
        summary = "Generation speed is the main bottleneck."
    elif state == "watch":
        summary = "Latency has at least one fixable bottleneck."
    else:
        summary = "This model is responding well on this machine."

    return {"state": state, "summary": summary, "signals": signals, "actions": actions}


def _installed_model_names_status() -> tuple[list[str], bool]:
    resp = _ollama_request("GET", "/api/tags")
    if not isinstance(resp, dict) or "error" in resp:
        return [], False
    return sorted(
        m.get("name")
        for m in resp.get("models", [])
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    ), True


def _running_model_names_status() -> tuple[list[str], bool]:
    resp = _ollama_request("GET", "/api/ps")
    if not isinstance(resp, dict) or "error" in resp:
        return [], False
    return sorted(
        m.get("name")
        for m in resp.get("models", [])
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    ), True


_PUBLIC_PERFORMANCE_FIELDS = frozenset({
    "model",
    "prompt_len",
    "num_predict",
    "num_ctx",
    "temperature",
    "eval_count",
    "eval_duration_ms",
    "total_duration_ms",
    "load_duration_ms",
    "prompt_eval_duration_ms",
    "tokens_per_second",
    "time_to_first_token_ms",
    "source",
    "timestamp",
    "protocol_id",
    "fingerprint",
})


def _public_performance_metrics(record: object) -> dict:
    """Allowlist counters and provenance; never expose prompts or model output."""
    if not isinstance(record, dict):
        return {}
    return {
        key: value for key, value in record.items()
        if key in _PUBLIC_PERFORMANCE_FIELDS
    }


def _benchmark_history_for_model(model: str | None) -> list[dict]:
    from .cookbook.benchmark import history

    records = history()
    if model:
        records = [r for r in records if r.get("model") == model]
    records.sort(key=lambda r: float(r.get("timestamp") or 0), reverse=True)
    return [_public_performance_metrics(record) for record in records[:20]]


@app.route("/api/diagnostics/performance")
def api_performance_diagnostics():
    model = request.args.get("model", "").strip() or None
    records = _benchmark_history_for_model(model)
    latest = records[0] if records else None
    installed_models, installed_models_reported = _installed_model_names_status()
    running_models, running_models_reported = _running_model_names_status()
    return jsonify({
        "model": model,
        "installed_models": installed_models,
        "installed_models_reported": installed_models_reported,
        "running_models": running_models,
        "running_models_reported": running_models_reported,
        "history": records,
        "latest": latest,
        "diagnosis": _diagnose_performance(latest),
    })


@app.route("/api/diagnostics/performance/probe", methods=["POST"])
def api_performance_probe():
    data = request.get_json(silent=True)
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, str) or not model.strip():
        return jsonify({"error": "model required"}), 400

    prompt = "Reply with one short sentence confirming the LAC latency probe is ready."
    num_predict = 32
    num_ctx = _interactive_context()
    result = _ollama_request(
        "POST",
        "/api/generate",
        {
            "model": model.strip(),
            "prompt": prompt,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": 0,
            },
        },
        timeout=300,
    )
    if not isinstance(result, dict) or "error" in result:
        return jsonify({
            "model": model.strip(),
            "state": "failed",
            "error": result.get("error") if isinstance(result, dict) else "Ollama did not return metrics",
        }), 502

    from .cookbook.benchmark import build_metrics

    metrics = build_metrics(result, model.strip(), prompt, num_predict, 0.0)
    metrics["source"] = "diagnostic_probe"
    metrics["protocol_id"] = "lac.quick-latency.v1"
    metrics["num_ctx"] = num_ctx
    metrics = _public_performance_metrics(metrics)
    return jsonify({
        "model": model.strip(),
        "state": "done",
        "metrics": metrics,
        "diagnosis": _diagnose_performance(metrics),
    })


@app.route("/api/ollama/chat", methods=["POST"])
def ollama_chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model = data.get("model", "")
    messages = data.get("messages", [])
    if not model or not messages:
        return jsonify({"error": "Model and messages required"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/chat"
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"num_ctx": _interactive_context()},
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': f'HTTP {e.code}: {e.reason}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


_WEB_AGENT_MODES = {"ask", "build", "plan", "explore"}
_WEB_AGENT_TOOLS = {
    "ask": [],
    "build": ["read_file", "write_file", "list_files"],
    "plan": ["read_file", "list_files"],
    "explore": ["read_file", "list_files", "web_search"],
}
_WEB_AGENT_PROMPTS = {
    "ask": "",
    "build": "You are the LAC Workbench build agent for one explicit project. You can inspect files and propose write_file changes, which are staged for separate user review and apply. You cannot run shell commands on the host. Do not claim tests, builds, commands, or staged changes have run unless a provided tool result proves it.",
    "plan": "You are the LAC Workbench plan agent. You have read-only access. Inspect context, reason carefully, and propose concrete implementation steps. Never claim you changed files.",
    "explore": "You are the LAC Workbench explore agent. You have read-only code and web search access. Gather context, summarize findings, and cite the files or sources you used.",
}
_SESSION_MESSAGE_ROLES = {"system", "user", "assistant"}
_PERSISTED_AGENT_EVENT_TYPES = {
    "tool_calls",
    "tool_call",
    "tool_result",
    "error",
    "ask",
    "ask_remember_started",
    "ask_resolved",
    "ask_timeout",
    "run_cancelled",
    "staged_change",
}
_LATE_SANDBOX_CLEANUP_PREFIXES = (
    "error: docker_cleanup_failed:",
    "error: docker_ownership_refused:",
    "error: docker_ownership_unverified:",
    "error: sandbox_internal_error:",
    "error: snapshot_cleanup_failed:",
)


def _is_bounded_late_sandbox_cleanup_failure(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    result = event.get("result")
    return bool(
        event.get("type") == "tool_result"
        and event.get("name") == "run_task"
        and event.get("ok") is False
        and isinstance(result, str)
        and len(result) <= 66_000
        and result.startswith(_LATE_SANDBOX_CLEANUP_PREFIXES)
    )


def _run_task_schema(task_names: tuple[str, ...]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_task",
            "description": (
                "Run one operator-configured verification task in a disposable, "
                "network-disabled Docker snapshot. Container writes are discarded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": list(task_names),
                        "description": "Exact configured task name.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    }


def _web_agent(
    agent_name: str,
    model: str,
    *,
    sandbox_tasks: tuple[str, ...] = (),
) -> Agent:
    is_ask = agent_name == "ask"
    is_build = agent_name == "build"
    permissions = copy.deepcopy(FULL_PERMISSIONS if is_build else READONLY_PERMISSIONS)
    if is_ask:
        permissions.filesystem.read = False
        permissions.network.fetch = False
        permissions.bash.read_output = False
        permissions.mcp.connect = False
    if is_build:
        permissions.bash.run = False
    tools = list(_WEB_AGENT_TOOLS[agent_name])
    prompt = _WEB_AGENT_PROMPTS[agent_name]
    if is_build and sandbox_tasks:
        tools.append("run_task")
        names = ", ".join(sandbox_tasks)
        prompt += (
            " You may run only these operator-configured sandbox tasks through "
            f"run_task: {names}. Each task requires one-time approval, runs with "
            "network disabled against a disposable snapshot including pending "
            "staged edits, and cannot change the real project."
        )
    return Agent(
        name=agent_name,
        type=agent_name,
        description=(
            "Project-scoped web build agent with staged writes and optional named Docker tasks"
            if is_build
            else (
                "Project-bound local Ask chat with no tools"
                if is_ask
                else f"Read-only web {agent_name} agent"
            )
        ),
        model=model,
        system_prompt=prompt,
        permissions=permissions,
        tools=tools,
        raw={"source": "web_builtin"},
    )


def _clean_session_messages(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    messages: list[dict] = []
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        content = msg.get("content", "")
        if role not in _SESSION_MESSAGE_ROLES or not isinstance(content, str):
            continue
        out = {"role": role, "content": content}
        if isinstance(msg.get("timestamp"), (int, float)):
            out["timestamp"] = msg["timestamp"]
        messages.append(out)
    return messages


def _agent_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _resolve_agent_cwd(raw: object) -> Path:
    base = Path.cwd()
    cwd = Path(str(raw or base)).expanduser()
    if cwd.is_absolute():
        resolved = cwd.resolve()
    else:
        resolved = (base / cwd).resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Project root not found: {resolved}")
    return resolved


_PRIVATE_PROJECT_FIELDS = {
    "root_key",
    "root_dev",
    "root_ino",
    "root_device",
    "root_inode",
}


def _public_project(project: dict) -> dict:
    """Return the local UI contract without filesystem identity internals."""

    return {
        key: value
        for key, value in project.items()
        if key not in _PRIVATE_PROJECT_FIELDS
    }


def _registered_project_root(project_id: str) -> tuple[dict, Path]:
    """Load and revalidate one registered project or raise a bounded error."""

    from .cookbook.persistence import get_project, revalidate_project_root

    project = get_project(project_id)
    if project is None:
        raise KeyError("Project not found")
    return project, revalidate_project_root(project)


def _agent_request_root(*, project_id: str, raw_cwd: object) -> tuple[dict | None, Path]:
    """Resolve either the registered path or the isolated legacy cwd path."""

    if project_id:
        if raw_cwd is not None:
            raise TypeError("cwd cannot be supplied with project_id")
        return _registered_project_root(project_id)
    return None, _resolve_agent_cwd(raw_cwd)


class _ProjectRootDriftError(ValueError):
    """A registered root no longer matches its immutable filesystem identity."""


def _assert_registered_project_root(project_id: str, expected_root: Path) -> Path:
    from .cookbook.persistence import revalidate_project_root

    try:
        current_root = revalidate_project_root(project_id)
    except ValueError as exc:
        raise _ProjectRootDriftError(
            "Registered project root identity changed or is unavailable"
        ) from exc
    if current_root != expected_root:
        raise _ProjectRootDriftError(
            "Registered project root identity changed or is unavailable"
        )
    return current_root


def _project_bound_tool_handlers(
    base_handlers: dict,
    *,
    project_id: str,
    expected_root: Path,
) -> dict:
    """Revalidate the registered root immediately before every local tool call."""

    handlers = dict(base_handlers)

    def guarded(handler):
        def invoke(args: dict, ctx: dict) -> str:
            try:
                current_root = _assert_registered_project_root(
                    project_id, expected_root
                )
            except _ProjectRootDriftError:
                return "error: registered project root identity changed or is unavailable"
            scoped_ctx = dict(ctx or {})
            scoped_ctx["cwd"] = str(current_root)
            return handler(args, scoped_ctx)

        return invoke

    for name in ("read_file", "list_files", "write_file", "run_bash"):
        handler = handlers.get(name)
        if handler is not None:
            handlers[name] = guarded(handler)
    return handlers


def _require_exact_configured_project_root(cwd: Path) -> None:
    """Reject a descendant when an ancestor owns the applied .apt config."""

    from .config import find_project_root

    configured_root = find_project_root(cwd)
    if configured_root is None:
        return
    configured_root = configured_root.resolve()
    if cwd != configured_root:
        raise ValueError(
            f"Build cwd must match the configured project root: {configured_root}"
        )


@app.route("/api/agent/sandbox")
def agent_sandbox_capability():
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Agent sandbox status is available only on this machine"}), 403
    project_id = str(request.args.get("project_id") or "").strip()
    raw_cwd = request.args.get("cwd")
    if not project_id and (not isinstance(raw_cwd, str) or not raw_cwd.strip()):
        return jsonify({"error": "Project id or cwd required"}), 400
    try:
        _project, cwd = _agent_request_root(
            project_id=project_id,
            raw_cwd=raw_cwd,
        )
        _require_exact_configured_project_root(cwd)
    except TypeError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409 if project_id else 400
    return jsonify(probe_project_sandbox(cwd).to_dict())


_AGENT_SKIP_ENTRIES = {"node_modules", "__pycache__"}
AGENT_FILE_MAX_BYTES = 1024 * 1024


def _project_file_error(exc: Exception):
    """Map bounded project-security errors to stable local HTTP responses."""

    code = str(getattr(exc, "code", "unsafe_project_path"))
    message = str(getattr(exc, "message", str(exc)))
    if code in {"project_file_not_found", "project_directory_not_found"}:
        status = 404
    elif code == "project_file_too_large":
        status = 413
    elif code == "project_file_not_utf8":
        status = 415
    elif code == "sensitive_project_path":
        status = 403
    else:
        status = 400
    return jsonify({"error": message, "code": code}), status


@app.route("/api/agent/files")
def agent_files():
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    project_id = str(request.args.get("project_id") or "").strip()
    try:
        _project, root = _agent_request_root(
            project_id=project_id,
            raw_cwd=request.args.get("cwd"),
        )
    except TypeError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409 if project_id else 400
    rel = str(request.args.get("path") or "").strip()
    from .project_security import SensitiveProjectPathError, list_project_directory

    try:
        canonical_rel, safe_entries, truncated = list_project_directory(
            root,
            rel or ".",
        )
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    if project_id:
        try:
            _assert_registered_project_root(project_id, root)
        except _ProjectRootDriftError as e:
            return jsonify({"error": str(e)}), 409
    entries = [
        {
            "name": entry.name,
            "type": "dir" if entry.is_dir else "file",
            "size": entry.size,
        }
        for entry in sorted(
            (
                entry
                for entry in safe_entries
                if entry.name not in _AGENT_SKIP_ENTRIES
            ),
            key=lambda item: (not item.is_dir, item.name.casefold(), item.name),
        )
    ]
    response = jsonify({
        "path": "" if canonical_rel == "." else canonical_rel,
        "entries": entries,
        "truncated": truncated,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/agent/file")
def agent_file():
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    project_id = str(request.args.get("project_id") or "").strip()
    try:
        _project, root = _agent_request_root(
            project_id=project_id,
            raw_cwd=request.args.get("cwd"),
        )
    except TypeError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409 if project_id else 400
    rel = str(request.args.get("path") or "").strip()
    from .project_security import SensitiveProjectPathError, read_project_text

    try:
        canonical_rel, content = read_project_text(
            root,
            rel,
            max_bytes=AGENT_FILE_MAX_BYTES,
        )
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    if project_id:
        try:
            _assert_registered_project_root(project_id, root)
        except _ProjectRootDriftError as e:
            return jsonify({"error": str(e)}), 409
    data = content.encode("utf-8")
    response = jsonify({
        "path": canonical_rel,
        "content": content,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


_PROJECT_BROWSER_FILE_ENDPOINTS = frozenset(
    {"api_project_files", "api_project_file"}
)
_PROJECT_BROWSER_ID_PATTERN = re.compile(r"^[0-9a-f]{14}$")
_PROJECT_BROWSER_FILE_PATH_PATTERN = re.compile(
    r"^/api/projects/[^/]+/(?:files|file)$"
)
_PROJECT_BROWSER_SKIPPED_PARTS = frozenset(
    name.casefold() for name in _AGENT_SKIP_ENTRIES
)
_PROJECT_BROWSER_DECEPTIVE_CODEPOINTS = (
    frozenset({0x061C, 0x200B, 0x200E, 0x200F, 0x2060})
    | frozenset(range(0x202A, 0x202F))
    | frozenset(range(0x2066, 0x206A))
)


@app.after_request
def _project_browser_files_no_store(response):
    """Never allow browser-facing project file responses to be cached."""

    if (
        request.endpoint in _PROJECT_BROWSER_FILE_ENDPOINTS
        or _PROJECT_BROWSER_FILE_PATH_PATTERN.fullmatch(request.path)
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


def _project_browser_path_query(*, required: bool) -> str:
    """Accept at most one relative ``path`` query and no ambient root input."""

    if any(key != "path" for key in request.args.keys()):
        raise ValueError("only the path query parameter is allowed")
    values = request.args.getlist("path")
    if len(values) > 1:
        raise ValueError("path query parameter must not be repeated")
    if not values:
        if required:
            raise ValueError("path query parameter required")
        return ""
    value = str(values[0])
    if required and not value:
        raise ValueError("path query parameter required")
    return value


def _project_browser_path_is_skipped(value: str) -> bool:
    """Deny direct navigation into generated dependency/cache directories."""

    if not value:
        return False
    from .project_paths import validate_relative_project_path

    try:
        relative = validate_relative_project_path(value)
    except ValueError:
        return False
    return any(
        part.casefold() in _PROJECT_BROWSER_SKIPPED_PARTS
        for part in relative.split("/")
    )


def _project_browser_text_is_previewable(
    content: str,
    *,
    allow_leading_bom: bool = False,
) -> bool:
    """Keep browser previews to ordinary UTF-8 text, not control payloads."""

    for index, character in enumerate(content):
        codepoint = ord(character)
        if codepoint == 0xFEFF and allow_leading_bom and index == 0:
            continue
        if character in "\t\n\r":
            continue
        if (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or codepoint in _PROJECT_BROWSER_DECEPTIVE_CODEPOINTS
            or codepoint == 0xFEFF
        ):
            return False
    return True


@app.route("/api/projects/<project_id>/files")
def api_project_files(project_id):
    """List one safe directory level for one registered local project."""

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    if not _PROJECT_BROWSER_ID_PATTERN.fullmatch(project_id):
        return jsonify({"error": "invalid project identity", "code": "invalid_project_id"}), 400
    try:
        rel = _project_browser_path_query(required=False)
    except ValueError as e:
        return jsonify({"error": str(e), "code": "invalid_query"}), 400
    if not _project_browser_text_is_previewable(rel):
        return jsonify({"error": "path contains unsupported text controls", "code": "invalid_query"}), 400
    if _project_browser_path_is_skipped(rel):
        return jsonify({"error": "project path is not previewable", "code": "project_path_not_previewable"}), 403
    try:
        _project, root = _registered_project_root(project_id)
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    from .project_security import SensitiveProjectPathError, list_project_directory

    try:
        canonical_rel, safe_entries, truncated = list_project_directory(
            root,
            rel or ".",
        )
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    try:
        _assert_registered_project_root(project_id, root)
    except _ProjectRootDriftError as e:
        return jsonify({"error": str(e)}), 409

    entries = [
        {
            "name": entry.name,
            "type": "dir" if entry.is_dir else "file",
            "size": entry.size,
        }
        for entry in sorted(
            (
                entry
                for entry in safe_entries
                if (
                    entry.name.casefold() not in _PROJECT_BROWSER_SKIPPED_PARTS
                    and _project_browser_text_is_previewable(entry.name)
                )
            ),
            key=lambda item: (not item.is_dir, item.name.casefold(), item.name),
        )
    ]
    return jsonify({
        "path": "" if canonical_rel == "." else canonical_rel,
        "entries": entries,
        "truncated": truncated,
    })


@app.route("/api/projects/<project_id>/file")
def api_project_file(project_id):
    """Read one safe UTF-8 file for one registered local project."""

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    if not _PROJECT_BROWSER_ID_PATTERN.fullmatch(project_id):
        return jsonify({"error": "invalid project identity", "code": "invalid_project_id"}), 400
    try:
        rel = _project_browser_path_query(required=True)
    except ValueError as e:
        return jsonify({"error": str(e), "code": "invalid_query"}), 400
    if not _project_browser_text_is_previewable(rel):
        return jsonify({"error": "path contains unsupported text controls", "code": "invalid_query"}), 400
    if _project_browser_path_is_skipped(rel):
        return jsonify({"error": "project path is not previewable", "code": "project_path_not_previewable"}), 403
    try:
        _project, root = _registered_project_root(project_id)
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    from .project_security import SensitiveProjectPathError, read_project_text

    try:
        canonical_rel, content = read_project_text(
            root,
            rel,
            max_bytes=AGENT_FILE_MAX_BYTES,
        )
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    try:
        _assert_registered_project_root(project_id, root)
    except _ProjectRootDriftError as e:
        return jsonify({"error": str(e)}), 409

    if not _project_browser_text_is_previewable(content, allow_leading_bom=True):
        return jsonify({
            "error": "project file is not supported as a text preview",
            "code": "project_file_not_previewable",
        }), 415

    data = content.encode("utf-8")
    return jsonify({
        "path": canonical_rel,
        "content": content,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    })


_SAVE_RUN_ID = "editor-save"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@app.route("/api/projects/<project_id>/file/save", methods=["POST"])
def api_project_file_save(project_id):
    """Human edit-and-stage save: stage + auto-apply through the manual-edits session."""

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project files are available only on this machine"}), 403
    if not _PROJECT_BROWSER_ID_PATTERN.fullmatch(project_id):
        return jsonify({"error": "invalid project identity", "code": "invalid_project_id"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required", "code": "invalid_body"}), 400
    rel = data.get("path")
    content = data.get("content")
    base_sha256 = data.get("base_sha256")
    if not isinstance(rel, str) or not rel:
        return jsonify({"error": "path must be a non-empty string", "code": "invalid_body"}), 400
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string", "code": "invalid_body"}), 400
    if base_sha256 is not None and (
        not isinstance(base_sha256, str) or not _SHA256_HEX.fullmatch(base_sha256)
    ):
        return jsonify({"error": "base_sha256 must be null or 64 lowercase hex chars", "code": "invalid_body"}), 400
    if not _project_browser_text_is_previewable(rel):
        return jsonify({"error": "path contains unsupported text controls", "code": "invalid_query"}), 400
    if _project_browser_path_is_skipped(rel):
        return jsonify({"error": "project path is not previewable", "code": "project_path_not_previewable"}), 403
    if not _project_browser_text_is_previewable(content, allow_leading_bom=True):
        return jsonify({
            "error": "content is not supported as previewable text",
            "code": "project_file_not_previewable",
        }), 415
    from .agent.staging import MAX_STAGED_BYTES

    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        return jsonify({
            "error": "content is not supported as previewable text",
            "code": "project_file_not_previewable",
        }), 415
    if content_size > MAX_STAGED_BYTES:
        return jsonify({
            "error": "content exceeds the 2 MiB staged-change limit",
            "code": "project_file_too_large",
        }), 413
    try:
        _project, root = _registered_project_root(project_id)
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    from .cookbook.persistence import (
        apply_staged_change,
        get_or_create_manual_session,
        get_staged_change,
        set_staged_status,
        stage_change,
    )
    from .project_security import SensitiveProjectPathError

    try:
        session_id = get_or_create_manual_session(project_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    try:
        row = stage_change(session_id, _SAVE_RUN_ID, str(root), rel, content)
    except SensitiveProjectPathError as e:
        return _project_file_error(e)
    except ValueError as e:
        message = str(e)
        if "non-UTF-8" in message:
            return jsonify({"error": message, "code": "project_file_not_previewable"}), 415
        return jsonify({"error": message, "code": "invalid_path"}), 400

    # The editor's base must match the snapshot stage_change just took from
    # disk — this closes the load->save drift window. A leftover pending row
    # (crashed earlier save) carries a stale snapshot and lands here too,
    # self-healing on the user's retry.
    if row["base_hash"] != base_sha256:
        set_staged_status(row["id"], "rejected")
        return jsonify({
            "error": "conflict",
            "code": "save_conflict",
            "disk_sha256": row["base_hash"],
        }), 409

    result = apply_staged_change(row["id"])
    if result["status"] == "applied":
        applied = get_staged_change(row["id"])
        payload = applied["new_content"].encode("utf-8")
        return jsonify({
            "status": "applied",
            "change_id": row["id"],
            "path": row["path"],
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        })
    if result["status"] == "conflict":
        return jsonify({
            "error": "conflict",
            "code": "save_conflict",
            "disk_sha256": result.get("disk_hash"),
        }), 409
    return jsonify({"error": result.get("error") or result["status"], "code": "save_failed"}), 500


def _staged_summary(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("old_content", "new_content")}
    out["new_size"] = len((row.get("new_content") or "").encode("utf-8"))
    return out


def _staged_scope_conflict(exc: ValueError):
    return jsonify({"error": str(exc)}), 409


def _staged_local_guard():
    if not _is_trusted_local_approval_request():
        return jsonify({
            "error": "Staged changes are available only on this machine"
        }), 403
    return None


@app.route("/api/agent/sessions/<session_id>/changes")
def agent_session_changes(session_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import get_session, list_staged_changes

    session = get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    try:
        rows = list_staged_changes(
            session_id,
            run_id=request.args.get("run_id") or None,
            status=request.args.get("status") or None,
        )
    except ValueError as e:
        return _staged_scope_conflict(e)
    return jsonify({"changes": [_staged_summary(r) for r in rows]})


@app.route("/api/agent/changes/<change_id>")
def agent_change_detail(change_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import get_staged_change

    try:
        row = get_staged_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    return jsonify(row)


@app.route("/api/agent/changes/<change_id>/apply", methods=["POST"])
def agent_change_apply(change_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import apply_staged_change, get_staged_change

    try:
        row = get_staged_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    try:
        result = apply_staged_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if result["status"] == "not_found":
        return jsonify({"error": "Change not found"}), 404
    if result["status"] == "applied":
        return jsonify(result)
    return jsonify(result), 409


@app.route("/api/agent/changes/<change_id>/reject", methods=["POST"])
def agent_change_reject(change_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import get_staged_change, set_staged_status

    try:
        row = get_staged_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    if row["status"] != "pending":
        return jsonify({"status": "not_pending", "current": row["status"]}), 409
    try:
        set_staged_status(change_id, "rejected")
    except ValueError as e:
        return _staged_scope_conflict(e)
    return jsonify({"status": "rejected", "path": row["path"]})


@app.route("/api/agent/changes/<change_id>/revert", methods=["POST"])
def agent_change_revert(change_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import get_staged_change, revert_applied_change

    try:
        row = get_staged_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    try:
        result = revert_applied_change(change_id)
    except ValueError as e:
        return _staged_scope_conflict(e)
    if result["status"] == "not_found":
        return jsonify({"error": "Change not found"}), 404
    if result["status"] == "reverted":
        return jsonify(result)
    return jsonify(result), 409


@app.route("/api/agent/sessions/<session_id>/changes/apply", methods=["POST"])
def agent_session_changes_apply(session_id):
    local_guard = _staged_local_guard()
    if local_guard is not None:
        return local_guard
    from .cookbook.persistence import apply_staged_change, get_session, list_staged_changes

    session = get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    try:
        pending = list_staged_changes(session_id, status="pending")  # created_at ASC
    except ValueError as e:
        return _staged_scope_conflict(e)
    if isinstance(ids, list):
        wanted = [str(i) for i in ids]
        pending = [r for r in pending if r["id"] in wanted]
        known = {r["id"] for r in pending}
        unknown = [i for i in wanted if i not in known]
    else:
        unknown = []
    applied, conflicts, errors = [], [], []
    for row in pending:
        try:
            result = apply_staged_change(row["id"])
        except ValueError as e:
            return _staged_scope_conflict(e)
        if result["status"] == "applied":
            applied.append(row["id"])
        elif result["status"] == "conflict":
            conflicts.append(row["id"])
        else:
            errors.append({"id": row["id"], "error": result.get("error") or result["status"]})
    for i in unknown:
        errors.append({"id": i, "error": "not pending"})
    return jsonify({"applied": applied, "conflicts": conflicts, "errors": errors})


def _agent_chat_options(provider: object) -> dict:
    if getattr(provider, "name", "") != "ollama":
        return {}
    return {
        "keep_alive": "30m",
        "options": {"num_ctx": _interactive_context()},
    }


def _loopback_ollama_host(raw: object) -> str:
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Ask mode requires a loopback Ollama host") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Ask mode requires a loopback Ollama host")
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("Ask mode requires a loopback Ollama host")
        except ValueError as exc:
            if str(exc) == "Ask mode requires a loopback Ollama host":
                raise
            raise ValueError("Ask mode requires a loopback Ollama host") from exc
    return value.rstrip("/")


@app.route("/api/agent/chat", methods=["POST"])
def agent_chat():
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Agent runs are available only on this machine"}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    message = data.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Message required"}), 400
    message = message.strip()

    agent_name = str(data.get("agent") or "plan").strip().lower()
    if agent_name not in _WEB_AGENT_MODES:
        return jsonify({
            "error": f"Unknown web agent mode: {agent_name}",
            "allowed_agents": sorted(_WEB_AGENT_MODES),
        }), 403

    model = str(data.get("model") or "").strip()

    workspace = str(data.get("workspace") or "").strip()
    project_id = str(data.get("project_id") or "").strip()
    raw_cwd = data.get("cwd")
    cwd_supplied = "cwd" in data
    session_id_raw = data.get("session_id")
    session_id = str(session_id_raw).strip() if session_id_raw else ""
    session_name = str(data.get("name") or message.replace("\n", " ")[:64]).strip()

    from .cookbook.persistence import (
        add_session_event,
        create_session,
        delete_session,
        get_session,
        get_project,
        revalidate_project_root,
        save_session,
    )

    saved_session = get_session(session_id) if session_id else None
    if session_id and saved_session is None:
        return jsonify({"error": "Session not found"}), 404
    if saved_session is not None:
        session_name = str(saved_session.get("name") or session_name)

    saved_project_id = (
        str(saved_session.get("project_id") or "") if saved_session is not None else ""
    )
    if (project_id or saved_project_id) and not _is_trusted_local_approval_request():
        return jsonify({
            "error": "Project-bound agent runs are available only on this machine"
        }), 403
    if saved_project_id:
        if project_id and project_id != saved_project_id:
            return jsonify({"error": "Thread belongs to a different project"}), 409
        if cwd_supplied:
            return jsonify({"error": "cwd cannot be supplied for a project-bound thread"}), 409
        project_id = saved_project_id
    elif saved_session is not None and project_id:
        return jsonify({"error": "Legacy unassigned threads cannot be bound implicitly"}), 409

    if agent_name == "ask" and not project_id:
        return jsonify({"error": "Ask mode requires a registered project"}), 400

    project = None
    if project_id:
        if cwd_supplied:
            return jsonify({"error": "cwd cannot be supplied with project_id"}), 400
        project = get_project(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        project_workspace = str(project.get("workspace") or "")
        if workspace and workspace != project_workspace:
            return jsonify({"error": "Workspace does not match the selected project"}), 409
        if saved_session is not None and str(saved_session.get("workspace") or "") != project_workspace:
            return jsonify({"error": "Thread workspace does not match its project"}), 409
        try:
            cwd = revalidate_project_root(project)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        workspace = project_workspace
    else:
        if saved_session is not None:
            saved_workspace = str(saved_session.get("workspace") or "")
            if workspace and workspace != saved_workspace:
                return jsonify({"error": "Thread belongs to a different workspace"}), 409
            workspace = saved_workspace
        if agent_name == "build" and (
            not isinstance(raw_cwd, str) or not raw_cwd.strip()
        ):
            return jsonify({"error": "Build mode requires an explicit project cwd"}), 400
        try:
            cwd = _resolve_agent_cwd(raw_cwd)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    if agent_name == "build":
        try:
            _require_exact_configured_project_root(cwd)
        except ValueError as e:
            configured_root = str(e).split(": ", 1)[-1]
            return jsonify(
                {"error": str(e), "configured_root": configured_root}
            ), 409 if project_id else 400

    project_config = None
    if project_id:
        try:
            _assert_registered_project_root(project_id, cwd)
            if agent_name != "ask":
                project_config = resolve_config(cwd)
            _assert_registered_project_root(project_id, cwd)
        except (UnsafeProjectConfigError, _ProjectRootDriftError) as e:
            return jsonify({"error": str(e)}), 409

    if not model:
        if agent_name == "ask":
            return jsonify({"error": "Ask mode requires an explicit local model"}), 400
        if project_id:
            model = (project_config.default_model or "").strip()
        else:
            model = (load_config().default_model or "").strip()
    if not model:
        return jsonify({"error": "Model required"}), 400

    ask_ollama_host = None
    if agent_name == "ask":
        try:
            ask_ollama_host = _loopback_ollama_host(OLLAMA_HOST)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409

    created_session = not session_id
    if created_session:
        session_id = create_session(
            name=session_name,
            model=model,
            workspace=workspace,
            project_id=project_id or None,
        )

    history = _clean_session_messages(data.get("messages"))
    if not history and saved_session is not None:
        history = _clean_session_messages(saved_session.get("messages", []))
    runner_history = [{"role": m["role"], "content": m["content"]} for m in history]

    try:
        max_iterations = int(data.get("max_iterations") or 6)
    except (TypeError, ValueError):
        max_iterations = 6
    max_iterations = max(1, min(max_iterations, 12))

    try:
        sandbox_capability = (
            probe_project_sandbox(cwd) if agent_name == "build" else None
        )
        if project_id:
            _assert_registered_project_root(project_id, cwd)
        sandbox_tasks = (
            sandbox_capability.tasks
            if sandbox_capability is not None and sandbox_capability.available
            else ()
        )
        agent = _web_agent(
            agent_name,
            model,
            sandbox_tasks=sandbox_tasks,
        )
        provider = (
            OllamaProvider(base_url=ask_ollama_host)
            if agent_name == "ask"
            else default_provider(cwd)
        )
        if project_id:
            _assert_registered_project_root(project_id, cwd)
        chat_options = _agent_chat_options(provider)
        run_id = uuid.uuid4().hex
        run = _AgentRun(
            ask_event=threading.Event(),
            queue=queue.Queue(maxsize=AGENT_EVENT_QUEUE_MAX),
            session_id=session_id,
            created_at=time.time(),
        )
        base_handlers = (
            {}
            if agent_name == "ask"
            else (
                _project_bound_tool_handlers(
                    TOOL_HANDLERS,
                    project_id=project_id,
                    expected_root=cwd,
                )
                if project_id
                else TOOL_HANDLERS
            )
        )
        handlers = base_handlers
        schemas = [] if agent_name == "ask" else list(TOOL_SCHEMAS)
        permission_engine = None
        tool_preparers = {}
        always_ask_tools = set()
        never_remember_tools = set()
        if agent_name == "build":
            permission_engine = PermissionEngine.from_config(
                start_dir=cwd,
                permission_scope_root=cwd,
            )
            if project_id:
                _assert_registered_project_root(project_id, cwd)
            handlers = build_staged_handlers(
                base_handlers,
                session_id=session_id,
                run_id=run_id,
                event_queue=_PersistedRunEventSink(run),
            )
            handlers.pop("run_bash", None)
            if sandbox_tasks:
                broker = DockerTaskBroker(
                    cwd,
                    session_id,
                    run_id,
                    run.cancel_event,
                    capability=sandbox_capability,
                )

                def require_current_project_root() -> None:
                    if not project_id:
                        return
                    try:
                        _assert_registered_project_root(project_id, cwd)
                    except _ProjectRootDriftError as exc:
                        raise SandboxError(
                            "project_identity_drift",
                            "Registered project root identity changed",
                        ) from exc

                def prepare_run_task(args: dict, _ctx: dict) -> PreparedToolCall:
                    if set(args) != {"name"} or not isinstance(args.get("name"), str):
                        raise SandboxError(
                            "invalid_task_request",
                            "run_task accepts only one configured task name",
                        )
                    require_current_project_root()
                    frozen = broker.prepare_task(args["name"])

                    def execute_frozen_task() -> tuple[bool, str]:
                        try:
                            require_current_project_root()
                        except SandboxError as exc:
                            return False, f"error: {exc.code}: {exc.message}"
                        return frozen.execute_outcome()

                    return PreparedToolCall(
                        permission_target=frozen.permission_target,
                        approval_target=frozen.approval_target,
                        execute=execute_frozen_task,
                    )

                tool_preparers["run_task"] = prepare_run_task
                always_ask_tools.add("run_task")
                never_remember_tools.add("run_task")
                schemas.append(_run_task_schema(sandbox_tasks))
        runner = AgentRunner(
            provider,
            agent,
            handlers,
            schemas,
            ctx={"cwd": str(cwd), "cancel_event": run.cancel_event},
            max_iterations=1 if agent_name == "ask" else max_iterations,
            chat_options=chat_options,
            permission_engine=permission_engine,
            on_ask=None if agent_name == "ask" else _make_web_ask(run_id, run),
            tool_preparers=tool_preparers,
            always_ask_tools=always_ask_tools,
            never_remember_tools=never_remember_tools,
        )
    except (UnsafeProjectConfigError, _ProjectRootDriftError) as e:
        if created_session:
            delete_session(session_id)
        return jsonify({"error": str(e)}), 409
    except Exception:
        if created_session:
            delete_session(session_id)
        raise
    try:
        _register_agent_run(run_id, run)
    except _AgentSessionBusyError as e:
        if created_session:
            delete_session(session_id)
        return jsonify({"error": str(e)}), 409
    except RuntimeError as e:
        if created_session:
            delete_session(session_id)
        return jsonify({"error": str(e)}), 503

    def pump():
        async def _drive():
            stream = None
            try:
                if project_id:
                    _assert_registered_project_root(project_id, cwd)
                stream = runner.run_stream(message, runner_history)
                if project_id:
                    _assert_registered_project_root(project_id, cwd)
                async for ev in stream:
                    if run.cancelled and run.cancel_reason == "user_cancelled":
                        if _is_bounded_late_sandbox_cleanup_failure(ev):
                            _persist_run_event(run, ev)
                        break
                    ev_type = ev.get("type")
                    if ev_type in _PERSISTED_AGENT_EVENT_TYPES:
                        if run.cancelled and run.cancel_reason == "user_cancelled":
                            break
                        # Audit at the source, before the SSE socket can drop.
                        _persist_run_event(run, ev)
                        _put_run_item(run, _PersistedAgentEvent(dict(ev)))
                    elif not run.cancelled:
                        _put_run_item(run, ev)
                    if run.cancelled:
                        break
            finally:
                if stream is not None:
                    await stream.aclose()

        try:
            asyncio.run(_drive())
        except Exception as e:
            if not (run.cancelled and run.cancel_reason == "user_cancelled"):
                err = {"type": "error", "message": str(e)}
                try:
                    _persist_run_event(run, err)
                    _put_run_item(run, _PersistedAgentEvent(err))
                except Exception:
                    _put_run_item(run, err)
        finally:
            with run.state_lock:
                _put_run_item(run, _RUN_SENTINEL, terminal=True)
            run.worker_done.set()
            _release_agent_run_if_finished(run_id, run)

    worker = threading.Thread(target=pump, daemon=True)
    run.thread = worker

    def generate():
        assistant_content = ""
        saved_done = False
        disconnected = False
        worker_started = False
        started_at = time.time()
        persisted_messages = [
            {
                **m,
                "timestamp": m.get("timestamp") if isinstance(m.get("timestamp"), (int, float)) else started_at + (i * 0.000001),
            }
            for i, m in enumerate(history)
        ]
        persisted_messages.append({
            "role": "user",
            "content": message,
            "timestamp": started_at + (len(persisted_messages) * 0.000001),
        })

        try:
            yield _agent_sse({"type": "session", "session_id": session_id})
            yield _agent_sse(
                {
                    "type": "run",
                    "run_id": run_id,
                    "approval_token": run.approval_token,
                }
            )
            yield _agent_sse({"type": "status", "message": f"{agent_name.title()} agent started"})
            worker.start()
            worker_started = True

            while True:
                try:
                    queued = run.queue.get(timeout=HEARTBEAT_INTERVAL)
                except queue.Empty:
                    # keeps disconnect detectable while the run is paused on an ask
                    yield ": ping\n\n"
                    continue
                if queued is _RUN_SENTINEL:
                    break

                skip_persist = isinstance(
                    queued, (_PersistedAgentEvent, _TerminalAgentEvent)
                )
                ev = queued.payload if skip_persist else queued

                ev_type = ev.get("type")
                if ev_type == "delta":
                    assistant_content += str(ev.get("content") or "")
                elif ev_type == "done":
                    assistant_content = str(ev.get("content") or assistant_content)
                    if assistant_content:
                        persisted_messages.append({
                            "role": "assistant",
                            "content": assistant_content,
                            "timestamp": started_at + (len(persisted_messages) * 0.000001),
                        })
                    save_session(
                        session_id=session_id,
                        model=model,
                        messages=persisted_messages,
                        name=session_name,
                        workspace=workspace,
                        project_id=project_id or None,
                        create_if_missing=False,
                    )
                    saved_done = True
                elif ev_type in _PERSISTED_AGENT_EVENT_TYPES and not skip_persist:
                    add_session_event(session_id, str(ev_type), ev)

                yield _agent_sse(ev)
                if ev_type == "run_cancelled":
                    break
        except GeneratorExit:
            disconnected = True
            raise
        except Exception as e:
            err = {"type": "error", "message": str(e)}
            add_session_event(session_id, "error", err)
            yield _agent_sse(err)
        finally:
            # cancel-by-disconnect AND normal completion both land here
            with run.state_lock:
                run.cancelled = True
                if run.cancel_reason is None:
                    run.cancel_reason = "disconnect"
                run.cancel_event.set()
                run.ask_event.set()
            if worker_started:
                worker.join(timeout=WORKER_JOIN_TIMEOUT)
            if not worker_started:
                run.worker_done.set()
            try:
                if not saved_done:
                    if assistant_content:
                        persisted_messages.append({
                            "role": "assistant",
                            "content": assistant_content,
                            "timestamp": started_at + (len(persisted_messages) * 0.000001),
                        })
                    save_session(
                        session_id=session_id,
                        model=model,
                        messages=persisted_messages,
                        name=session_name,
                        workspace=workspace,
                        project_id=project_id or None,
                        create_if_missing=False,
                    )
            finally:
                run.persistence_done.set()
                _release_agent_run_if_finished(run_id, run)
            if not disconnected:
                # yielding after GeneratorExit is a RuntimeError - only emit on normal end
                yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


def _is_trusted_local_approval_request() -> bool:
    try:
        remote = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if not remote.is_loopback:
        return False
    def is_local_host(host: str) -> bool:
        if host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    host = urlsplit(f"//{request.host}").hostname or ""
    if not is_local_host(host):
        return False
    origin = request.headers.get("Origin")
    if origin:
        origin_host = urlsplit(origin).hostname or ""
        if not is_local_host(origin_host):
            return False
    return True


@app.route("/api/agent/runs/<run_id>/cancel", methods=["POST"])
def agent_run_cancel(run_id):
    """Cancel exactly one capability-bound local agent run."""

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Agent cancellation is accepted only from this machine"}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    approval_token = data.get("approval_token")
    if not isinstance(approval_token, str) or not approval_token:
        return jsonify({"error": "approval_token required"}), 400

    with _AGENT_RUNS_LOCK:
        run = _AGENT_RUNS.get(run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    if not secrets.compare_digest(approval_token, run.approval_token):
        return jsonify({"error": "Invalid approval capability"}), 403

    cancel_event = {
        "type": "run_cancelled",
        "run_id": run_id,
        "reason": "user_cancelled",
    }
    should_journal = False
    with run.state_lock:
        run.cancelled = True
        run.cancel_reason = "user_cancelled"
        run.cancel_event.set()
        run.ask_event.set()
        if not run.cancel_announced:
            # Explicit cancellation supersedes every buffered wire item,
            # including a stale completion sentinel. Persisted items remain in
            # the audit journal; the browser receives cancellation next.
            while True:
                try:
                    run.queue.get_nowait()
                except queue.Empty:
                    break
            _put_run_item(
                run,
                _TerminalAgentEvent(cancel_event),
                terminal=True,
            )
            run.cancel_announced = True
        if not run.cancel_journaled and not run.cancel_journal_in_progress:
            run.cancel_journal_in_progress = True
            should_journal = True
    if should_journal:
        try:
            _persist_run_event(run, cancel_event)
        except Exception:
            # Stop is a safety control: an unavailable audit store must never
            # leave the task running. A later idempotent request may retry.
            with run.state_lock:
                run.cancel_journal_in_progress = False
        else:
            with run.state_lock:
                run.cancel_journaled = True
                run.cancel_journal_in_progress = False
    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/agent/runs/<run_id>/answer", methods=["POST"])
def agent_run_answer(run_id):
    from .permission import Decision

    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Agent approvals are accepted only from this machine"}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    decision_raw = str(data.get("decision") or "").strip().lower()
    if decision_raw not in ("allow", "deny"):
        return jsonify({"error": "decision must be 'allow' or 'deny'"}), 400
    ask_id = str(data.get("ask_id") or "").strip()
    if not ask_id:
        return jsonify({"error": "ask_id required"}), 400
    remember_raw = data.get("remember", False)
    if not isinstance(remember_raw, bool):
        return jsonify({"error": "remember must be a boolean"}), 400
    approval_token = data.get("approval_token")
    if not isinstance(approval_token, str) or not approval_token:
        return jsonify({"error": "approval_token required"}), 400

    with _AGENT_RUNS_LOCK:
        run = _AGENT_RUNS.get(run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    if not secrets.compare_digest(approval_token, run.approval_token):
        return jsonify({"error": "Invalid approval capability"}), 403

    with run.state_lock:
        pending = run.pending_ask
        if run.cancelled:
            return jsonify({"error": "Run is no longer active"}), 409
        if pending is None or run.ask_event.is_set():
            return jsonify({"error": "No pending ask"}), 409
        if pending.get("ask_id") != ask_id:
            return jsonify({"error": "Ask is no longer pending"}), 409

        decision = Decision.ALLOW if decision_raw == "allow" else Decision.DENY
        remember = bool(
            remember_raw
            and decision is Decision.ALLOW
            and _ask_is_rememberable(
                str(pending.get("tool") or ""),
                pending.get("target"),
                str(pending.get("key") or ""),
            )
        )
        run.answer = decision
        run.remember = remember
        run.ask_event.set()
        pending_state = pending
        consumed_event = pending["consumed_event"]

    consumed = consumed_event.wait(ANSWER_ACK_TIMEOUT)
    if not consumed:
        # The worker did not acknowledge the answer. Revoke any unconsumed
        # ALLOW so a late wake-up cannot execute after this request fails.
        with run.state_lock:
            actual = pending_state.get("result")
            if (
                actual is None
                and run.pending_ask is pending_state
            ):
                run.answer = Decision.DENY
                run.remember = False
                run.cancel_reason = "answer_ack_timeout"
                run.ask_event.set()
        if actual is None:
            return jsonify({"error": "Agent did not acknowledge the answer"}), 504

    with run.state_lock:
        if pending_state.get("ask_id") != ask_id:
            return jsonify({"error": "Ask resolution mismatch"}), 409
        actual = pending_state.get("result")
        error = pending_state.get("error")
    if actual is _ASK_COMMIT_FAILED:
        return jsonify({"error": "Failed to commit approval", "detail": error}), 500
    if actual is not decision:
        actual_value = actual.value if isinstance(actual, Decision) else "deny"
        return jsonify({"error": "Run ended before approval was consumed", "decision": actual_value}), 409
    return jsonify({"ok": True})


@app.route("/api/ollama/check-install")
def ollama_check_install():
    import shutil
    path = shutil.which("ollama")
    if path:
        return jsonify({"installed": True, "path": path})
    url = "https://ollama.com/download"
    system = platform.system().lower()
    return jsonify({"installed": False, "download_url": url, "instructions": f"Download Ollama from {url}"})


@app.route("/api/system/ollama-path")
def ollama_path():
    import shutil
    path = shutil.which("ollama")
    return jsonify({"path": path})


@app.route("/api/system/version")
def api_version():
    return jsonify({
        "version": APP_VERSION,
        "github_url": __github_url__,
        "download_url": __download_url__,
        "app_name": "LAC",
    })


@app.route("/api/system/storage")
def api_storage():
    app_dir = _app_payload_dir()
    models_dir = _default_ollama_models_dir()
    user_models_dir = _read_user_env_var("OLLAMA_MODELS")
    app_size = _safe_dir_size(app_dir) if getattr(sys, "frozen", False) else None
    model_files = _find_model_weight_files(app_dir)
    return jsonify({
        "app_dir": str(app_dir),
        "app_size_bytes": app_size,
        "ollama_models_dir": str(models_dir),
        "ollama_models_size_bytes": _safe_dir_size(models_dir),
        "ollama_models_configured": bool(os.environ.get("OLLAMA_MODELS")),
        "ollama_models_user_dir": str(Path(user_models_dir).expanduser()) if user_models_dir else None,
        "ollama_models_user_configured": bool(user_models_dir),
        "ollama_models_restart_required": user_models_dir != os.environ.get("OLLAMA_MODELS"),
        "model_weight_files_in_app": model_files,
        "models_are_bundled": bool(model_files),
        "model_install_mode": "on_demand_ollama_pull",
    })


def _model_store_doctor_payload() -> dict:
    app_dir = _app_payload_dir()
    models_dir = _default_ollama_models_dir()
    default_dir = Path.home() / ".ollama" / "models"
    if platform.system().lower() == "linux":
        default_dir = Path("/usr/share/ollama/.ollama/models")
    scratch_dir = _hf_import_scratch_root()
    model_files = _find_model_weight_files(app_dir)

    model_size = _safe_dir_size(models_dir)
    scratch_size = _safe_dir_size(scratch_dir)
    default_size = _safe_dir_size(default_dir)
    app_size = _safe_dir_size(app_dir) if getattr(sys, "frozen", False) else None
    disk = _disk_usage_payload(models_dir)
    scratch_disk = _disk_usage_payload(scratch_dir)

    warnings = []
    actions = []
    if disk["free_bytes"] is not None:
        free_gb = disk["free_gb"] or 0
        if free_gb < 10:
            warnings.append("Model drive has less than 10 GB free.")
            actions.append({
                "kind": "move_models",
                "label": "Move future Ollama pulls to a larger drive",
                "severity": "danger",
            })
        elif free_gb < 25:
            warnings.append("Model drive is getting tight.")
            actions.append({
                "kind": "clean_models",
                "label": "Delete unused models before large imports",
                "severity": "warning",
            })
    if scratch_size and scratch_size > 0:
        actions.append({
            "kind": "clear_import_scratch",
            "label": "Clear Hugging Face import scratch",
            "severity": "info",
        })
    if default_dir != models_dir and default_size and default_size > 0:
        warnings.append("The default Ollama model folder still contains files on this machine.")
        actions.append({
            "kind": "inspect_default_store",
            "label": "Inspect the old default model folder",
            "severity": "warning",
        })
    if model_files:
        warnings.append("Model weight files were found inside the app payload.")
        actions.append({
            "kind": "remove_bundled_weights",
            "label": "Remove bundled model weights from the app folder",
            "severity": "danger",
        })

    state = "critical" if any(a.get("severity") == "danger" for a in actions) else "watch" if warnings else "ok"
    return {
        "state": state,
        "warnings": warnings,
        "actions": actions,
        "model_store": {
            "path": str(models_dir),
            "exists": models_dir.exists(),
            "size_bytes": model_size,
            "size_gb": _bytes_to_gb_display(model_size),
            **disk,
        },
        "import_scratch": {
            "path": str(scratch_dir),
            "exists": scratch_dir.exists(),
            "size_bytes": scratch_size,
            "size_gb": _bytes_to_gb_display(scratch_size),
            "entries": _count_dir_entries(scratch_dir),
            "safe_to_clear": _is_safe_import_scratch_dir(scratch_dir),
            **scratch_disk,
        },
        "default_model_store": {
            "path": str(default_dir),
            "active": default_dir == models_dir,
            "exists": default_dir.exists(),
            "size_bytes": default_size,
            "size_gb": _bytes_to_gb_display(default_size),
        },
        "app_payload": {
            "path": str(app_dir),
            "size_bytes": app_size,
            "size_gb": _bytes_to_gb_display(app_size),
            "model_weight_files": model_files,
        },
    }


@app.route("/api/system/model-store-doctor")
def api_model_store_doctor():
    return jsonify(_model_store_doctor_payload())


@app.route("/api/system/import-scratch", methods=["DELETE"])
def api_clear_import_scratch():
    scratch_dir = _hf_import_scratch_root()
    if not _is_safe_import_scratch_dir(scratch_dir):
        return jsonify({
            "state": "failed",
            "error": f"Refusing to clear unsafe import scratch path: {scratch_dir}",
            "path": str(scratch_dir),
        }), 400
    try:
        result = _clear_directory_contents(scratch_dir)
    except OSError as exc:
        return jsonify({
            "state": "failed",
            "error": f"Could not clear import scratch: {exc}",
            "path": str(scratch_dir),
        }), 500
    return jsonify({
        "state": "cleared",
        "path": str(scratch_dir),
        **result,
    })


@app.route("/api/system/model-location")
def api_model_location():
    return jsonify(_model_location_payload())


@app.route("/api/system/model-location", methods=["PUT"])
def api_set_model_location():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    reset = bool(data.get("reset"))
    if reset:
        _write_user_env_var("OLLAMA_MODELS", None)
        return jsonify(_model_location_payload(restart_required=True))

    raw_path = data.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return jsonify({"error": "path required"}), 400

    target = Path(raw_path.strip()).expanduser()
    try:
        if target.exists() and not target.is_dir():
            return jsonify({"error": "Model location must be a folder, not a file."}), 400
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
    except OSError as exc:
        return jsonify({"error": f"Could not create model location: {exc}"}), 400

    _write_user_env_var("OLLAMA_MODELS", str(resolved))
    return jsonify(_model_location_payload(restart_required=True))


def _debug_env_flags() -> dict:
    """Sanitized environment presence for support exports.

    Values that can contain credentials or private endpoints are never included.
    """
    allowed_values = {"OLLAMA_HOST", "OLLAMA_MODELS"}
    names = [
        "OLLAMA_HOST",
        "OLLAMA_MODELS",
        "LAC_PRO_GATE_URL",
        "LAC_PRO_DEV",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
    ]
    out = {}
    for name in names:
        value = os.environ.get(name)
        out[name] = {"set": value is not None}
        if name in allowed_values and value is not None:
            out[name]["value"] = _safe_debug_env_value(name, value)
    return out


def _safe_debug_env_value(name: str, value: str) -> str:
    if name != "OLLAMA_HOST":
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parts.username and not parts.password:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _debug_call(label: str, fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - diagnostics should never fail whole export
        return {"error": f"{label}: {exc}"}


def _debug_bundle_payload() -> dict:
    cfg = _debug_call("config", load_config)
    cfg_payload = cfg if isinstance(cfg, dict) and "error" in cfg else {
        "workspace": getattr(cfg, "workspace", None),
        "ollama_host": getattr(cfg, "ollama_host", None),
        "theme": getattr(cfg, "theme", None),
        "default_model": getattr(cfg, "default_model", None),
    }

    info = _debug_call("scan", detect)
    if not (isinstance(info, dict) and "error" in info):
        info = {
            "os": info.os,
            "cpu": info.cpu,
            "cores": info.cpu_cores,
            "ram_gb": info.ram_gb,
            "gpus": [
                {
                    "name": g.name,
                    "vram_gb": g.vram_gb,
                    "backend": g.backend,
                    "tier": g.tier,
                    "device_index": g.device_index,
                }
                for g in info.gpus
            ],
            "total_vram_gb": info.total_vram_gb,
            "combined_vram_gb": info.combined_vram_gb,
            "in_container": info.in_container,
        }

    models = _debug_call("ollama models", lambda: _ollama_request("GET", "/api/tags"))
    model_payload = []
    if isinstance(models, dict) and isinstance(models.get("models"), list):
        model_payload = [
            {
                "name": m.get("name"),
                "size_gb": round((m.get("size") or 0) / (1024**3), 2),
                "modified": m.get("modified_at", ""),
            }
            for m in models.get("models", [])
            if isinstance(m, dict)
        ]
    elif isinstance(models, dict) and "error" in models:
        model_payload = models

    downloads = _debug_call("downloads", lambda: download_history()[-10:])

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": {
            "name": "LAC",
            "version": APP_VERSION,
            "github_url": __github_url__,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "config": cfg_payload,
        "environment": _debug_env_flags(),
        "hardware": info,
        "ollama": {
            "host": _safe_debug_env_value("OLLAMA_HOST", OLLAMA_HOST),
            "version": _debug_call("ollama version", lambda: _ollama_request("GET", "/api/version")),
            "running_models": _debug_call("ollama ps", lambda: _ollama_request("GET", "/api/ps")),
            "installed_models": model_payload,
        },
        "storage": {
            "app_dir": str(_app_payload_dir()),
            "ollama_models_dir": str(_default_ollama_models_dir()),
            "ollama_models_configured": bool(os.environ.get("OLLAMA_MODELS")),
            "model_install_mode": "on_demand_ollama_pull",
        },
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "ok": p.ok,
                "state": p.state,
                "host_api_version": p.host_api_version,
                "error": p.error or p.compatibility_error,
            }
            for p in _discover_plugins_safe()
        ],
        "recent_downloads": downloads,
    }


@app.route("/api/system/debug-bundle")
def api_debug_bundle():
    payload = _debug_bundle_payload()
    resp = jsonify(payload)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    resp.headers["Content-Disposition"] = f'attachment; filename="lac-debug-{stamp}.json"'
    return resp


@app.route("/api/system/check-update")
def api_check_update():
    current = request.args.get("current", APP_VERSION)
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = "https://api.github.com/repos/Dkrynen/lac/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"LAC/{APP_VERSION}")
        resp = urllib.request.urlopen(req, timeout=5)
        data = _json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("vV")
        if latest and is_newer(latest, current):
            return jsonify({
                "update_available": True,
                "latest_version": latest,
                "download_url": select_release_download_url(data, data.get("html_url", "")),
                "release_notes": (data.get("body") or "")[:500],
            })
        return jsonify({"update_available": False, "latest_version": latest, "current_version": current})
    except Exception as e:
        return jsonify({"update_available": False, "error": str(e)})


@app.route("/api/ollama/check-install-detailed")
def ollama_check_detailed():
    import shutil
    path = shutil.which("ollama")
    if path:
        try:
            r = proc.run([path, "--version"], capture_output=True, text=True, timeout=5)
            version = r.stdout.strip() or r.stderr.strip() or "unknown"
        except Exception:
            version = "unknown"
        return jsonify({"installed": True, "path": path, "version": version})

    system = platform.system().lower()
    urls = {
        "windows": "https://ollama.com/download/windows",
        "darwin": "https://ollama.com/download/mac",
        "linux": "https://ollama.com/download/linux",
    }
    return jsonify({
        "installed": False,
        "download_url": urls.get(system, "https://ollama.com/download"),
        "instructions": f"Download and install Ollama from ollama.com/download for your OS.",
    })


LIBRARY_CACHE = None
LIBRARY_CACHE_TIME = 0
LIBRARY_CACHE_TTL = 3600
LIBRARY_CACHE_REFRESHING = False
USER_LIBRARY_CACHE_PATH = Path.home() / ".model-hub" / "cache" / "library_cache.json"
SHIPPED_LIBRARY_CACHE_PATH = (
    Path(__file__).resolve().parent / "cookbook" / "data" / "library_cache.json"
)


def _write_library_cache(models):
    """Persist refreshed data in user space without touching shipped assets."""
    cache_path = USER_LIBRARY_CACHE_PATH
    temp_path = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"fetched": time.time(), "models": models}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, cache_path)
        return True
    except Exception:
        return False
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _normalize_library_models(models):
    """Validate cached rows for Browse and discard fields the scraper never emits."""
    if not isinstance(models, list) or not models:
        return None

    normalized = []
    for model in models:
        if not isinstance(model, dict):
            return None
        name = model.get("name")
        if not isinstance(name, str) or not name.strip():
            return None

        clean = {"name": name.strip()}
        for field in ("description", "pulls", "tag_count"):
            if field in model:
                value = model[field]
                if not isinstance(value, str):
                    return None
                clean[field] = value
        for field in ("capabilities", "sizes"):
            if field in model:
                value = model[field]
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    return None
                clean[field] = list(value)
        normalized.append(clean)
    return normalized


def _read_library_cache(cache_path):
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    models = _normalize_library_models(data.get("models"))
    if models is None:
        return None
    try:
        fetched = float(data.get("fetched", 0))
    except (TypeError, ValueError):
        fetched = 0
    return models, fetched


def _scrape_library():
    """Scrape the Ollama library index. Returns a list of model dicts or
    {"error": str, "models": []} on failure."""
    try:
        import urllib.request
        req = urllib.request.Request("https://ollama.com/library", headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode()
        models = []
        cards = re.split(r'(?=<a\s+href="/library/[^"]+"\s+class="group\s+w-full\s+space-y-5")', html)[1:]
        for card in cards:
            name_m = re.search(r'href="/library/([^"]+)"', card)
            if not name_m:
                continue
            name = name_m.group(1)
            desc_m = re.search(r'class="max-w-lg\s+break-words\s+text-neutral-800\s+text-md">(.*?)</p>', card, re.DOTALL)
            desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""
            capabilities = re.findall(r'x-test-capability[^>]*>\s*([^<]+)\s*<', card)
            sizes = re.findall(r'x-test-size[^>]*>\s*([^<]+)\s*<', card)
            pulls_m = re.search(r'x-test-pull-count>([^<]+)<', card)
            pulls = pulls_m.group(1).strip() if pulls_m else "0"
            tags_m = re.search(r'x-test-tag-count>([^<]+)<', card)
            tag_count = tags_m.group(1).strip() if tags_m else "0"
            models.append({
                "name": name,
                "description": desc[:300],
                "capabilities": capabilities,
                "sizes": sizes,
                "pulls": pulls,
                "tag_count": tag_count,
            })
        _write_library_cache(models)
        return models
    except Exception as e:
        return {"error": str(e), "models": []}


def _refresh_library_background():
    """Refresh the library cache off the request thread (stale-while-revalidate)."""
    global LIBRARY_CACHE_REFRESHING
    if LIBRARY_CACHE_REFRESHING:
        return
    LIBRARY_CACHE_REFRESHING = True

    def worker():
        global LIBRARY_CACHE, LIBRARY_CACHE_TIME, LIBRARY_CACHE_REFRESHING
        try:
            models = _scrape_library()
            if isinstance(models, list):
                LIBRARY_CACHE = models
                LIBRARY_CACHE_TIME = time.time()
        finally:
            LIBRARY_CACHE_REFRESHING = False

    threading.Thread(target=worker, daemon=True).start()


def _fetch_library():
    """Stale-while-revalidate: serve any cached data instantly and refresh in
    the background when stale. Only the very first (cold) call blocks on a
    live scrape — thereafter Browse loads instantly."""
    global LIBRARY_CACHE, LIBRARY_CACHE_TIME
    now = time.time()

    if LIBRARY_CACHE:
        if now - LIBRARY_CACHE_TIME > LIBRARY_CACHE_TTL:
            _refresh_library_background()
        return LIBRARY_CACHE

    for cache_path in (USER_LIBRARY_CACHE_PATH, SHIPPED_LIBRARY_CACHE_PATH):
        cached = _read_library_cache(cache_path)
        if cached is None:
            continue
        models, fetched = cached
        LIBRARY_CACHE = models
        LIBRARY_CACHE_TIME = now
        if now - fetched > LIBRARY_CACHE_TTL:
            _refresh_library_background()
        return LIBRARY_CACHE

    # Cold cache — scrape synchronously (happens once, ever).
    models = _scrape_library()
    if isinstance(models, list):
        LIBRARY_CACHE = models
        LIBRARY_CACHE_TIME = now
    return models


@app.route("/api/library/browse")
def api_library_browse():
    q = request.args.get("q", "").strip().lower()
    capability = request.args.get("capability", "").strip().lower()
    sort = request.args.get("sort", "pulls")
    compatible = request.args.get("compatible", "").strip()
    result = _fetch_library()
    if isinstance(result, dict) and "error" in result:
        return jsonify(result)
    models = list(result)

    # Always detect system VRAM so every card can show a real fit verdict.
    system_vram = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    # Cross-reference each library family against the curated catalog to
    # populate real VRAM/params and a hardware fit verdict (shared with the
    # CLI's `lac browse`, which uses the exact same enrichment).
    from .cookbook.library import enrich_library_models
    models = enrich_library_models(models, system_vram)
    sv = system_vram or 0

    if q:
        models = [m for m in models if q in m["name"].lower() or q in m.get("display", m["name"]).lower() or q in m.get("description", "").lower()]
    if capability:
        models = [m for m in models if any(capability in c.lower() for c in m.get("capabilities", []))]

    if compatible and compatible != "false" and sv:
        if compatible == "gpu":
            models = [m for m in models if m.get("fit") == "gpu"]
        elif compatible == "cpu":
            models = [m for m in models if m.get("fit") in ("offload", "too_big")]

    def parse_pulls(p):
        try:
            p = p.replace("M", "e6").replace("B", "e9").replace("K", "e3")
            return float(p)
        except (ValueError, TypeError):
            return 0

    def parse_vram(m):
        return m.get("vram_q4", 0) or 0

    if sort == "name":
        models.sort(key=lambda m: m.get("display", m["name"]))
    elif sort == "pulls":
        models.sort(key=lambda m: parse_pulls(m.get("pulls", "0")), reverse=True)
    elif sort == "newest":
        models.sort(key=lambda m: m["name"], reverse=True)
    elif sort == "vram":
        models.sort(key=parse_vram)
    elif sort == "params":
        models.sort(key=lambda m: m.get("params_b", 0), reverse=True)

    return jsonify({"total": len(models), "system_vram": system_vram, "models": models})


@app.route("/api/library/tags")
def api_library_tags():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "No model name"}), 400
    try:
        import urllib.request
        url = f"https://ollama.com/library/{name}/tags"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode()
        tags = re.findall(r'href="/library/' + re.escape(name) + r':([^"]+)"', html)
        tags = sorted(set(tags))
        return jsonify({"name": name, "tags": tags, "count": len(tags)})
    except Exception as e:
        return jsonify({"error": str(e), "name": name, "tags": []})


_GGUF_QUANT_PAT = re.compile(
    r"(?i)(^|[^A-Za-z0-9])"
    r"(IQ[1-4]_(?:XXS|XS|S|M|L|XL|NL)|"
    r"Q[2-8](?:_K(?:_XS|_S|_M|_L|_XL)?|_[0-8])?|"
    r"F32|BF16|F16|FP16)"
    r"([^A-Za-z0-9]|$)"
)
_GGUF_UNSUPPORTED_IMPORT_VARIANT_PAT = re.compile(
    r"(?i)(^|[^A-Za-z0-9])Q[2-8][_-]0[_-](?:4[_-]4|4[_-]8|8[_-]8)([^A-Za-z0-9]|$)"
)
_GGUF_QUANT_SORT = {
    "IQ1_S": 1,
    "IQ1_M": 2,
    "IQ2_XXS": 3,
    "IQ2_XS": 4,
    "IQ2_S": 5,
    "IQ2_M": 6,
    "Q2_K": 7,
    "Q2_K_S": 8,
    "Q2_K_M": 9,
    "Q2_K_L": 10,
    "IQ3_XXS": 11,
    "IQ3_XS": 12,
    "IQ3_S": 13,
    "IQ3_M": 14,
    "Q3_K_S": 15,
    "Q3_K_M": 16,
    "Q3_K_L": 17,
    "Q3_K_XL": 18,
    "IQ4_XS": 19,
    "IQ4_NL": 20,
    "IQ4_M": 21,
    "Q4_0": 22,
    "Q4_K_S": 23,
    "Q4_K_M": 24,
    "Q5_0": 25,
    "Q5_K_S": 26,
    "Q5_K_M": 27,
    "Q6_K": 28,
    "Q8_0": 29,
    "Q8": 30,
    "F16": 31,
    "BF16": 32,
    "F32": 33,
}
_GGUF_IMPORT_PREFERENCE = [
    "Q4_K_M",
    "Q4_K_S",
    "Q5_K_M",
    "Q5_0",
    "Q6_K",
    "Q8_0",
    "Q8",
    "IQ4_XS",
    "IQ4_NL",
    "Q3_K_M",
    "Q3_K_L",
    "Q3_K_S",
    "IQ3_M",
    "IQ3_XS",
    "Q2_K",
    "IQ2_M",
    "F16",
    "BF16",
    "F32",
]


def _gguf_quant(filename: str) -> str | None:
    match = _GGUF_QUANT_PAT.search(filename or "")
    if not match:
        return None
    quant = match.group(2).upper().replace("-", "_").replace("FP16", "F16")
    return quant


def _gguf_import_block_reason(filename: str) -> str | None:
    if _GGUF_UNSUPPORTED_IMPORT_VARIANT_PAT.search(filename or ""):
        return "CPU-tuned GGUF variant; choose the standard file for this quant."
    return None


def _quant_sort_key(quant: str | None) -> tuple[int, str]:
    q = quant or ""
    return (_GGUF_QUANT_SORT.get(q, 999), q)


def _gguf_quants(filenames: list[str]) -> list[str]:
    quants = {_gguf_quant(name) for name in filenames}
    return sorted((q for q in quants if q), key=_quant_sort_key)


def _bytes_to_gb(size_bytes: int | None) -> float | None:
    if not size_bytes:
        return None
    return round(size_bytes / (1024**3), 2)


def _bytes_to_gb_display(size_bytes: int | None) -> float | None:
    if size_bytes is None:
        return None
    return round(size_bytes / (1024**3), 2)


def _space_check(free_bytes: int | None, required_bytes: int) -> dict:
    ok = free_bytes is not None and free_bytes >= required_bytes
    return {
        "ok": ok,
        "free_bytes": free_bytes,
        "free_gb": _bytes_to_gb_display(free_bytes),
        "required_bytes": required_bytes,
        "required_gb": _bytes_to_gb_display(required_bytes),
    }


def _hf_import_preflight(size_bytes: int | None) -> dict:
    """Mirror LAC Pro's import staging locations without importing lac_pro."""
    scratch_dir = _hf_import_scratch_root()
    model_dir = _default_ollama_models_dir()
    try:
        shared_volume: bool | None = (
            _storage_volume_identity(scratch_dir) == _storage_volume_identity(model_dir)
        )
    except OSError:
        shared_volume = None
    size = int(size_bytes or 0)
    if size <= 0:
        return {
            "state": "unknown",
            "scratch_dir": str(scratch_dir),
            "model_store_dir": str(model_dir),
            "shared_volume": shared_volume,
            "combined": None,
            "warnings": ["File size is unknown; disk fit cannot be checked before import."],
            "scratch": _space_check(_disk_free_bytes(scratch_dir), 0),
            "model_store": _space_check(_disk_free_bytes(model_dir), 0),
        }

    scratch_required = size
    # Worst-case Windows create peak: uploaded GGUF/cache, validation or
    # conversion output, NewLayer's full commit-time copy, plus a possible
    # recreate for a verified template repair. Pro enforces the same 4x store
    # reserve; shared volumes add the 1x download scratch requirement below.
    model_required = int(size * 4.0)
    scratch = _space_check(_disk_free_bytes(scratch_dir), scratch_required)
    model_store = _space_check(_disk_free_bytes(model_dir), model_required)
    warnings = []
    combined = None
    if shared_volume is True:
        combined = _space_check(_disk_free_bytes(scratch_dir), scratch_required + model_required)
        if not combined["ok"]:
            warnings.append(
                "Not enough combined free space for Hugging Face staging and the Ollama model store."
            )
    elif shared_volume is False:
        if not scratch["ok"]:
            warnings.append("Not enough free space in the Hugging Face staging folder.")
        if not model_store["ok"]:
            warnings.append("Not enough free space in the Ollama model store.")
    else:
        warnings.append("Could not verify whether import staging and Ollama storage share a volume.")
    return {
        "state": "ok" if not warnings else "blocked",
        "scratch_dir": str(scratch_dir),
        "model_store_dir": str(model_dir),
        "shared_volume": shared_volume,
        "combined": combined,
        "selected_size_bytes": size,
        "selected_size_gb": _bytes_to_gb_display(size),
        "moves_existing_models": False,
        "scratch": scratch,
        "model_store": model_store,
        "warnings": warnings,
    }


def _hf_file_fit(size_bytes: int | None, system_vram: float | None, ram_gb: float | None) -> dict:
    size_gb = _bytes_to_gb(size_bytes)
    if not size_gb:
        return {"fit": "unknown", "vram_gb": None}
    # GGUF runtime memory is more than the file on disk: KV cache, graph buffers,
    # and allocator headroom. Keep this deliberately conservative for previews.
    required_gb = round((size_gb * 1.18) + 0.25, 2)
    if system_vram and required_gb <= system_vram * 0.9:
        return {"fit": "fits", "vram_gb": required_gb}
    if (system_vram and required_gb <= system_vram * 2.0) or (ram_gb and required_gb <= ram_gb * 0.75):
        return {"fit": "offload", "vram_gb": required_gb}
    return {"fit": "too_large", "vram_gb": required_gb}


def _hf_gguf_files(siblings: list[dict], system_vram: float | None, ram_gb: float | None) -> list[dict]:
    files = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        filename = sibling.get("rfilename") or sibling.get("filename")
        if not isinstance(filename, str) or not filename.lower().endswith(".gguf"):
            continue
        size = sibling.get("size")
        lfs = sibling.get("lfs")
        if not isinstance(size, int) and isinstance(lfs, dict) and isinstance(lfs.get("size"), int):
            size = lfs["size"]
        if not isinstance(size, int):
            size = None
        quant = _gguf_quant(filename)
        fit = _hf_file_fit(size, system_vram, ram_gb)
        compatibility_note = _gguf_import_block_reason(filename)
        files.append({
            "filename": filename,
            "selection": filename,
            "quant": quant,
            "size_bytes": size,
            "size_gb": _bytes_to_gb(size),
            "fit": fit["fit"],
            "vram_gb": fit["vram_gb"],
            "importable": bool(quant) and compatibility_note is None,
            "compatibility_note": compatibility_note,
            "preflight": _hf_import_preflight(size),
        })
    return sorted(files, key=lambda f: (_quant_sort_key(f.get("quant")), f["filename"].lower()))


def _choose_hf_file(files: list[dict]) -> dict | None:
    importable = [f for f in files if f.get("importable")]
    if not importable:
        return files[0] if files else None
    non_blocked = [f for f in importable if f.get("fit") != "too_large"] or importable
    by_quant: dict[str, list[dict]] = {}
    for file in non_blocked:
        by_quant.setdefault(file.get("quant") or "", []).append(file)
    for quant in _GGUF_IMPORT_PREFERENCE:
        if quant in by_quant:
            return min(by_quant[quant], key=lambda f: f.get("size_bytes") or 0)
    return min(non_blocked, key=lambda f: _quant_sort_key(f.get("quant")))


def _hf_license(tags: list[str], card_data: dict | None) -> str | None:
    if isinstance(card_data, dict):
        license_name = card_data.get("license")
        if isinstance(license_name, str) and license_name:
            return license_name
    for tag in tags:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _hf_base_model(tags: list[str], card_data: dict | None) -> str | None:
    if isinstance(card_data, dict):
        base_model = card_data.get("base_model")
        if isinstance(base_model, str):
            return base_model
        if isinstance(base_model, list) and base_model and isinstance(base_model[0], str):
            return base_model[0]
    for tag in tags:
        if tag.startswith("base_model:") and not tag.startswith("base_model:quantized:"):
            return tag.split(":", 1)[1]
    return None


def _fetch_hf_model_detail(repo_id: str) -> dict | None:
    import urllib.parse
    import urllib.request

    now = time.time()
    with _HF_DETAIL_CACHE_LOCK:
        cached = _HF_DETAIL_CACHE.get(repo_id)
        if cached and now - cached[0] < HF_DETAIL_CACHE_TTL_S:
            return cached[1]

    encoded = "/".join(urllib.parse.quote(part, safe="") for part in repo_id.split("/"))
    req = urllib.request.Request(
        f"https://huggingface.co/api/models/{encoded}?blobs=true",
        headers={"Accept": "application/json", "User-Agent": f"LAC/{APP_VERSION}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    result = data if isinstance(data, dict) else None
    with _HF_DETAIL_CACHE_LOCK:
        _HF_DETAIL_CACHE[repo_id] = (time.time(), result)
        if len(_HF_DETAIL_CACHE) > HF_DETAIL_CACHE_MAX:
            oldest = min(_HF_DETAIL_CACHE, key=lambda key: _HF_DETAIL_CACHE[key][0])
            _HF_DETAIL_CACHE.pop(oldest, None)
    return result


_HF_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co"}
_HF_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


def _is_hf_repo_id(value: str) -> bool:
    return bool(_HF_REPO_ID_RE.match(value or ""))


def _looks_like_gguf_selector(value: str | None) -> bool:
    selector = (value or "").strip()
    if not selector:
        return False
    if selector.lower().endswith(".gguf"):
        return True
    normalized = selector.upper().replace("-", "_")
    if normalized in {"F16", "BF16"} or normalized.startswith(("Q2_", "Q3_", "Q4_", "Q5_", "Q6_", "Q8_")):
        return True
    return bool(_GGUF_QUANT_PAT.search(f"model-{normalized}.gguf"))


def _split_hf_model_selector(model_name: str, forced_hf: bool) -> tuple[str, str | None]:
    if ":" not in model_name:
        return model_name, None
    name, selector = model_name.rsplit(":", 1)
    if forced_hf or _looks_like_gguf_selector(selector):
        return name, selector
    return model_name, None


def _parse_hf_install_target(target: str) -> dict | None:
    """Recognize HF repo/page/file refs without importing the Pro package."""
    raw = (target or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return None

    candidate = raw
    if re.match(r"^(www\.)?(huggingface\.co|hf\.co)/", candidate, re.I):
        candidate = f"https://{candidate}"

    parsed = urlsplit(candidate)
    forced_hf = parsed.scheme in {"http", "https"} and parsed.netloc.lower() in _HF_HOSTS
    if forced_hf:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        model_name, selector = _split_hf_model_selector(parts[1], forced_hf=True)
        repo_id = f"{parts[0]}/{model_name}"
        file_selector = selector
        if len(parts) >= 5 and parts[2] in {"blob", "resolve"}:
            file_selector = "/".join(parts[4:])
        elif len(parts) >= 3 and parts[2].lower().endswith(".gguf"):
            file_selector = "/".join(parts[2:])
        if not _is_hf_repo_id(repo_id):
            return None
        return {
            "repo_id": repo_id,
            "selector": file_selector,
            "source_url": f"https://huggingface.co/{repo_id}",
            "forced_hf": True,
        }

    stripped = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    parts = [p for p in stripped.split("/") if p]
    if len(parts) < 2:
        return None

    model_name, selector = _split_hf_model_selector(parts[1], forced_hf=False)
    repo_id = f"{parts[0]}/{model_name}"
    if not _is_hf_repo_id(repo_id):
        return None

    if len(parts) == 2 and (":" not in parts[1] or selector):
        return {
            "repo_id": repo_id,
            "selector": selector,
            "source_url": f"https://huggingface.co/{repo_id}",
            "forced_hf": False,
        }

    if len(parts) >= 5 and parts[2] in {"blob", "resolve"}:
        return {
            "repo_id": repo_id,
            "selector": "/".join(parts[4:]),
            "source_url": f"https://huggingface.co/{repo_id}",
            "forced_hf": False,
        }

    if len(parts) >= 3 and parts[2].lower().endswith(".gguf"):
        return {
            "repo_id": repo_id,
            "selector": "/".join(parts[2:]),
            "source_url": f"https://huggingface.co/{repo_id}",
            "forced_hf": False,
        }

    return None


def _hf_gguf_result(
    item: dict,
    system_vram: float | None,
    ram_gb: float | None,
    detail: dict | None = None,
) -> dict | None:
    repo_id = item.get("id") or item.get("modelId")
    if not isinstance(repo_id, str):
        return None
    tags = [t for t in item.get("tags", []) if isinstance(t, str)]
    siblings = item.get("siblings", [])
    if not isinstance(siblings, list):
        siblings = []

    if detail:
        tags = [t for t in detail.get("tags", tags) if isinstance(t, str)]
        detail_siblings = detail.get("siblings", [])
        if isinstance(detail_siblings, list):
            siblings = detail_siblings

    filenames = [
        s.get("rfilename", "")
        for s in siblings
        if isinstance(s, dict) and isinstance(s.get("rfilename"), str)
    ]
    files = _hf_gguf_files(siblings, system_vram, ram_gb)
    if not files and not any(t.lower() == "gguf" for t in tags):
        return None

    selected = _choose_hf_file(files)
    card_data = detail.get("cardData") if isinstance(detail, dict) else item.get("cardData")
    pipeline_tag = (detail or item).get("pipeline_tag") if isinstance((detail or item), dict) else None
    return {
        "repo_id": repo_id,
        "author": (detail or item).get("author") if isinstance((detail or item), dict) else item.get("author"),
        "downloads": (detail or item).get("downloads") or item.get("downloads") or 0,
        "likes": (detail or item).get("likes") or item.get("likes") or 0,
        "gated": bool((detail or item).get("gated")) if isinstance((detail or item), dict) else bool(item.get("gated")),
        "last_modified": (detail or item).get("lastModified") if isinstance((detail or item), dict) else item.get("lastModified"),
        "tags": tags[:8],
        "license": _hf_license(tags, card_data if isinstance(card_data, dict) else None),
        "base_model": _hf_base_model(tags, card_data if isinstance(card_data, dict) else None),
        "pipeline_tag": pipeline_tag if isinstance(pipeline_tag, str) else None,
        "gguf_files": len(files) or len([f for f in filenames if f.lower().endswith(".gguf")]),
        "quants": _gguf_quants([f["filename"] for f in files] or filenames)[:16],
        "files": files[:18],
        "recommended_quant": selected.get("quant") if selected else None,
        "recommended_file": selected.get("filename") if selected else None,
        "recommended_size_gb": selected.get("size_gb") if selected else None,
        "fit": selected.get("fit") if selected else "unknown",
        "vram_gb": selected.get("vram_gb") if selected else None,
        "preflight": selected.get("preflight") if selected else _hf_import_preflight(None),
    }


def _hf_system_fit_context() -> tuple[float | None, float | None]:
    system_vram = None
    ram_gb = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
        ram_gb = info.ram_gb
    except Exception:
        pass
    return system_vram, ram_gb


def _select_hf_preflight_file(model: dict, selector: str | None) -> dict | None:
    files = model.get("files") if isinstance(model, dict) else None
    if not isinstance(files, list) or not files:
        return None

    normalized_selector = (selector or "").strip().lower()
    if normalized_selector:
        for file in files:
            filename = str(file.get("filename") or "")
            selection = str(file.get("selection") or filename)
            quant = str(file.get("quant") or "")
            if normalized_selector in {
                filename.lower(),
                selection.lower(),
                quant.lower(),
            }:
                return file
            if filename.lower().endswith(normalized_selector):
                return file

    recommended = model.get("recommended_file")
    if recommended:
        for file in files:
            if file.get("filename") == recommended:
                return file
    return files[0]


def _state_from_preflight(preflight: dict | None, blocked: bool = False) -> str:
    if blocked:
        return "blocked"
    state = (preflight or {}).get("state")
    if state in {"ok", "blocked", "unknown"}:
        return str(state)
    return "unknown"


def _hf_install_preflight(target: str, parsed: dict) -> dict:
    repo_id = parsed["repo_id"]
    selector = parsed.get("selector")
    base = {
        "target": target,
        "kind": "hf_unknown",
        "action": "import",
        "state": "unknown",
        "normalized": repo_id,
        "repo_id": repo_id,
        "source_url": parsed.get("source_url") or f"https://huggingface.co/{repo_id}",
        "selector": selector,
        "warnings": [],
    }

    try:
        detail = _fetch_hf_model_detail(repo_id)
    except Exception as exc:  # noqa: BLE001 - metadata lookup should not hard-fail Browse
        return {
            **base,
            "state": "error",
            "message": f"Could not inspect Hugging Face metadata: {exc}",
            "preflight": _hf_import_preflight(None),
        }

    if not isinstance(detail, dict):
        return {
            **base,
            "message": "Hugging Face metadata was unavailable; Pro can still try to inspect the repo during import.",
            "preflight": _hf_import_preflight(None),
        }

    system_vram, ram_gb = _hf_system_fit_context()
    mapped = _hf_gguf_result({"id": repo_id}, system_vram, ram_gb, detail=detail)
    if not mapped:
        warnings = []
        if detail.get("gated"):
            warnings.append("This repo is gated; save a Hugging Face token in Pro before importing.")
        return {
            **base,
            "state": "unknown",
            "message": "No GGUF artifact metadata found; Pro may use safetensors conversion if the architecture is supported.",
            "gated": bool(detail.get("gated")),
            "preflight": _hf_import_preflight(None),
            "warnings": warnings,
        }

    selected = _select_hf_preflight_file(mapped, selector)
    preflight = selected.get("preflight") if selected else mapped.get("preflight")
    warnings = list((preflight or {}).get("warnings") or [])
    blocked = (preflight or {}).get("state") == "blocked"
    if selected and not selected.get("importable", True):
        warnings.append(selected.get("compatibility_note") or "Selected GGUF file is not importable.")
        blocked = True
    if mapped.get("gated"):
        warnings.append("This repo is gated; save a Hugging Face token in Pro before importing.")

    state = _state_from_preflight(preflight if isinstance(preflight, dict) else None, blocked)
    selected_quant = selected.get("quant") if selected else mapped.get("recommended_quant")
    selected_file = selected.get("filename") if selected else mapped.get("recommended_file")
    selected_size_gb = selected.get("size_gb") if selected else mapped.get("recommended_size_gb")
    selected_size_bytes = selected.get("size_bytes") if selected else None
    return {
        **base,
        "kind": "hf_gguf",
        "state": state,
        "message": "Ready to import selected GGUF." if state == "ok" else "Review the selected GGUF before importing.",
        "gated": bool(mapped.get("gated")),
        "selected_file": selected_file,
        "selected_quant": selected_quant,
        "selected_size_bytes": selected_size_bytes,
        "selected_size_gb": selected_size_gb,
        "fit": selected.get("fit") if selected else mapped.get("fit"),
        "vram_gb": selected.get("vram_gb") if selected else mapped.get("vram_gb"),
        "recommended_file": mapped.get("recommended_file"),
        "recommended_quant": mapped.get("recommended_quant"),
        "preflight": preflight,
        "warnings": warnings,
    }


def _ollama_install_preflight(target: str) -> dict:
    model_ref = " ".join((target or "").split())
    models_dir = _default_ollama_models_dir()
    return {
        "target": target,
        "kind": "ollama",
        "action": "pull",
        "state": "unknown",
        "normalized": model_ref,
        "model_ref": model_ref,
        "message": "Ollama will report exact size during pull; LAC will stream progress in Downloads.",
        "model_store_dir": str(models_dir),
        "model_store": _space_check(_disk_free_bytes(models_dir), 0),
        "warnings": [],
    }


def _search_hf_gguf(query: str, limit: int = 12) -> dict:
    """Search public Hugging Face metadata for GGUF repos.

    This is deliberately open-core safe: it reads public HF model metadata only
    and never imports or calls lac_pro. The Pro plugin still owns importing.
    """
    query = " ".join((query or "").split())
    if not query:
        return {"query": query, "total": 0, "models": []}
    limit = max(1, min(int(limit or 12), 24))
    system_vram = None
    ram_gb = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
        ram_gb = info.ram_gb
    except Exception:
        pass

    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({
            "search": f"{query} gguf",
            "limit": str(limit),
            "full": "false",
        })
        req = urllib.request.Request(
            f"https://huggingface.co/api/models?{params}",
            headers={"Accept": "application/json", "User-Agent": f"LAC/{APP_VERSION}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - search is optional; Browse must still work
        return {"query": query, "total": 0, "models": [], "error": str(exc)}

    candidates = []
    seen: set[str] = set()
    for item in data if isinstance(data, list) else []:
        repo_id = item.get("id") or item.get("modelId")
        if not isinstance(repo_id, str) or repo_id in seen:
            continue
        seen.add(repo_id)
        candidates.append(item)

    details: dict[str, dict | None] = {}
    if candidates:
        workers = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_hf_model_detail, item.get("id") or item.get("modelId")): item
                for item in candidates
                if isinstance(item.get("id") or item.get("modelId"), str)
            }
            for future in as_completed(futures):
                item = futures[future]
                repo_id = item.get("id") or item.get("modelId")
                if not isinstance(repo_id, str):
                    continue
                try:
                    details[repo_id] = future.result()
                except Exception:
                    details[repo_id] = None

    out = []
    for item in candidates:
        repo_id = item.get("id") or item.get("modelId")
        detail = details.get(repo_id) if isinstance(repo_id, str) else None
        mapped = _hf_gguf_result(item, system_vram, ram_gb, detail=detail)
        if mapped:
            out.append(mapped)

    return {"query": query, "total": len(out), "system_vram": system_vram, "ram_gb": ram_gb, "models": out}


@app.route("/api/hf/gguf-search")
def api_hf_gguf_search():
    q = request.args.get("q", "").strip()
    try:
        limit = int(request.args.get("limit", "12"))
    except (TypeError, ValueError):
        limit = 12
    return jsonify(_search_hf_gguf(q, limit=limit))


@app.route("/api/model/install-preflight")
def api_model_install_preflight():
    target = request.args.get("target", "").strip()
    if not target:
        return jsonify({"error": "Missing target"}), 400
    parsed = _parse_hf_install_target(target)
    if parsed:
        return jsonify(_hf_install_preflight(target, parsed))
    return jsonify(_ollama_install_preflight(target))


@app.route("/api/ollama/library")
def ollama_library():
    result = _fetch_library()
    if isinstance(result, dict) and "error" in result:
        return jsonify(result)
    return jsonify(result)


@app.route("/api/ollama/ps")
def ollama_ps():
    resp = _ollama_request("GET", "/api/ps")
    if not isinstance(resp, dict) or "error" in resp:
        return jsonify({"error": "Ollama residency unavailable"}), 502
    models = []
    for m in resp.get("models", []):
        models.append({
            "name": m.get("name"),
            "size_gb": round(m.get("size", 0) / (1024**3), 2),
            "digest_short": (m.get("digest") or "")[:12],
        })
    return jsonify({"running": True, "models": models})


@app.route("/api/config/downloads")
def api_config_downloads():
    from .cookbook.downloads import download_history
    return jsonify(download_history())


@app.route("/api/config", methods=["GET"])
def api_get_config():
    from .cookbook.config import load_config
    cfg = load_config()
    return jsonify({
        "workspace": cfg.workspace,
        "ollama_host": cfg.ollama_host,
        "theme": cfg.theme,
        "default_model": cfg.default_model,
    })


@app.route("/api/config", methods=["PUT"])
def api_save_config():
    from .cookbook.config import load_config, save_config
    data = request.get_json() or {}
    cfg = load_config()
    for k in ("workspace", "ollama_host", "theme", "default_model"):
        if k in data:
            setattr(cfg, k, data[k])
    save_config(cfg)
    return jsonify({"success": True})


@app.route("/api/workspaces", methods=["GET"])
def api_list_workspaces():
    from .cookbook.config import list_workspaces
    ws = list_workspaces()
    return jsonify([{"id": w.id, "name": w.name, "description": w.description} for w in ws])


@app.route("/api/workspaces", methods=["POST"])
def api_create_workspace():
    from .cookbook.config import create_workspace
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Workspace name required"}), 400
    try:
        ws = create_workspace(name, data.get("description", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description}), 201


@app.route("/api/workspaces/<workspace_id>", methods=["GET"])
def api_get_workspace(workspace_id):
    from .cookbook.config import get_workspace
    ws = get_workspace(workspace_id)
    if not ws:
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description})


@app.route("/api/workspaces/<workspace_id>", methods=["DELETE"])
def api_delete_workspace(workspace_id):
    from .cookbook.config import delete_workspace
    from .cookbook.persistence import list_projects

    if list_projects(workspace_id):
        return jsonify({
            "error": "Cannot delete a workspace with registered projects"
        }), 409
    if delete_workspace(workspace_id):
        return jsonify({"success": True})
    if list_projects(workspace_id):
        return jsonify({
            "error": "Cannot delete a workspace with registered projects"
        }), 409
    return jsonify({"error": "Cannot delete default workspace or workspace not found"}), 400


@app.route("/api/workspaces/<workspace_id>/switch", methods=["POST"])
def api_switch_workspace(workspace_id):
    from .cookbook.config import switch_workspace
    if not switch_workspace(workspace_id):
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"success": True, "workspace": workspace_id})


@app.route("/api/workspaces/<workspace_id>/projects", methods=["GET", "POST"])
def api_workspace_projects(workspace_id):
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project records are available only on this machine"}), 403

    from .cookbook.config import get_workspace
    from .cookbook.persistence import (
        ProjectConflictError,
        create_project,
        list_projects,
    )

    if get_workspace(workspace_id) is None:
        return jsonify({"error": "Workspace not found"}), 404
    if request.method == "GET":
        return jsonify([_public_project(row) for row in list_projects(workspace_id)])

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    name = data.get("name")
    root = data.get("root")
    description = data.get("description", "")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "Project name required"}), 400
    if not isinstance(root, str) or not root.strip():
        return jsonify({"error": "Project root required"}), 400
    if not isinstance(description, str):
        return jsonify({"error": "Project description must be text"}), 400
    try:
        project = create_project(
            workspace=workspace_id,
            name=name,
            root=root,
            description=description,
        )
    except ProjectConflictError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(_public_project(project)), 201


@app.route("/api/projects/<project_id>", methods=["GET"])
def api_get_project(project_id):
    if not _is_trusted_local_approval_request():
        return jsonify({"error": "Project records are available only on this machine"}), 403

    from .cookbook.persistence import get_project

    project = get_project(project_id)
    if project is None:
        return jsonify({"error": "Project not found"}), 404
    return jsonify(_public_project(project))


@app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    from .cookbook.persistence import list_sessions
    ws = request.args.get("workspace", "")
    raw_limit = request.args.get("limit")
    limit = None
    if raw_limit:
        try:
            limit = max(1, min(int(raw_limit), 500))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
    project_id = request.args.get("project_id")
    if not _is_trusted_local_approval_request():
        if project_id not in (None, "", "unassigned"):
            return jsonify({
                "error": "Project-bound threads are available only on this machine"
            }), 403
        project_id = "unassigned"
    try:
        rows = list_sessions(workspace=ws, limit=limit, project_id=project_id)
    except ValueError as e:
        message = str(e)
        if "does not exist" in message:
            return jsonify({"error": "Project not found"}), 404
        if "workspace" in message:
            return jsonify({"error": message}), 409
        return jsonify({"error": message}), 400
    return jsonify(rows)


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    from .cookbook.persistence import create_session, get_project
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    workspace = str(data.get("workspace") or "").strip()
    project_id = str(data.get("project_id") or "").strip()
    if project_id and not _is_trusted_local_approval_request():
        return jsonify({
            "error": "Project-bound threads are available only on this machine"
        }), 403
    if project_id:
        project = get_project(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        project_workspace = str(project.get("workspace") or "")
        if workspace and workspace != project_workspace:
            return jsonify({"error": "Workspace does not match the selected project"}), 409
        workspace = project_workspace
    sid = create_session(
        name=data.get("name", ""),
        model=data.get("model", ""),
        system_prompt=data.get("system_prompt", ""),
        workspace=workspace,
        project_id=project_id or None,
    )
    return jsonify({"id": sid}), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id):
    from .cookbook.persistence import get_session
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    if session.get("project_id") and not _is_trusted_local_approval_request():
        return jsonify({
            "error": "Project-bound threads are available only on this machine"
        }), 403
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["PUT"])
def api_save_session(session_id):
    from .cookbook.persistence import get_project, get_session, save_session
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    existing = get_session(session_id)
    workspace = str(data.get("workspace") or "").strip()
    project_id = str(data.get("project_id") or "").strip()
    if (
        (project_id or (existing is not None and existing.get("project_id")))
        and not _is_trusted_local_approval_request()
    ):
        return jsonify({
            "error": "Project-bound threads are available only on this machine"
        }), 403
    if existing is not None:
        existing_workspace = str(existing.get("workspace") or "")
        existing_project = str(existing.get("project_id") or "")
        if workspace and workspace != existing_workspace:
            return jsonify({"error": "Thread belongs to a different workspace"}), 409
        if project_id and project_id != existing_project:
            return jsonify({"error": "Thread belongs to a different project"}), 409
    elif project_id:
        project = get_project(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        project_workspace = str(project.get("workspace") or "")
        if workspace and workspace != project_workspace:
            return jsonify({"error": "Workspace does not match the selected project"}), 409
        workspace = project_workspace
    save_session(
        session_id=session_id,
        model=data.get("model", ""),
        messages=data.get("messages", []),
        name=data.get("name", ""),
        workspace=workspace,
        project_id=project_id or None,
    )
    return jsonify({"success": True})


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    from .cookbook.persistence import delete_session, get_session
    session = get_session(session_id)
    if (
        session is not None
        and session.get("project_id")
        and not _is_trusted_local_approval_request()
    ):
        return jsonify({
            "error": "Project-bound threads are available only on this machine"
        }), 403
    delete_session(session_id)
    return jsonify({"success": True})


@app.route("/api/pro/unlock", methods=["POST"])
def api_pro_unlock():
    """Activate LAC Pro from the browser — the twin of `lac unlock <key>`.

    Hand the license key to install_pro_plugin (which fetches the licensed
    plugin from the delivery gate and installs it) and return its honest dict
    verbatim. That helper NEVER raises: a failed install is reported in the
    body as {"state":"failed","error_type","message"} at HTTP 200 — the
    frontend branches on `state`. A 400 is reserved for a malformed request
    body only (missing or non-string key), matching the guard idiom used by
    the other POST routes in this file.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    key = data.get("key")
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "License key required"}), 400
    return jsonify(install_pro_plugin(key.strip()))


@app.route("/api/pro/activate", methods=["POST"])
def api_pro_activate():
    """Self-serve Pro: install the plugin, then write the license grant by
    running `lac pro activate` in a throwaway process with the key on STDIN
    (never argv). Honest JSON states; never raises."""
    data = request.get_json(silent=True)
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "License key required"}), 400
    key = key.strip()

    installed = install_pro_plugin(key)
    if installed.get("state") != "installed":
        return jsonify({"state": "install_failed", **{k: v for k, v in installed.items() if k != "state"}})

    try:
        r = proc.run([*self_invoke.cli_prefix(), "pro", "activate"],
                     input=key + "\n", capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 — subprocess spawn failure
        return jsonify({"state": "activation_failed",
                        "message": f"Could not run activation: {e}"})
    if r.returncode != 0:
        lines = (r.stdout or r.stderr or "activation failed").strip().splitlines()
        msg = lines[-1].strip() if lines else "activation failed"
        return jsonify({"state": "activation_failed", "message": msg})
    return jsonify({"state": "activated"})


@app.route("/api/app/relaunch", methods=["POST"])
def api_app_relaunch():
    """Self-relaunch the desktop window so a freshly-installed Pro plugin
    mounts on a clean startup. desktop.relaunch() exits this process on
    success (the response never reaches the client); on failure it returns
    False without exiting, so we report a normal JSON body and the user can
    restart LAC manually."""
    from backend import desktop
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    view = data.get("view")
    bounds = data.get("bounds")
    ok = desktop.relaunch(view=view, bounds=bounds)
    if not ok:
        return jsonify({"state": "failed",
                         "message": "Could not relaunch; please restart LAC manually."})
    return jsonify({"state": "relaunching"})


@app.errorhandler(404)
def spa_fallback(_e):
    # Client-side routes (e.g. /browse, /chat) -> index.html; API 404 -> JSON.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    index_path = Path(app.static_folder) / "index.html"
    if index_path.exists():
        return app.send_static_file("index.html")
    return (
        "Web app not built. Run `npm run build` inside web/, or `npm run dev` for development.",
        404,
    )


@app.errorhandler(400)
def bad_request_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Bad request"}), 400
    return e


@app.errorhandler(405)
def method_not_allowed_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method not allowed"}), 405
    return e


def run_server(host="127.0.0.1", port=5050, debug=False):
    _configure_trusted_server_host(host)
    print(f"  LAC running at http://{host}:{port}")
    print(f"  Open your browser to that address.\n")
    # Pre-warm the library cache in the background so Browse loads instantly.
    threading.Thread(target=_fetch_library, daemon=True).start()
    # threaded=True is a LOAD-BEARING invariant: /api/agent/runs/<id>/answer must be
    # servable while /api/agent/chat streams (the ask bridge deadlocks otherwise).
    app.run(host=host, port=port, debug=debug, threaded=True)


# --- plugin seam -----------------------------------------------------------

def _discover_plugins_safe():
    """Call plugins.discover(), isolating discovery-itself failures.

    Mirrors the CLI-layer guard in cli.py: a broken discover() (e.g. a
    corrupt entry point) must never break core — warn and act as if no
    plugins are installed.
    """
    from backend import plugins as _plugins
    try:
        return _plugins.discover()
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the API
        print(f"[plugins] discovery failed: {e}")
        return []


def _local_pro_product_state(loaded_plugins) -> dict:
    candidates = [
        plugin for plugin in loaded_plugins
        if plugin.product_id == "local_pro" or plugin.name == "pro"
    ]
    if not candidates:
        return {"state": "absent"}
    if len(candidates) != 1:
        return {
            "state": "incompatible",
            "plugin_version": "multiple",
            "host_api_version": None,
        }
    plugin = candidates[0]
    if plugin.state != "ready" or plugin.product_state is None:
        return {
            "state": plugin.state,
            "plugin_version": plugin.version,
            "host_api_version": plugin.host_api_version,
        }
    return {
        "state": "ready",
        "plugin_version": plugin.version,
        "host_api_version": plugin.host_api_version,
        **plugin.product_state,
    }


def _cloud_error_response(exc: CloudSessionError | SecureTokenStoreError, status=400):
    code = exc.code
    if isinstance(exc, SecureTokenStoreError) and code not in {
        "corrupt_store", "secure_storage_unavailable"
    }:
        code = "secure_storage_unavailable"
    response = jsonify({"error": {"code": code}})
    response.headers["Cache-Control"] = "no-store"
    return response, status


_CLOUD_PROXY_ERROR_STATUS = {
    "auth_required": 401,
    "quota_exhausted": 402,
    "entitlement_required": 403,
    "conflict_or_concurrency": 409,
    "abuse_rate_limited": 429,
    "invalid_response": 502,
    "provider_unavailable": 503,
    "corrupt_store": 503,
    "secure_storage_unavailable": 503,
}
_CLOUD_JOB_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _cloud_json_response(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _cloud_invalid_request():
    return _cloud_json_response({"error": {"code": "invalid_request"}}, 400)


def _cloud_request_has_body() -> bool:
    if request.content_length not in {None, 0}:
        return True
    try:
        return bool(request.stream.read(1))
    except Exception:  # noqa: BLE001 - unreadable request bodies fail closed
        return True


def _cloud_local_guard():
    if _is_trusted_local_approval_request():
        return None
    return _cloud_json_response(
        {"error": {"code": "local_request_required"}},
        403,
    )


def _cloud_proxy_error_response(exc: CloudSessionError | SecureTokenStoreError):
    code = exc.code
    if isinstance(exc, SecureTokenStoreError) and code not in {
        "corrupt_store", "secure_storage_unavailable"
    }:
        code = "secure_storage_unavailable"
    status = _CLOUD_PROXY_ERROR_STATUS.get(code, 503)
    stable_code = code if code in _CLOUD_PROXY_ERROR_STATUS else "provider_unavailable"
    return _cloud_json_response({"error": {"code": stable_code}}, status)


@app.route("/api/product/state")
def api_product_state():
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    loaded = _discover_plugins_safe()
    response = jsonify({
        "schema_version": 1,
        "execution_default": "local",
        "local": {"state": "ready"},
        "local_pro": _local_pro_product_state(loaded),
        "cloud": _cloud_session.product_state(refresh=True),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/cloud/auth/start", methods=["POST"])
def api_cloud_auth_start():
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    if request.content_length is not None and request.content_length > 1024:
        return _cloud_invalid_request()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"provider"}:
        return _cloud_invalid_request()
    try:
        result = _cloud_session.start_authorization(data["provider"])
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_error_response(exc)
    return _cloud_json_response(
        {key: value for key, value in result.items() if key != "authorization_url"}
    )


@app.route("/api/cloud/auth/callback", methods=["POST"])
def api_cloud_auth_callback():
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    if request.content_length is not None and request.content_length > 4096:
        return _cloud_invalid_request()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"callback_uri"}:
        return _cloud_invalid_request()
    try:
        return _cloud_json_response(
            _cloud_session.complete_authorization(data["callback_uri"])
        )
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_error_response(exc)


@app.route("/api/cloud/logout", methods=["POST"])
def api_cloud_logout():
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    try:
        return _cloud_json_response(_cloud_session.logout())
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_error_response(exc)


@app.route("/api/cloud/jobs", methods=["GET"])
def api_cloud_jobs():
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    if request.args:
        return _cloud_invalid_request()
    try:
        return _cloud_json_response(_cloud_session.list_jobs())
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_proxy_error_response(exc)


@app.route("/api/cloud/jobs/<job_id>/events", methods=["GET"])
def api_cloud_job_events(job_id):
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    values = request.args.getlist("after_sequence")
    if (
        _CLOUD_JOB_ID.fullmatch(job_id) is None
        or set(request.args) != {"after_sequence"}
        or len(values) != 1
        or re.fullmatch(r"(?:-1|0|[1-9][0-9]{0,15})", values[0]) is None
    ):
        return _cloud_invalid_request()
    after_sequence = int(values[0])
    if after_sequence > 9_007_199_254_740_991:
        return _cloud_invalid_request()
    try:
        return _cloud_json_response(
            _cloud_session.job_events(job_id, after_sequence)
        )
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_proxy_error_response(exc)


@app.route("/api/cloud/jobs/<job_id>/cancel", methods=["POST"])
def api_cloud_job_cancel(job_id):
    local_guard = _cloud_local_guard()
    if local_guard is not None:
        return local_guard
    if (
        _CLOUD_JOB_ID.fullmatch(job_id) is None
        or request.args
        or _cloud_request_has_body()
    ):
        return _cloud_invalid_request()
    try:
        return _cloud_json_response(_cloud_session.cancel_job(job_id), 202)
    except (CloudSessionError, SecureTokenStoreError) as exc:
        return _cloud_proxy_error_response(exc)


def _notify_model_installed(model_name: str) -> None:
    """Call every plugin's on_model_installed(model_name), isolated per-plugin
    (mirrors _mount_plugins()'s isolation). A missing hook, a plugin that
    isn't installed, or a raising hook must never affect the install that
    already succeeded."""
    for p in _discover_plugins_safe():
        hook = getattr(p.obj, "on_model_installed", None)
        if not p.ok or hook is None:
            continue
        try:
            hook(model_name)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] on_model_installed failed: {e}")


def _notify_model_installed_async(model_name: str) -> None:
    """Fire _notify_model_installed in a background thread so a slow plugin
    hook (e.g. LAC Pro's benchmark+sweep+apply autopilot) never delays the
    pull's HTTP response. Mirrors _refresh_library_background()'s pattern."""
    threading.Thread(target=_notify_model_installed, args=(model_name,), daemon=True).start()


@app.route("/api/plugins")
def api_plugins():
    return jsonify([
        {
            "name": p.name,
            "version": p.version,
            "ok": p.ok,
            "state": p.state,
            "host_api_version": p.host_api_version,
            "error": p.error or p.compatibility_error,
        }
        for p in _discover_plugins_safe()
    ])


def _mount_plugins(flask_app):
    """Call each plugin's register_api(app). Isolated: a broken plugin logs and moves on."""
    for p in _discover_plugins_safe():
        reg = getattr(p.obj, "register_api", None)
        if not p.ok or reg is None:
            continue
        try:
            reg(flask_app)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] register_api failed: {e}")


_mount_plugins(app)
