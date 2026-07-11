"""Fail-closed Docker task sandbox for web Workbench Build mode.

The model selects only an operator-configured task name.  The image, argv,
Docker context, mounts, network posture, resources, cwd, user, and timeout are
all frozen server-side before approval.  The real project is never mounted:
execution receives a bounded disposable copy plus the exact pending staged
overlay for one session/root.
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from backend.config import (
    AptProjectConfig,
    SandboxConfig,
    SandboxTaskConfig,
    strip_jsonc,
)
from backend.cookbook import persistence, proc
from backend.cookbook.config import CONFIG_DIR
from backend.project_paths import validate_relative_project_path

DOCKER_PROBE_TIMEOUT_SECONDS = 5.0
DOCKER_CONTROL_TIMEOUT_SECONDS = 15.0
DOCKER_CREATE_RECONCILE_SECONDS = 15.0
DOCKER_CREATE_RECONCILE_POLL_SECONDS = 0.1
MAX_DOCKER_CONTROL_OUTPUT_BYTES = 64 * 1024
MAX_TASK_OUTPUT_BYTES = 64 * 1024
MAX_SANDBOX_CONFIG_BYTES = 1024 * 1024
MAX_SNAPSHOT_FILES = 20_000
MAX_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_STAGED_ROWS = 16
MAX_STAGED_OVERLAY_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_ENTRIES = 40_000
MAX_SNAPSHOT_DIRECTORIES = 20_000
MAX_SNAPSHOT_DEPTH = 64
MAX_SNAPSHOT_PATH_CHARS = 512
MAX_SNAPSHOT_ARCHIVE_BYTES = 384 * 1024 * 1024
MAX_CONCURRENT_TASKS = 2

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_LOCAL_NPIPE = re.compile(
    r"^npipe:////\./pipe/[A-Za-z0-9_.-]+$", re.IGNORECASE
)
_SANDBOX_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_TASKS)
_SANDBOX_BOOTSTRAP = (
    'set -eu; limit="$1"; shift; '
    'exec /usr/bin/env -i '
    'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin '
    'HOME=/workspace TMPDIR=/tmp LANG=C.UTF-8 LC_ALL=C.UTF-8 '
    '/usr/bin/timeout --signal=TERM --kill-after=2s "$limit" '
    '/usr/bin/setpriv --reuid=65532 '
    '--bounding-set=-all --inh-caps=-all --ambient-caps=-all '
    '--securebits=+noroot,+noroot_locked '
    '--no-new-privs --pdeathsig=KILL -- /bin/sh -c '
    "'set -eu; /usr/bin/tar --restrict --extract --file=- --directory=/workspace "
    "--no-same-owner --no-same-permissions --keep-old-files; cd /workspace; "
    "exec \"$@\"' "
    'lac-task "$@"'
)
_OWNERSHIP_FORMAT = (
    '{{.Id}}|{{index .Config.Labels "com.lac.managed"}}|'
    '{{index .Config.Labels "com.lac.owner"}}|'
    '{{index .Config.Labels "com.lac.execution"}}'
)


def _redact_task_output(output: str, container_id: str, workspace: Path) -> str:
    """Remove daemon/runtime identifiers before durable tool-result storage."""

    redacted = str(output)
    snapshot_paths = {
        str(workspace),
        workspace.as_posix(),
        str(workspace.parent),
        workspace.parent.as_posix(),
    }
    replacements = [
        (container_id, "<container>"),
        (container_id[:12], "<container>"),
        *((value, "<snapshot>") for value in snapshot_paths if value),
    ]
    for value, marker in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        redacted = re.sub(re.escape(value), marker, redacted, flags=re.IGNORECASE)
    return redacted


class _CrossProcessSlot:
    def __init__(self, handle, index: int):
        self.handle = handle
        self.index = index

    def release(self) -> None:
        handle = self.handle
        if handle is None:
            return
        self.handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(self.index)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.lockf(
                    handle.fileno(),
                    fcntl.LOCK_UN,
                    1,
                    self.index,
                    os.SEEK_SET,
                )
        except OSError:
            pass
        finally:
            handle.close()


def _acquire_cross_process_slot() -> _CrossProcessSlot | None:
    """Acquire one of two user-local byte-range locks without blocking."""

    path = Path(tempfile.gettempdir()) / "lac-agent-sandbox-slots.lock"
    try:
        handle = path.open("a+b", buffering=0)
        if handle.seek(0, os.SEEK_END) < MAX_CONCURRENT_TASKS:
            handle.write(b"\0" * MAX_CONCURRENT_TASKS)
    except OSError:
        return None
    for index in range(MAX_CONCURRENT_TASKS):
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(index)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.lockf(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                    1,
                    index,
                    os.SEEK_SET,
                )
            return _CrossProcessSlot(handle, index)
        except OSError:
            continue
    handle.close()
    return None

_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".apt",
        ".hg",
        ".svn",
        ".ssh",
        ".docker",
        ".aws",
        ".azure",
        ".gcloud",
        "gcloud",
        ".kube",
        ".terraform",
        ".pulumi",
        ".secrets",
        "secrets",
        ".direnv",
        "credentials",
        ".credentials",
        "tokens",
        ".tokens",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".cache",
        ".tmp",
        "dist",
        "build",
        "agent-sandboxes",
    }
)
_EXCLUDED_FILE_NAMES = frozenset(
    {
        "credentials.json",
        "token.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "docker-config.json",
        ".git",
        ".envrc",
        ".vault-token",
        "client_secret.json",
        "service-account.json",
        "service_account.json",
    }
)
_EXCLUDED_FILE_SUFFIXES = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".gguf",
        ".safetensors",
        ".ckpt",
        ".onnx",
        ".bin",
        ".pt",
        ".pth",
    }
)


class SandboxError(RuntimeError):
    """A bounded public failure code with a non-sensitive message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    output_limited: bool = False

    @property
    def output(self) -> str:
        parts = [part.strip() for part in (self.stdout, self.stderr) if part.strip()]
        return "\n".join(parts)


class _BoundedOutput:
    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self.total = 0
        self._kept = {"stdout": bytearray(), "stderr": bytearray()}
        self.exceeded = threading.Event()
        self.lock = threading.Lock()

    def add(self, channel: str, chunk: bytes) -> None:
        if not chunk:
            return
        with self.lock:
            self.total += len(chunk)
            kept_total = sum(len(value) for value in self._kept.values())
            remaining = self.limit - kept_total
            if remaining > 0:
                self._kept[channel].extend(chunk[:remaining])
            if self.total > self.limit:
                self.exceeded.set()

    def text(self, channel: str) -> str:
        with self.lock:
            return bytes(self._kept[channel]).decode("utf-8", errors="replace")


def _docker_cli_env(executable: Path) -> dict[str, str]:
    """Keep the inherited desktop identity, but remove environment-selected Docker behavior."""

    env = dict(os.environ)
    for key in tuple(env):
        if key.upper().startswith("DOCKER_"):
            env.pop(key, None)
    if os.name == "nt":
        # Docker is always invoked by its absolute canonical path. Keeping an
        # untrusted project directory in PATH would still expose child/helper
        # and DLL lookup to that project before the user approves a task.
        env["PATH"] = str(executable.parent)
    return env


def _trusted_windows_program_files_roots() -> tuple[Path, ...]:
    """Read the native Program Files root from the machine registry, not env/PATH."""

    if os.name != "nt":
        return ()
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion",
            0,
            access,
        ) as key:
            values: list[str] = []
            for name in ("ProgramW6432Dir", "ProgramFilesDir"):
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                values.append(str(value).strip())
    except OSError:
        return ()

    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        root = Path(value)
        if not value or not root.is_absolute():
            continue
        normalized = os.path.normcase(str(root.absolute()))
        if normalized not in seen:
            seen.add(normalized)
            roots.append(root)
    return tuple(roots)


def _trusted_windows_docker_executable() -> str | None:
    """Resolve only Docker Desktop's canonical Program Files PE executable."""

    roots = [
        root / "Docker" / "Docker" / "resources" / "bin"
        for root in _trusted_windows_program_files_roots()
    ]
    seen: set[str] = set()
    for root in roots:
        candidate = root / "docker.exe"
        key = os.path.normcase(str(candidate.absolute()))
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved = candidate.resolve(strict=True)
            if os.path.normcase(str(resolved)) != key:
                continue
        except OSError:
            continue
        try:
            st = candidate.lstat()
            if (
                _is_reparse(st)
                or not stat.S_ISREG(st.st_mode)
                or st.st_size < 2
                or st.st_size > 512 * 1024 * 1024
            ):
                continue
            fd = _open_config_no_follow(candidate)
            try:
                opened_st = os.fstat(fd)
                header = os.read(fd, 2)
            finally:
                os.close(fd)
            if (
                _is_reparse(opened_st)
                or not stat.S_ISREG(opened_st.st_mode)
                or (opened_st.st_dev, opened_st.st_ino) != (st.st_dev, st.st_ino)
                or header != b"MZ"
            ):
                continue
            final_st = candidate.lstat()
            if (
                _is_reparse(final_st)
                or (final_st.st_dev, final_st.st_ino) != (st.st_dev, st.st_ino)
                or final_st.st_size != st.st_size
                or final_st.st_mtime_ns != st.st_mtime_ns
            ):
                continue
            return str(resolved)
        except OSError:
            continue
    return None


class DefaultProcessAdapter:
    """Bounded subprocess adapter used for Docker CLI control and attachment."""

    def which(self, name: str) -> str | None:
        if os.name != "nt":
            return None
        if name.casefold() != "docker":
            return None
        return _trusted_windows_docker_executable()

    def run(
        self,
        argv,
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        cancel_event: threading.Event | None = None,
        stdin_path: str | Path | None = None,
    ) -> ProcessResult:
        if cancel_event is not None and cancel_event.is_set():
            return ProcessResult(returncode=130, cancelled=True)

        command = [str(value) for value in argv]
        try:
            executable = Path(command[0])
            if not executable.is_absolute():
                raise OSError("subprocess executable is not absolute")
            executable = executable.resolve(strict=True)
            trusted_cwd = str(executable.parent)
        except (IndexError, OSError):
            return ProcessResult(returncode=127, stderr="untrusted executable")

        stdin_handle = None
        try:
            if stdin_path is not None:
                stdin_handle = Path(stdin_path).open("rb", buffering=0)
            child = proc.popen(
                command,
                shell=False,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_docker_cli_env(executable),
                cwd=trusted_cwd,
            )
        except Exception as exc:  # noqa: BLE001 - converted to bounded result
            if stdin_handle is not None:
                stdin_handle.close()
            return ProcessResult(returncode=127, stderr=type(exc).__name__)

        combined = _BoundedOutput(output_limit_bytes)

        def drain(stream, channel: str, target: _BoundedOutput) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    target.add(channel, chunk)
            except Exception:
                return

        readers = [
            threading.Thread(
                target=drain, args=(child.stdout, "stdout", combined), daemon=True
            ),
            threading.Thread(
                target=drain, args=(child.stderr, "stderr", combined), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + max(0.01, float(timeout_seconds))
        cancelled = timed_out = output_limited = False
        while child.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if combined.exceeded.is_set():
                output_limited = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.02)

        if child.poll() is None and (cancelled or timed_out or output_limited):
            try:
                child.terminate()
                child.wait(timeout=1.0)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        try:
            child.wait(timeout=2.0)
        except Exception:
            pass
        for reader in readers:
            reader.join(timeout=1.0)
        if stdin_handle is not None:
            stdin_handle.close()

        output_limited = output_limited or combined.total > int(output_limit_bytes)
        return ProcessResult(
            returncode=int(child.returncode if child.returncode is not None else -1),
            stdout=combined.text("stdout"),
            stderr=combined.text("stderr"),
            timed_out=timed_out,
            cancelled=cancelled,
            output_limited=output_limited,
        )


@dataclass(frozen=True)
class SandboxCapability:
    available: bool
    code: str
    message: str
    tasks: tuple[str, ...] = ()
    image: str | None = None
    network: str = "none"
    image_id: str | None = field(default=None, repr=False, compare=False)
    docker_executable: str | None = field(default=None, repr=False, compare=False)
    context: str | None = field(default=None, repr=False, compare=False)
    context_endpoint: str | None = field(default=None, repr=False, compare=False)
    config: SandboxConfig | None = field(default=None, repr=False, compare=False)
    config_digest: str | None = field(default=None, repr=False, compare=False)
    process_adapter: Any | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": "docker",
            "available": self.available,
            "code": self.code,
            "message": self.message,
            "tasks": list(self.tasks),
            "image": self.image,
            "network": "none",
        }


def _process_json(result: ProcessResult) -> Any:
    if result.returncode != 0 or result.timed_out or result.cancelled:
        raise ValueError("Docker command failed")
    return json.loads(result.stdout.strip())


def _unavailable(
    code: str,
    message: str,
    *,
    config: SandboxConfig | None = None,
    config_digest: str | None = None,
    docker_executable: str | None = None,
    context_endpoint: str | None = None,
) -> SandboxCapability:
    return SandboxCapability(
        available=False,
        code=code,
        message=message,
        tasks=tuple(sorted(config.tasks)) if config else (),
        image=config.image if config else None,
        config=config,
        config_digest=config_digest,
        docker_executable=docker_executable,
        context=config.context if config else None,
        context_endpoint=context_endpoint,
    )


def _config_at_exact_root(root: Path) -> tuple[SandboxConfig, str] | None:
    apt_dir = root / ".apt"
    path = apt_dir / "apt.jsonc"
    try:
        root_st = root.lstat()
        apt_st = apt_dir.lstat()
        st = path.lstat()
    except OSError:
        return None
    if (
        _is_reparse(root_st)
        or _is_reparse(apt_st)
        or _is_reparse(st)
        or not stat.S_ISDIR(root_st.st_mode)
        or not stat.S_ISDIR(apt_st.st_mode)
        or not stat.S_ISREG(st.st_mode)
        or apt_st.st_dev != root_st.st_dev
        or st.st_dev != root_st.st_dev
        or int(getattr(st, "st_nlink", 1)) != 1
        or st.st_size > MAX_SANDBOX_CONFIG_BYTES
    ):
        return None

    fd: int | None = None
    try:
        fd = _open_config_no_follow(path)
        opened_st = os.fstat(fd)
        if (
            _is_reparse(opened_st)
            or not stat.S_ISREG(opened_st.st_mode)
            or int(getattr(opened_st, "st_nlink", 1)) != 1
            or opened_st.st_size > MAX_SANDBOX_CONFIG_BYTES
            or (opened_st.st_dev, opened_st.st_ino) != (st.st_dev, st.st_ino)
        ):
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, MAX_SANDBOX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SANDBOX_CONFIG_BYTES:
                return None
            chunks.append(chunk)
        raw = b"".join(chunks)
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    try:
        final_root_st = root.lstat()
        final_apt_st = apt_dir.lstat()
        final_st = path.lstat()
    except OSError:
        return None
    if (
        _is_reparse(final_root_st)
        or _is_reparse(final_apt_st)
        or _is_reparse(final_st)
        or (final_root_st.st_dev, final_root_st.st_ino)
        != (root_st.st_dev, root_st.st_ino)
        or (final_apt_st.st_dev, final_apt_st.st_ino)
        != (apt_st.st_dev, apt_st.st_ino)
        or int(getattr(final_st, "st_nlink", 1)) != 1
        or (final_st.st_dev, final_st.st_ino) != (st.st_dev, st.st_ino)
        or final_st.st_size != st.st_size
        or final_st.st_mtime_ns != st.st_mtime_ns
    ):
        return None
    try:
        parsed = json.loads(strip_jsonc(raw.decode("utf-8")))
        project = AptProjectConfig.model_validate(parsed)
    except Exception:
        return None
    if project.sandbox is None:
        return None
    return project.sandbox, hashlib.sha256(raw).hexdigest()


def _open_config_no_follow(path: Path) -> int:
    """Open one config file without following the final Windows reparse point."""

    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x00000080 | 0x00200000 | 0x08000000,  # NORMAL, OPEN_REPARSE, SEQUENTIAL
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(error, "project config could not be opened")
    try:
        return msvcrt.open_osfhandle(
            int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise


def _parse_image_inspect(
    value: Any,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError("unexpected image inspect result")
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError("unexpected image inspect result")
    image_id = str(value.get("id") or value.get("Id") or "")
    image_os = str(value.get("os") or value.get("Os") or "")
    raw_digests = value.get("repo_digests")
    if raw_digests is None:
        raw_digests = value.get("RepoDigests")
    digests = tuple(str(item) for item in (raw_digests or []))
    image_config = value.get("config")
    if image_config is None:
        image_config = value.get("Config")
    if image_config is None:
        image_config = {}
    if not isinstance(image_config, dict):
        raise ValueError("unexpected image config")
    raw_volumes = image_config.get("volumes")
    if raw_volumes is None:
        raw_volumes = image_config.get("Volumes")
    if raw_volumes is None:
        raw_volumes = {}
    if not isinstance(raw_volumes, dict):
        raise ValueError("unexpected image volumes")
    volumes = tuple(sorted(str(item) for item in raw_volumes))
    return image_id, image_os, digests, volumes


def probe_project_sandbox(
    root: str | Path,
    *,
    process_adapter: Any | None = None,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> SandboxCapability:
    """Return bounded public readiness without pulling, building, or starting."""

    project_root = Path(root).resolve()
    configured = _config_at_exact_root(project_root)
    if configured is None:
        return _unavailable(
            "sandbox_unconfigured",
            "No Docker task sandbox is configured for this project root",
        )
    config, config_digest = configured
    adapter = process_adapter or DefaultProcessAdapter()
    docker = adapter.which("docker")
    if not docker:
        return _unavailable(
            "docker_cli_missing",
            "Docker Desktop is not installed or its CLI is unavailable",
            config=config,
            config_digest=config_digest,
        )

    def probe_timeout() -> float:
        _check_external_budget(deadline, cancel_event)
        if deadline is None:
            return DOCKER_PROBE_TIMEOUT_SECONDS
        return min(
            DOCKER_PROBE_TIMEOUT_SECONDS,
            max(0.01, deadline - time.monotonic()),
        )

    def run_probe(argv: list[str]) -> ProcessResult:
        result = adapter.run(
            argv,
            timeout_seconds=probe_timeout(),
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
            cancel_event=cancel_event,
        )
        _check_external_budget(deadline, cancel_event)
        return result

    try:
        context_result = run_probe(
        [
            docker,
            "context",
            "inspect",
            config.context,
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ]
        )
    except SandboxError as exc:
        return _unavailable(
            exc.code,
            exc.message,
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
        )
    try:
        endpoint = str(_process_json(context_result))
    except Exception:
        return _unavailable(
            "docker_context_unavailable",
            "The configured Docker context is unavailable",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
        )
    if not _LOCAL_NPIPE.fullmatch(endpoint):
        return _unavailable(
            "docker_context_refused",
            "Only an explicit local Windows named-pipe Docker context is allowed",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )

    try:
        info = run_probe(
            [docker, "--host", endpoint, "info", "--format", "{{.OSType}}"]
        )
    except SandboxError as exc:
        return _unavailable(
            exc.code,
            exc.message,
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    if info.returncode != 0 or info.timed_out:
        return _unavailable(
            "docker_daemon_unavailable",
            "Docker Desktop is installed but its Linux daemon is not running",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    if info.stdout.strip().lower() != "linux":
        return _unavailable(
            "docker_non_linux",
            "The configured Docker context must use Linux containers",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )

    try:
        inspected = run_probe(
        [
            docker,
            "--host",
            endpoint,
            "image",
            "inspect",
            config.image,
            "--format",
            "{{json .}}",
        ]
        )
    except SandboxError as exc:
        return _unavailable(
            exc.code,
            exc.message,
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    try:
        image_id, image_os, repo_digests, image_volumes = _parse_image_inspect(
            _process_json(inspected)
        )
    except Exception:
        return _unavailable(
            "docker_image_unavailable",
            "The pinned sandbox image is not available locally",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    if image_os.lower() != "linux" or not _IMAGE_ID.fullmatch(image_id):
        return _unavailable(
            "docker_image_refused",
            "The sandbox image must be a locally verified Linux image",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    if image_volumes:
        return _unavailable(
            "docker_image_volumes_refused",
            "Sandbox images cannot declare writable volumes",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )
    if config.image.lower().startswith("sha256:"):
        pinned = image_id.lower() == config.image.lower()
    else:
        pinned = config.image in repo_digests
    if not pinned:
        return _unavailable(
            "docker_image_refused",
            "The local image does not match the configured immutable digest",
            config=config,
            config_digest=config_digest,
            docker_executable=docker,
            context_endpoint=endpoint,
        )

    return SandboxCapability(
        available=True,
        code="ready",
        message="Docker task sandbox ready",
        tasks=tuple(sorted(config.tasks)),
        image=config.image,
        image_id=image_id,
        docker_executable=docker,
        context=config.context,
        context_endpoint=endpoint,
        config=config,
        config_digest=config_digest,
        process_adapter=adapter,
    )


def _is_reparse(st: os.stat_result) -> bool:
    attributes = int(getattr(st, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(st.st_mode) or bool(attributes & reparse_flag)


def _is_sensitive_rel(rel: str) -> bool:
    try:
        parts = [part.casefold() for part in PurePosixPath(rel).parts]
    except Exception:
        return True
    if not parts or any(part in ("", ".", "..") for part in parts):
        return True
    for part in parts[:-1]:
        if (
            part in _EXCLUDED_DIR_NAMES
            or part == ".model-hub"
            or part == ".env"
            or part.startswith(".env.")
            or part.startswith(".env-")
        ):
            return True
    name = parts[-1]
    if (
        name == ".env"
        or name.startswith(".env.")
        or name.startswith(".env-")
        or name == ".envrc"
        or name.startswith(".envrc.")
        or name in _EXCLUDED_DIR_NAMES
    ):
        return True
    if name in _EXCLUDED_FILE_NAMES:
        return True
    if (
        name.startswith("secret")
        or "credential" in name
        or "client_secret" in name
        or "service-account" in name
        or "service_account" in name
        or "api_key" in name
        or "api-key" in name
        or "api_token" in name
        or "api-token" in name
    ):
        return True
    if name.endswith((".tfstate", ".tfstate.backup")):
        return True
    return any(name.endswith(suffix) for suffix in _EXCLUDED_FILE_SUFFIXES)


def _snapshot_path_included(rel: str, patterns: tuple[str, ...]) -> bool:
    path_parts = tuple(PurePosixPath(rel).parts)

    def matches(pattern: str) -> bool:
        pattern_parts = tuple(pattern.split("/"))
        seen: set[tuple[int, int]] = set()

        def visit(path_index: int, pattern_index: int) -> bool:
            state = (path_index, pattern_index)
            if state in seen:
                return False
            seen.add(state)
            if pattern_index == len(pattern_parts):
                return path_index == len(path_parts)
            part = pattern_parts[pattern_index]
            if part == "**":
                return visit(path_index, pattern_index + 1) or (
                    path_index < len(path_parts)
                    and visit(path_index + 1, pattern_index)
                )
            return (
                path_index < len(path_parts)
                and fnmatch.fnmatchcase(path_parts[path_index], part)
                and visit(path_index + 1, pattern_index + 1)
            )

        return visit(0, 0)

    return any(matches(pattern) for pattern in patterns)


def _safe_staged_target(root: Path, rel: str) -> Path:
    try:
        normalized = validate_relative_project_path(rel)
    except ValueError as exc:
        raise SandboxError("invalid_staged_path", "A staged path is invalid") from exc
    pure = PurePosixPath(normalized)
    if len(pure.parts) > MAX_SNAPSHOT_DEPTH:
        raise SandboxError("invalid_staged_path", "A staged path is invalid")
    target = root.joinpath(*pure.parts)
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SandboxError("invalid_staged_path", "A staged path escapes the project") from exc
    return resolved


def _check_external_budget(
    deadline: float | None, cancel_event: threading.Event | None
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SandboxError("task_cancelled", "The sandbox task was cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise SandboxError("task_timeout", "The sandbox task timed out")


def _disk_hash(
    target: Path,
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> str | None:
    _check_external_budget(deadline, cancel_event)
    try:
        st = target.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "unsafe"
    if (
        _is_reparse(st)
        or not stat.S_ISREG(st.st_mode)
        or int(getattr(st, "st_nlink", 1)) > 1
    ):
        return "unsafe"
    if st.st_size > MAX_SNAPSHOT_FILE_BYTES:
        raise SandboxError(
            "snapshot_file_too_large",
            "A staged base file exceeds the sandbox limit",
        )
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            opened_st = os.fstat(handle.fileno())
            if (
                (opened_st.st_dev, opened_st.st_ino) != (st.st_dev, st.st_ino)
                or opened_st.st_size != st.st_size
            ):
                return "unsafe"
            while True:
                _check_external_budget(deadline, cancel_event)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            completed_st = os.fstat(handle.fileno())
            if (
                (completed_st.st_dev, completed_st.st_ino)
                != (st.st_dev, st.st_ino)
                or completed_st.st_size != st.st_size
                or completed_st.st_mtime_ns != st.st_mtime_ns
            ):
                return "unsafe"
    except OSError:
        return "unsafe"
    try:
        final_st = target.lstat()
    except OSError:
        return "unsafe"
    if (
        _is_reparse(final_st)
        or (final_st.st_dev, final_st.st_ino) != (st.st_dev, st.st_ino)
        or final_st.st_size != st.st_size
        or final_st.st_mtime_ns != st.st_mtime_ns
    ):
        return "unsafe"
    return digest.hexdigest()


@dataclass(frozen=True)
class _FrozenStagedRow:
    id: str
    path: str
    base_hash: str | None
    updated_at: float
    content: str
    content_hash: str


def _freeze_pending_rows(
    session_id: str,
    root: Path,
    snapshot_include: tuple[str, ...],
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[_FrozenStagedRow, ...]:
    def should_abort() -> bool:
        return bool(
            (cancel_event is not None and cancel_event.is_set())
            or (deadline is not None and time.monotonic() >= deadline)
        )

    _check_external_budget(deadline, cancel_event)
    try:
        rows, count, content_bytes = persistence.list_staged_changes_for_root_bounded(
            session_id,
            str(root),
            status="pending",
            max_rows=MAX_STAGED_ROWS,
            max_content_bytes=MAX_STAGED_OVERLAY_BYTES,
            should_abort=should_abort,
        )
    except InterruptedError:
        _check_external_budget(deadline, cancel_event)
        raise SandboxError("task_cancelled", "The sandbox task was cancelled")
    if count > MAX_STAGED_ROWS:
        raise SandboxError(
            "staged_overlay_too_many_files",
            "The staged overlay has too many files",
        )
    if content_bytes > MAX_STAGED_OVERLAY_BYTES:
        raise SandboxError(
            "staged_overlay_too_large",
            "The staged overlay exceeds the sandbox limit",
        )
    frozen: list[_FrozenStagedRow] = []
    for row in rows:
        _check_external_budget(deadline, cancel_event)
        rel = str(row["path"])
        target = _safe_staged_target(root, rel)
        if _is_sensitive_rel(rel):
            raise SandboxError(
                "sensitive_staged_path",
                "Sensitive staged paths cannot enter the task sandbox",
            )
        if not _snapshot_path_included(rel, snapshot_include):
            raise SandboxError(
                "staged_path_not_in_snapshot",
                "A staged path is outside the configured snapshot include policy",
            )
        if _disk_hash(
            target, deadline=deadline, cancel_event=cancel_event
        ) != row["base_hash"]:
            raise SandboxError(
                "staged_base_conflict",
                "A staged file changed on disk before task execution",
            )
        content = str(row["new_content"])
        encoded_content = content.encode("utf-8")
        if len(encoded_content) > MAX_SNAPSHOT_FILE_BYTES:
            raise SandboxError(
                "snapshot_file_too_large",
                "A staged project file exceeds the sandbox limit",
            )
        frozen.append(
            _FrozenStagedRow(
                id=str(row["id"]),
                path=rel,
                base_hash=row["base_hash"],
                updated_at=float(row["updated_at"]),
                content=content,
                content_hash=hashlib.sha256(encoded_content).hexdigest(),
            )
        )
    return tuple(frozen)


def _overlay_digest(rows: tuple[_FrozenStagedRow, ...]) -> str:
    encoded = json.dumps(
        [
            {
                "id": row.id,
                "path": row.path,
                "base_hash": row.base_hash,
                "updated_at": row.updated_at,
                "content_hash": row.content_hash,
            }
            for row in rows
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FrozenSandboxTask:
    permission_target: str
    approval_target: dict[str, Any]
    staged_overlay_digest: str
    staged_change_ids: tuple[str, ...]
    config_digest: str
    _broker: "DockerTaskBroker" = field(repr=False, compare=False)
    _task: SandboxTaskConfig = field(repr=False, compare=False)
    _rows: tuple[_FrozenStagedRow, ...] = field(repr=False, compare=False)

    def execute(self) -> str:
        return self._broker._execute(self)

    def execute_outcome(self) -> tuple[bool, str]:
        try:
            result = self.execute()
        except SandboxError as exc:
            return False, f"error: {exc.code}: {exc.message}"
        except Exception:
            # Tool results are persisted. Never serialize an unexpected host
            # exception (which can contain usernames or scratch paths) into
            # the session audit trail.
            return (
                False,
                "error: sandbox_internal_error: The local task sandbox failed safely",
            )
        ok = result.startswith("[exit 0]")
        return ok, result


class DockerTaskBroker:
    def __init__(
        self,
        root: str | Path,
        session_id: str,
        run_id: str,
        cancel_event: threading.Event,
        *,
        capability: SandboxCapability | None = None,
        process_adapter: Any | None = None,
        data_root: str | Path | None = None,
    ):
        self.root = Path(root).resolve()
        self.session_id = str(session_id)
        self.run_id = str(run_id)
        self.cancel_event = cancel_event
        self.process_adapter = (
            process_adapter
            or (capability.process_adapter if capability is not None else None)
            or DefaultProcessAdapter()
        )
        self.capability = capability or probe_project_sandbox(
            self.root, process_adapter=self.process_adapter
        )
        self.snapshot_include = tuple(
            self.capability.config.snapshot_include
            if self.capability.config is not None
            else ()
        )
        self.data_root = Path(data_root or CONFIG_DIR).resolve()
        self.owner = secrets.token_hex(16)

    def prepare_task(self, name: str) -> FrozenSandboxTask:
        capability = self.capability
        if not capability.available or capability.config is None:
            raise SandboxError(capability.code, capability.message)
        if not isinstance(name, str) or name not in capability.config.tasks:
            raise SandboxError("unknown_sandbox_task", "Unknown configured sandbox task")
        configured = _config_at_exact_root(self.root)
        if configured is None:
            raise SandboxError("sandbox_config_drift", "Sandbox configuration changed")
        config, config_digest = configured
        if config_digest != capability.config_digest or config != capability.config:
            raise SandboxError("sandbox_config_drift", "Sandbox configuration changed")

        rows = _freeze_pending_rows(
            self.session_id,
            self.root,
            tuple(config.snapshot_include),
            cancel_event=self.cancel_event,
        )
        digest = _overlay_digest(rows)
        task = config.tasks[name]
        approval_target = {
            "kind": "sandbox_task",
            "name": name,
            "argv": list(task.argv),
            "root": str(self.root),
            "image": str(capability.image),
            "image_id": str(capability.image_id),
            "timeout_seconds": task.timeout_seconds,
            "network": "none",
            "staged_overlay_digest": digest,
            "config_digest": config_digest,
            "staged_changes": [
                {
                    "id": row.id,
                    "path": row.path,
                    "base_hash": row.base_hash,
                    "updated_at": row.updated_at,
                    "content_hash": row.content_hash,
                }
                for row in rows
            ],
        }
        return FrozenSandboxTask(
            permission_target=name,
            approval_target=approval_target,
            staged_overlay_digest=digest,
            staged_change_ids=tuple(row.id for row in rows),
            config_digest=config_digest,
            _broker=self,
            _task=task.model_copy(deep=True),
            _rows=rows,
        )

    def _assert_frozen_state(
        self, frozen: FrozenSandboxTask, deadline: float | None = None
    ) -> None:
        configured = _config_at_exact_root(self.root)
        if configured is None or configured[1] != frozen.config_digest:
            raise SandboxError("sandbox_config_drift", "Sandbox configuration changed")
        rows = _freeze_pending_rows(
            self.session_id,
            self.root,
            tuple(configured[0].snapshot_include),
            deadline=deadline,
            cancel_event=self.cancel_event,
        )
        if rows != frozen._rows or _overlay_digest(rows) != frozen.staged_overlay_digest:
            raise SandboxError("staged_overlay_drift", "The staged overlay changed after approval")

    def _check_execution_budget(
        self, deadline: float, timeout_seconds: int | None = None
    ) -> None:
        if self.cancel_event.is_set():
            raise SandboxError("task_cancelled", "The sandbox task was cancelled")
        if time.monotonic() >= deadline:
            suffix = f" after {timeout_seconds}s" if timeout_seconds is not None else ""
            raise SandboxError(
                "task_timeout",
                f"The sandbox task timed out{suffix}",
            )

    def _remaining_execution_seconds(
        self, deadline: float, timeout_seconds: int
    ) -> float:
        self._check_execution_budget(deadline, timeout_seconds)
        return max(0.01, deadline - time.monotonic())

    def _execute(self, frozen: FrozenSandboxTask) -> str:
        if self.cancel_event.is_set():
            return "error: task cancelled before execution"
        deadline = time.monotonic() + frozen._task.timeout_seconds
        self._assert_frozen_state(frozen, deadline)
        self._check_execution_budget(deadline, frozen._task.timeout_seconds)
        refreshed = probe_project_sandbox(
            self.root,
            process_adapter=self.process_adapter,
            deadline=deadline,
            cancel_event=self.cancel_event,
        )
        self._check_execution_budget(deadline, frozen._task.timeout_seconds)
        if not refreshed.available:
            raise SandboxError(refreshed.code, refreshed.message)
        if (
            refreshed.image_id != self.capability.image_id
            or refreshed.config_digest != frozen.config_digest
            or refreshed.context_endpoint != self.capability.context_endpoint
            or refreshed.docker_executable != self.capability.docker_executable
        ):
            raise SandboxError("sandbox_capability_drift", "Sandbox capability changed")
        if not _SANDBOX_SLOTS.acquire(blocking=False):
            raise SandboxError("sandbox_busy", "The local task sandbox is busy")
        process_slot: _CrossProcessSlot | None = None
        try:
            process_slot = _acquire_cross_process_slot()
            if process_slot is None:
                raise SandboxError("sandbox_busy", "The local task sandbox is busy")
            if self._managed_container_count(
                refreshed, deadline, frozen._task.timeout_seconds
            ) >= MAX_CONCURRENT_TASKS:
                raise SandboxError("sandbox_busy", "The local task sandbox is busy")
            return self._execute_with_slot(frozen, refreshed, deadline)
        finally:
            if process_slot is not None:
                process_slot.release()
            _SANDBOX_SLOTS.release()

    def _execute_with_slot(
        self,
        frozen: FrozenSandboxTask,
        capability: SandboxCapability,
        deadline: float,
    ) -> str:
        sandboxes = self.data_root / "agent-sandboxes"
        try:
            sandboxes.mkdir(parents=True, exist_ok=True)
            scratch = Path(
                tempfile.mkdtemp(prefix=f"{self.run_id[:8]}-", dir=sandboxes)
            )
        except OSError as exc:
            raise SandboxError(
                "snapshot_unavailable",
                "A disposable task snapshot could not be created",
            ) from exc
        workspace = scratch / "workspace"
        archive_path = scratch / "snapshot.tar"
        container_id: str | None = None
        execution_id: str | None = None
        create_attempted = False
        try:
            counters = self._copy_project(workspace, deadline)
            self._apply_overlay(workspace, frozen._rows, counters, deadline)
            self._validate_materialized_workspace(workspace, deadline)
            self._assert_frozen_state(frozen, deadline)
            self._write_snapshot_archive(workspace, archive_path, deadline)
            self._assert_frozen_state(frozen, deadline)
            self._check_execution_budget(deadline, frozen._task.timeout_seconds)

            execution_id = uuid.uuid4().hex
            name = f"lac-task-{self.run_id[:8].lower()}-{execution_id[:8]}"
            create_argv = self._create_argv(
                capability,
                frozen._task,
                name,
                execution_id,
            )
            create_attempted = True
            created = self.process_adapter.run(
                create_argv,
                timeout_seconds=min(
                    DOCKER_CONTROL_TIMEOUT_SECONDS,
                    self._remaining_execution_seconds(
                        deadline, frozen._task.timeout_seconds
                    ),
                ),
                output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
                cancel_event=self.cancel_event,
            )
            candidate = created.stdout.strip().lower()
            if _CONTAINER_ID.fullmatch(candidate):
                container_id = candidate
                self._assert_owned(capability, container_id, execution_id)
            if created.cancelled or self.cancel_event.is_set():
                return "error: task cancelled before container start"
            if created.timed_out:
                if time.monotonic() >= deadline:
                    return f"error: task timed out after {frozen._task.timeout_seconds}s"
                raise SandboxError(
                    "docker_create_timeout",
                    "Docker timed out creating the task container",
                )
            if created.output_limited:
                raise SandboxError(
                    "docker_create_output_limited",
                    "Docker create output exceeded the control limit",
                )
            if created.returncode != 0 or container_id is None:
                raise SandboxError(
                    "docker_create_failed", "Docker could not create the task container"
                )

            result = self.process_adapter.run(
                self._docker_command(
                    capability,
                    "container",
                    "start",
                    "--attach",
                    "--interactive",
                    container_id,
                ),
                timeout_seconds=self._remaining_execution_seconds(
                    deadline, frozen._task.timeout_seconds
                ),
                output_limit_bytes=MAX_TASK_OUTPUT_BYTES,
                cancel_event=self.cancel_event,
                stdin_path=archive_path,
            )
            if result.cancelled or self.cancel_event.is_set():
                self._terminate_owned(capability, container_id, execution_id)
                return "error: task cancelled"
            if result.timed_out:
                self._terminate_owned(capability, container_id, execution_id)
                return f"error: task timed out after {frozen._task.timeout_seconds}s"
            if result.output_limited:
                self._terminate_owned(capability, container_id, execution_id)
                return f"error: task output exceeded {MAX_TASK_OUTPUT_BYTES} bytes"
            output = _redact_task_output(result.output, container_id, workspace)
            return f"[exit {result.returncode}]" + (f"\n{output}" if output else "")
        finally:
            cleanup_error: BaseException | None = None
            try:
                if execution_id is not None:
                    retained = container_id
                    if retained is None and create_attempted:
                        self._reconcile_ambiguous_create(capability, execution_id)
                    if retained is not None:
                        self._remove_owned(capability, retained, execution_id)
            except BaseException as exc:  # preserve cleanup failure over task output
                cleanup_error = exc
            finally:
                try:
                    shutil.rmtree(scratch)
                    if scratch.exists():
                        raise OSError("snapshot directory remains")
                except OSError:
                    if scratch.exists() and cleanup_error is None:
                        cleanup_error = SandboxError(
                            "snapshot_cleanup_failed",
                            "The disposable task snapshot could not be removed",
                        )
                try:
                    sandboxes.rmdir()
                except OSError:
                    pass
            if cleanup_error is not None:
                raise cleanup_error

    def _docker_command(
        self, capability: SandboxCapability, *args: str
    ) -> list[str]:
        endpoint = str(capability.context_endpoint or "")
        if not _LOCAL_NPIPE.fullmatch(endpoint):
            raise SandboxError(
                "docker_context_refused",
                "The pinned local Docker endpoint is unavailable",
            )
        return [
            str(capability.docker_executable),
            "--host",
            endpoint,
            *args,
        ]

    def _managed_container_count(
        self,
        capability: SandboxCapability,
        deadline: float,
        timeout_seconds: int,
    ) -> int:
        result = self.process_adapter.run(
            self._docker_command(
                capability,
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                "label=com.lac.managed=true",
                "--format",
                "{{.ID}}",
            ),
            timeout_seconds=min(
                DOCKER_CONTROL_TIMEOUT_SECONDS,
                self._remaining_execution_seconds(deadline, timeout_seconds),
            ),
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
            cancel_event=self.cancel_event,
        )
        self._check_execution_budget(deadline, timeout_seconds)
        if (
            result.returncode != 0
            or result.timed_out
            or result.cancelled
            or result.output_limited
        ):
            raise SandboxError(
                "docker_state_unavailable",
                "Docker task state could not be verified",
            )
        ids = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
        if any(not _CONTAINER_ID.fullmatch(value) for value in ids):
            raise SandboxError(
                "docker_state_unavailable",
                "Docker task state could not be verified",
            )
        return len(ids)

    def _create_argv(
        self,
        capability: SandboxCapability,
        task: SandboxTaskConfig,
        container_name: str,
        execution_id: str,
    ) -> list[str]:
        return self._docker_command(
            capability,
            "container",
            "create",
            "--rm",
            "--interactive",
            "--name",
            container_name,
            "--label",
            "com.lac.managed=true",
            "--label",
            f"com.lac.owner={self.owner}",
            "--label",
            f"com.lac.execution={execution_id}",
            "--pull=never",
            "--network=none",
            "--hostname=lac-sandbox",
            "--read-only",
            "--cap-drop=ALL",
            "--cap-add=KILL",
            "--cap-add=SETPCAP",
            "--cap-add=SETUID",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=1g",
            "--memory-swap=1g",
            "--cpus=2",
            "--user=0:65532",
            "--workdir=/workspace",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777",
            "--init",
            "--no-healthcheck",
            "--log-driver=none",
            "--tmpfs=/workspace:rw,nosuid,nodev,size=512m,mode=0770,uid=65532,gid=65532",
            "--entrypoint",
            "/bin/sh",
            str(capability.image),
            "-c",
            _SANDBOX_BOOTSTRAP,
            "lac-sandbox",
            f"{task.timeout_seconds}s",
            *task.argv,
        )

    def _inspect_owned(
        self, capability: SandboxCapability, container_id: str
    ) -> dict[str, Any]:
        result = self.process_adapter.run(
            self._docker_command(
                capability,
                "container",
                "inspect",
                container_id,
                "--format",
                _OWNERSHIP_FORMAT,
            ),
            timeout_seconds=DOCKER_CONTROL_TIMEOUT_SECONDS,
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
        )
        if (
            result.returncode != 0
            or result.timed_out
            or result.cancelled
            or result.output_limited
        ):
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        parts = result.stdout.strip().split("|")
        if len(parts) != 4:
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        return {
            "id": parts[0],
            "managed": parts[1],
            "owner": parts[2],
            "execution": parts[3],
        }

    def _assert_owned(
        self,
        capability: SandboxCapability,
        container_id: str,
        execution_id: str,
    ) -> None:
        value = self._inspect_owned(capability, container_id)
        if (
            value["id"].lower() != container_id.lower()
            or str(value["managed"]).lower() != "true"
            or value["owner"] != self.owner
            or value["execution"] != execution_id
        ):
            raise SandboxError(
                "docker_ownership_refused",
                "Task container ownership verification failed",
            )

    def _control(
        self,
        capability: SandboxCapability,
        command: str,
        container_id: str,
        *extra: str,
    ) -> ProcessResult:
        return self.process_adapter.run(
            self._docker_command(
                capability,
                "container",
                command,
                *extra,
                container_id,
            ),
            timeout_seconds=DOCKER_CONTROL_TIMEOUT_SECONDS,
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
        )

    def _container_is_confirmed_absent(
        self, capability: SandboxCapability, container_id: str
    ) -> bool:
        result = self.process_adapter.run(
            self._docker_command(
                capability,
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                f"id={container_id}",
                "--format",
                "{{.ID}}",
            ),
            timeout_seconds=DOCKER_CONTROL_TIMEOUT_SECONDS,
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
        )
        if (
            result.returncode != 0
            or result.timed_out
            or result.cancelled
            or result.output_limited
        ):
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        ids = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
        if any(not _CONTAINER_ID.fullmatch(value) for value in ids):
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        return container_id.lower() not in ids

    def _owned_container_ids(
        self, capability: SandboxCapability, execution_id: str
    ) -> tuple[str, ...]:
        result = self.process_adapter.run(
            self._docker_command(
                capability,
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                f"label=com.lac.owner={self.owner}",
                "--filter",
                f"label=com.lac.execution={execution_id}",
                "--format",
                "{{.ID}}",
            ),
            timeout_seconds=DOCKER_CONTROL_TIMEOUT_SECONDS,
            output_limit_bytes=MAX_DOCKER_CONTROL_OUTPUT_BYTES,
        )
        if (
            result.returncode != 0
            or result.timed_out
            or result.cancelled
            or result.output_limited
        ):
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        ids = tuple(
            line.strip().lower()
            for line in result.stdout.splitlines()
            if line.strip()
        )
        if len(ids) > 1 or any(not _CONTAINER_ID.fullmatch(value) for value in ids):
            raise SandboxError(
                "docker_ownership_unverified",
                "Could not verify ownership of the task container",
            )
        return ids

    def _find_owned_container_id(
        self, capability: SandboxCapability, execution_id: str
    ) -> str | None:
        ids = self._owned_container_ids(capability, execution_id)
        if not ids:
            return None
        self._assert_owned(capability, ids[0], execution_id)
        return ids[0]

    def _assert_no_owned_container(
        self, capability: SandboxCapability, execution_id: str
    ) -> None:
        if self._owned_container_ids(capability, execution_id):
            raise SandboxError(
                "docker_cleanup_failed",
                "The task container could not be removed",
            )

    def _reconcile_ambiguous_create(
        self, capability: SandboxCapability, execution_id: str
    ) -> None:
        """Bound a daemon/client create race and remove any late exact-owned container."""

        deadline = time.monotonic() + DOCKER_CREATE_RECONCILE_SECONDS
        while True:
            retained = self._find_owned_container_id(capability, execution_id)
            if retained is not None:
                self._remove_owned(capability, retained, execution_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._assert_no_owned_container(capability, execution_id)
                return
            time.sleep(min(DOCKER_CREATE_RECONCILE_POLL_SECONDS, remaining))

    def _remove_owned(
        self,
        capability: SandboxCapability,
        container_id: str,
        execution_id: str,
    ) -> None:
        if self._container_is_confirmed_absent(capability, container_id):
            return
        self._assert_owned(capability, container_id, execution_id)
        self._control(capability, "rm", container_id, "--force")
        if not self._container_is_confirmed_absent(capability, container_id):
            raise SandboxError(
                "docker_cleanup_failed",
                "The task container could not be removed",
            )

    def _terminate_owned(
        self,
        capability: SandboxCapability,
        container_id: str,
        execution_id: str,
    ) -> None:
        for command, extra in (
            ("stop", ("--time", "2")),
            ("kill", ()),
            ("rm", ("--force",)),
        ):
            if self._container_is_confirmed_absent(capability, container_id):
                return
            self._assert_owned(capability, container_id, execution_id)
            self._control(capability, command, container_id, *extra)
        if not self._container_is_confirmed_absent(capability, container_id):
            raise SandboxError(
                "docker_cleanup_failed",
                "The running task container could not be stopped and removed",
            )

    def _copy_project(
        self, workspace: Path, deadline: float
    ) -> dict[str, int]:
        self._check_execution_budget(deadline)
        try:
            root_st = self.root.lstat()
        except OSError as exc:
            raise SandboxError("snapshot_unavailable", "Project root is unavailable") from exc
        if _is_reparse(root_st) or not stat.S_ISDIR(root_st.st_mode):
            raise SandboxError("snapshot_refused", "Project root cannot be a reparse point")
        try:
            workspace.mkdir(parents=True, mode=0o777)
            os.chmod(workspace, 0o777)
        except OSError as exc:
            raise SandboxError(
                "snapshot_unavailable",
                "The disposable project snapshot could not be created",
            ) from exc
        counters = {"files": 0, "bytes": 0, "entries": 0, "dirs": 1}
        self._copy_directory(
            self.root,
            workspace,
            root_st.st_dev,
            counters,
            deadline,
            root_st,
            0,
        )
        self._check_execution_budget(deadline)
        return counters

    def _write_snapshot_archive(
        self,
        workspace: Path,
        archive_path: Path,
        deadline: float,
    ) -> None:
        """Build a metadata-sanitized regular-file tar without recursive add()."""

        broker = self

        class BoundedWriter:
            def __init__(self, raw):
                self.raw = raw
                self.total = 0

            def write(self, data: bytes) -> int:
                broker._check_execution_budget(deadline)
                self.total += len(data)
                if self.total > MAX_SNAPSHOT_ARCHIVE_BYTES:
                    raise SandboxError(
                        "snapshot_archive_too_large",
                        "The disposable project archive exceeds the sandbox limit",
                    )
                return self.raw.write(data)

            def flush(self) -> None:
                self.raw.flush()

        class BudgetedReader:
            def __init__(self, raw):
                self.raw = raw

            def read(self, size: int = -1) -> bytes:
                broker._check_execution_budget(deadline)
                return self.raw.read(size)

        entries = files = directories = total_bytes = 0
        try:
            with archive_path.open("xb") as raw_archive:
                bounded = BoundedWriter(raw_archive)
                with tarfile.open(
                    fileobj=bounded,
                    mode="w|",
                    format=tarfile.GNU_FORMAT,
                    dereference=False,
                ) as archive:
                    stack = [workspace]
                    while stack:
                        self._check_execution_budget(deadline)
                        directory = stack.pop()
                        with os.scandir(directory) as scanned:
                            children = sorted(scanned, key=lambda entry: entry.name)
                        child_directories: list[Path] = []
                        for entry in children:
                            self._check_execution_budget(deadline)
                            entries += 1
                            if entries > MAX_SNAPSHOT_ENTRIES:
                                raise SandboxError(
                                    "snapshot_too_many_entries",
                                    "The project snapshot has too many entries",
                                )
                            path = Path(entry.path)
                            rel = path.relative_to(workspace).as_posix()
                            if (
                                not rel
                                or len(rel) > MAX_SNAPSHOT_PATH_CHARS
                                or any(part in ("", ".", "..") for part in PurePosixPath(rel).parts)
                            ):
                                raise SandboxError(
                                    "snapshot_archive_failed",
                                    "The disposable project archive contains an invalid path",
                                )
                            st = path.lstat()
                            if _is_reparse(st):
                                raise SandboxError(
                                    "snapshot_archive_failed",
                                    "The disposable project archive changed unexpectedly",
                                )
                            info = tarfile.TarInfo(rel)
                            info.uid = 65532
                            info.gid = 65532
                            info.uname = ""
                            info.gname = ""
                            info.mtime = 0
                            info.mode = stat.S_IMODE(st.st_mode) & 0o777
                            info.pax_headers = {}
                            if stat.S_ISDIR(st.st_mode):
                                directories += 1
                                if directories > MAX_SNAPSHOT_DIRECTORIES:
                                    raise SandboxError(
                                        "snapshot_too_many_directories",
                                        "The project snapshot has too many directories",
                                    )
                                info.type = tarfile.DIRTYPE
                                info.size = 0
                                archive.addfile(info)
                                child_directories.append(path)
                                continue
                            if (
                                not stat.S_ISREG(st.st_mode)
                                or int(getattr(st, "st_nlink", 1)) != 1
                            ):
                                raise SandboxError(
                                    "snapshot_archive_failed",
                                    "The disposable project archive contains an unsafe entry",
                                )
                            files += 1
                            total_bytes += int(st.st_size)
                            if files > MAX_SNAPSHOT_FILES:
                                raise SandboxError(
                                    "snapshot_too_many_files",
                                    "The project snapshot has too many files",
                                )
                            if (
                                st.st_size > MAX_SNAPSHOT_FILE_BYTES
                                or total_bytes > MAX_SNAPSHOT_BYTES
                            ):
                                raise SandboxError(
                                    "snapshot_too_large",
                                    "The project snapshot exceeds the sandbox limit",
                                )
                            info.type = tarfile.REGTYPE
                            info.size = int(st.st_size)
                            with path.open("rb") as reader:
                                opened_st = os.fstat(reader.fileno())
                                if (
                                    _is_reparse(opened_st)
                                    or int(getattr(opened_st, "st_nlink", 1)) != 1
                                    or (opened_st.st_dev, opened_st.st_ino)
                                    != (st.st_dev, st.st_ino)
                                ):
                                    raise SandboxError(
                                        "snapshot_archive_failed",
                                        "The disposable project archive changed unexpectedly",
                                    )
                                archive.addfile(info, BudgetedReader(reader))
                                completed_st = os.fstat(reader.fileno())
                            final_st = path.lstat()
                            if (
                                _is_reparse(final_st)
                                or (completed_st.st_dev, completed_st.st_ino)
                                != (st.st_dev, st.st_ino)
                                or completed_st.st_size != st.st_size
                                or completed_st.st_mtime_ns != st.st_mtime_ns
                                or (final_st.st_dev, final_st.st_ino)
                                != (st.st_dev, st.st_ino)
                                or final_st.st_size != st.st_size
                                or final_st.st_mtime_ns != st.st_mtime_ns
                            ):
                                raise SandboxError(
                                    "snapshot_archive_failed",
                                    "The disposable project archive changed unexpectedly",
                                )
                        stack.extend(reversed(child_directories))
        except SandboxError:
            raise
        except (OSError, tarfile.TarError, ValueError) as exc:
            raise SandboxError(
                "snapshot_archive_failed",
                "The disposable project archive could not be created",
            ) from exc
        self._check_execution_budget(deadline)

    def _validate_materialized_workspace(
        self, workspace: Path, deadline: float
    ) -> None:
        self._check_execution_budget(deadline)
        try:
            root_st = workspace.lstat()
        except OSError as exc:
            raise SandboxError(
                "snapshot_validation_failed",
                "The disposable project snapshot could not be validated",
            ) from exc
        counters = {"files": 0, "bytes": 0, "entries": 0, "dirs": 1}
        stack = [(workspace, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > MAX_SNAPSHOT_DEPTH:
                raise SandboxError(
                    "snapshot_too_deep",
                    "The project snapshot exceeds the directory depth limit",
                )
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        self._check_execution_budget(deadline)
                        path = Path(entry.path)
                        counters["entries"] += 1
                        if counters["entries"] > MAX_SNAPSHOT_ENTRIES:
                            raise SandboxError(
                                "snapshot_too_many_entries",
                                "The project snapshot has too many entries",
                            )
                        try:
                            relative = path.relative_to(workspace).as_posix()
                        except ValueError as exc:
                            raise SandboxError(
                                "snapshot_validation_failed",
                                "The disposable project snapshot contains an unsafe entry",
                            ) from exc
                        if len(relative) > MAX_SNAPSHOT_PATH_CHARS:
                            raise SandboxError(
                                "snapshot_path_too_long",
                                "A project snapshot path exceeds the sandbox limit",
                            )
                        st = path.lstat()
                        if _is_reparse(st) or st.st_dev != root_st.st_dev:
                            raise SandboxError(
                                "snapshot_validation_failed",
                                "The disposable project snapshot contains an unsafe entry",
                            )
                        if stat.S_ISDIR(st.st_mode):
                            counters["dirs"] += 1
                            if counters["dirs"] > MAX_SNAPSHOT_DIRECTORIES:
                                raise SandboxError(
                                    "snapshot_too_many_directories",
                                    "The project snapshot has too many directories",
                                )
                            stack.append((path, depth + 1))
                            continue
                        if not stat.S_ISREG(st.st_mode):
                            raise SandboxError(
                                "snapshot_validation_failed",
                                "The disposable project snapshot contains an unsafe entry",
                            )
                        counters["files"] += 1
                        counters["bytes"] += int(st.st_size)
                        if counters["files"] > MAX_SNAPSHOT_FILES:
                            raise SandboxError(
                                "snapshot_too_many_files",
                                "The project snapshot has too many files",
                            )
                        if st.st_size > MAX_SNAPSHOT_FILE_BYTES:
                            raise SandboxError(
                                "snapshot_file_too_large",
                                "A project file exceeds the sandbox limit",
                            )
                        if counters["bytes"] > MAX_SNAPSHOT_BYTES:
                            raise SandboxError(
                                "snapshot_too_large",
                                "The project snapshot exceeds the sandbox limit",
                            )
            except SandboxError:
                raise
            except OSError as exc:
                raise SandboxError(
                    "snapshot_validation_failed",
                    "The disposable project snapshot could not be validated",
                ) from exc
        self._check_execution_budget(deadline)

    def _copy_directory(
        self,
        source: Path,
        destination: Path,
        root_device: int,
        counters: dict[str, int],
        deadline: float,
        expected_st: os.stat_result,
        depth: int,
    ) -> None:
        self._check_execution_budget(deadline)
        if depth > MAX_SNAPSHOT_DEPTH:
            raise SandboxError(
                "snapshot_too_deep",
                "The project snapshot exceeds the directory depth limit",
            )
        try:
            current_st = source.lstat()
        except OSError as exc:
            raise SandboxError("snapshot_read_failed", "A project directory could not be read") from exc
        if (
            _is_reparse(current_st)
            or not stat.S_ISDIR(current_st.st_mode)
            or current_st.st_dev != root_device
            or (current_st.st_dev, current_st.st_ino)
            != (expected_st.st_dev, expected_st.st_ino)
        ):
            raise SandboxError(
                "snapshot_changed", "A project directory changed during snapshot"
            )
        try:
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SandboxError(
                "snapshot_changed", "A project directory escaped during snapshot"
            ) from exc
        try:
            with os.scandir(source) as entries:
                for entry in entries:
                    self._check_execution_budget(deadline)
                    counters["entries"] += 1
                    if counters["entries"] > MAX_SNAPSHOT_ENTRIES:
                        raise SandboxError(
                            "snapshot_too_many_entries",
                            "The project snapshot has too many entries",
                        )
                    name_folded = entry.name.casefold()
                    src = Path(entry.path)
                    try:
                        # DirEntry.stat can report st_dev=0 for NTFS directories;
                        # pathlib lstat returns the real volume id and stays no-follow.
                        st = src.lstat()
                    except OSError as exc:
                        raise SandboxError(
                            "snapshot_changed",
                            "A project entry changed during snapshot",
                        ) from exc
                    if _is_reparse(st) or st.st_dev != root_device:
                        continue
                    try:
                        resolved = src.resolve(strict=True)
                        resolved.relative_to(self.root)
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise SandboxError(
                            "snapshot_changed",
                            "A project entry escaped during snapshot",
                        ) from exc
                    if resolved == self.data_root or self.data_root in resolved.parents:
                        continue
                    rel = src.relative_to(self.root).as_posix()
                    if len(rel) > MAX_SNAPSHOT_PATH_CHARS:
                        raise SandboxError(
                            "snapshot_path_too_long",
                            "A project snapshot path exceeds the sandbox limit",
                        )
                    if stat.S_ISDIR(st.st_mode):
                        if name_folded in _EXCLUDED_DIR_NAMES:
                            continue
                        counters["dirs"] += 1
                        if counters["dirs"] > MAX_SNAPSHOT_DIRECTORIES:
                            raise SandboxError(
                                "snapshot_too_many_directories",
                                "The project snapshot has too many directories",
                            )
                        dst_dir = destination / entry.name
                        dst_dir.mkdir(mode=0o777)
                        os.chmod(dst_dir, 0o777)
                        self._copy_directory(
                            src,
                            dst_dir,
                            root_device,
                            counters,
                            deadline,
                            st,
                            depth + 1,
                        )
                        try:
                            dst_dir.rmdir()
                        except OSError:
                            pass
                        continue
                    if (
                        not stat.S_ISREG(st.st_mode)
                        or _is_sensitive_rel(rel)
                        or not _snapshot_path_included(rel, self.snapshot_include)
                    ):
                        continue
                    if int(getattr(st, "st_nlink", 1)) > 1:
                        continue
                    if st.st_size > MAX_SNAPSHOT_FILE_BYTES:
                        raise SandboxError(
                            "snapshot_file_too_large",
                            "A project file exceeds the sandbox limit",
                        )
                    counters["files"] += 1
                    if counters["files"] > MAX_SNAPSHOT_FILES:
                        raise SandboxError(
                            "snapshot_too_many_files",
                            "The project snapshot has too many files",
                        )
                    target = destination / entry.name
                    copied = 0
                    try:
                        with src.open("rb") as reader, target.open("xb") as writer:
                            opened_st = os.fstat(reader.fileno())
                            if (
                                _is_reparse(opened_st)
                                or (opened_st.st_dev, opened_st.st_ino)
                                != (st.st_dev, st.st_ino)
                            ):
                                raise SandboxError(
                                    "snapshot_changed",
                                    "A project file changed during snapshot",
                                )
                            while True:
                                self._check_execution_budget(deadline)
                                chunk = reader.read(1024 * 1024)
                                if not chunk:
                                    break
                                copied += len(chunk)
                                counters["bytes"] += len(chunk)
                                if copied > MAX_SNAPSHOT_FILE_BYTES:
                                    raise SandboxError(
                                        "snapshot_file_too_large",
                                        "A project file exceeds the sandbox limit",
                                    )
                                if counters["bytes"] > MAX_SNAPSHOT_BYTES:
                                    raise SandboxError(
                                        "snapshot_too_large",
                                        "The project snapshot exceeds the sandbox limit",
                                    )
                                writer.write(chunk)
                            completed_st = os.fstat(reader.fileno())
                            if (
                                completed_st.st_size != st.st_size
                                or completed_st.st_mtime_ns != st.st_mtime_ns
                                or (completed_st.st_dev, completed_st.st_ino)
                                != (st.st_dev, st.st_ino)
                            ):
                                raise SandboxError(
                                    "snapshot_changed",
                                    "A project file changed during snapshot",
                                )
                        final_st = src.lstat()
                        if (
                            _is_reparse(final_st)
                            or final_st.st_size != st.st_size
                            or final_st.st_mtime_ns != st.st_mtime_ns
                            or (final_st.st_dev, final_st.st_ino)
                            != (st.st_dev, st.st_ino)
                        ):
                            raise SandboxError(
                                "snapshot_changed",
                                "A project file changed during snapshot",
                            )
                        os.chmod(target, (st.st_mode & 0o111) | 0o666)
                    except SandboxError:
                        raise
                    except OSError as exc:
                        raise SandboxError(
                            "snapshot_read_failed",
                            "A project file could not be copied",
                        ) from exc
        except SandboxError:
            raise
        except OSError as exc:
            raise SandboxError(
                "snapshot_read_failed", "A project directory could not be read"
            ) from exc
        try:
            final_directory_st = source.lstat()
        except OSError as exc:
            raise SandboxError(
                "snapshot_changed", "A project directory changed during snapshot"
            ) from exc
        if (
            _is_reparse(final_directory_st)
            or (final_directory_st.st_dev, final_directory_st.st_ino)
            != (current_st.st_dev, current_st.st_ino)
            or final_directory_st.st_mtime_ns != current_st.st_mtime_ns
        ):
            raise SandboxError(
                "snapshot_changed", "A project directory changed during snapshot"
            )

    def _apply_overlay(
        self,
        workspace: Path,
        rows: tuple[_FrozenStagedRow, ...],
        counters: dict[str, int],
        deadline: float,
    ) -> None:
        for row in rows:
            self._check_execution_budget(deadline)
            encoded = row.content.encode("utf-8")
            if len(encoded) > MAX_SNAPSHOT_FILE_BYTES:
                raise SandboxError(
                    "snapshot_file_too_large",
                    "A staged project file exceeds the sandbox limit",
                )
            target = _safe_staged_target(workspace, row.path)
            old_size = 0
            try:
                old_st = target.lstat()
                old_exists = True
            except FileNotFoundError:
                old_exists = False
                old_st = None
            except OSError as exc:
                raise SandboxError(
                    "snapshot_overlay_failed",
                    "A staged file target could not be inspected",
                ) from exc
            if old_st is not None:
                if _is_reparse(old_st) or not stat.S_ISREG(old_st.st_mode):
                    raise SandboxError(
                        "snapshot_overlay_failed",
                        "A staged file target is unsafe",
                    )
                old_size = int(old_st.st_size)
            next_files = counters["files"] + (0 if old_exists else 1)
            next_bytes = counters["bytes"] - old_size + len(encoded)
            if next_files > MAX_SNAPSHOT_FILES:
                raise SandboxError(
                    "snapshot_too_many_files",
                    "The project snapshot has too many files",
                )
            if next_bytes > MAX_SNAPSHOT_BYTES:
                raise SandboxError(
                    "snapshot_too_large",
                    "The project snapshot exceeds the sandbox limit",
                )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise SandboxError(
                    "snapshot_overlay_failed",
                    "A staged file parent could not be materialized",
                ) from exc
            for parent in [target.parent, *target.parent.parents]:
                if parent == workspace.parent:
                    break
                try:
                    os.chmod(parent, 0o777)
                except OSError:
                    pass
                if parent == workspace:
                    break
            self._check_execution_budget(deadline)
            try:
                with target.open("wb") as writer:
                    writer.write(encoded)
                os.chmod(target, 0o666)
            except OSError as exc:
                raise SandboxError(
                    "snapshot_overlay_failed",
                    "A staged file could not be materialized",
                ) from exc
            counters["files"] = next_files
            counters["bytes"] = next_bytes


__all__ = [
    "DefaultProcessAdapter",
    "DockerTaskBroker",
    "FrozenSandboxTask",
    "ProcessResult",
    "SandboxCapability",
    "SandboxError",
    "probe_project_sandbox",
]
