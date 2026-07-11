from __future__ import annotations

import json
import os
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agent.sandbox import (
    DefaultProcessAdapter,
    DockerTaskBroker,
    ProcessResult,
    SandboxError,
    probe_project_sandbox,
)
from backend.config import SandboxConfig, SandboxTaskConfig


IMAGE_DIGEST = "a" * 64
IMAGE_ID = "sha256:" + ("b" * 64)
IMAGE = f"example/lac-dev@sha256:{IMAGE_DIGEST}"
CONTAINER_ID = "c" * 64


def _write_project_config(
    root: Path,
    *,
    image: str = IMAGE,
    snapshot_include: list[str] | None = None,
) -> Path:
    apt = root / ".apt"
    apt.mkdir(parents=True, exist_ok=True)
    path = apt / "apt.jsonc"
    path.write_text(
        json.dumps(
            {
                "sandbox": {
                    "engine": "docker",
                    "context": "desktop-linux",
                    "image": image,
                    "snapshot_include": snapshot_include
                    or [
                        ".apt/apt.jsonc",
                        "*.py",
                        "src/**",
                        "staged.txt",
                        "file-*.txt",
                        "linked.txt",
                        "innocent.txt",
                        "big.txt",
                    ],
                    "tasks": {
                        "test": {
                            "argv": ["python", "-m", "pytest", "-q"],
                            "timeout_seconds": 180,
                        },
                        "lint": {"argv": ["ruff", "check", "."]},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


class ReadyAdapter:
    """Deterministic Docker adapter; never contacts the real daemon."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.created_labels: dict[str, str] = {}
        self.on_create = None
        self.on_start = None
        self.create_result = None
        self.start_result = ProcessResult(returncode=0, stdout="2 passed\n")
        self.container_exists = False

    def which(self, name: str) -> str | None:
        assert name == "docker"
        return r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

    def run(
        self,
        argv,
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        cancel_event=None,
        stdin_path=None,
    ) -> ProcessResult:
        args = [str(v) for v in argv]
        self.calls.append(args)
        if len(args) > 2 and args[1:3] == ["context", "inspect"]:
            return ProcessResult(
                returncode=0,
                stdout=json.dumps("npipe:////./pipe/dockerDesktopLinuxEngine") + "\n",
            )
        if "info" in args:
            return ProcessResult(returncode=0, stdout="linux\n")
        if "image" in args and "inspect" in args:
            return ProcessResult(
                returncode=0,
                stdout=json.dumps(
                    {"id": IMAGE_ID, "os": "linux", "repo_digests": [IMAGE]}
                )
                + "\n",
            )
        if "create" in args:
            for index, value in enumerate(args):
                if value == "--label":
                    key, val = args[index + 1].split("=", 1)
                    self.created_labels[key] = val
            if self.on_create is not None:
                self.on_create(args)
            self.container_exists = True
            return self.create_result or ProcessResult(
                returncode=0, stdout=CONTAINER_ID + "\n"
            )
        if "start" in args:
            if self.on_start is not None:
                self.on_start(args, stdin_path)
            return self.start_result
        if "container" in args and "inspect" in args:
            if not self.container_exists:
                return ProcessResult(returncode=1, stderr="No such container")
            return ProcessResult(
                returncode=0,
                stdout="|".join(
                    (
                        CONTAINER_ID,
                        str(self.created_labels.get("com.lac.managed") or ""),
                        str(self.created_labels.get("com.lac.owner") or ""),
                        str(self.created_labels.get("com.lac.execution") or ""),
                    )
                )
                + "\n",
            )
        if "container" in args and "ls" in args:
            return ProcessResult(
                returncode=0,
                stdout=(CONTAINER_ID + "\n") if self.container_exists else "",
            )
        if any(command in args for command in ("stop", "kill", "rm")):
            if "rm" in args:
                self.container_exists = False
            return ProcessResult(returncode=0)
        raise AssertionError(f"unexpected Docker call: {args}")


def _session() -> str:
    from backend.cookbook.persistence import create_session

    return create_session(name="sandbox", model="mock:1b", workspace="default")


def _ready(root: Path, adapter: ReadyAdapter):
    capability = probe_project_sandbox(root, process_adapter=adapter)
    assert capability.available is True, capability.to_dict()
    return capability


def _archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:") as archive:
        return {member.name.rstrip("/") for member in archive.getmembers()}


def _archive_text(path: Path, name: str) -> str:
    with tarfile.open(path, "r:") as archive:
        member = archive.getmember(name)
        handle = archive.extractfile(member)
        assert handle is not None
        return handle.read().decode("utf-8")


def test_sandbox_config_models_bound_names_argv_images_and_timeouts():
    task = SandboxTaskConfig(argv=["python", "-m", "pytest"])
    assert task.timeout_seconds == 120
    config = SandboxConfig(
        context="desktop-linux",
        image=IMAGE,
        snapshot_include=["src/**", "tests/**", "pyproject.toml"],
        tasks={"test:unit": task},
    )
    assert config.engine == "docker"

    invalid = [
        {"context": "desktop-linux", "image": "python:latest", "snapshot_include": ["src/**"], "tasks": {"test": {"argv": ["pytest"]}}},
        {"context": "desktop-linux", "image": IMAGE, "tasks": {"test": {"argv": ["pytest"]}}},
        {"context": "desktop-linux", "image": IMAGE, "snapshot_include": ["**"], "tasks": {"test": {"argv": ["pytest"]}}},
        {"context": "desktop-linux", "image": IMAGE, "snapshot_include": ["src/**"], "tasks": {"../test": {"argv": ["pytest"]}}},
        {"context": "desktop-linux", "image": IMAGE, "snapshot_include": ["src/**"], "tasks": {"test": {"argv": []}}},
        {"context": "desktop-linux", "image": IMAGE, "snapshot_include": ["src/**"], "tasks": {"test": {"argv": ["-c", "bad"]}}},
        {"context": "desktop-linux", "image": IMAGE, "snapshot_include": ["src/**"], "tasks": {"test": {"argv": ["pytest"], "timeout_seconds": 301}}},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            SandboxConfig.model_validate(value)


def test_schema_declares_closed_docker_sandbox_contract():
    schema_path = Path(__file__).resolve().parents[1] / "backend" / "schema" / "apt.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    sandbox = schema["properties"]["sandbox"]
    assert sandbox["additionalProperties"] is False
    assert sandbox["properties"]["engine"]["const"] == "docker"
    assert "snapshot_include" in sandbox["required"]
    assert sandbox["properties"]["snapshot_include"]["maxItems"] == 128
    assert sandbox["properties"]["tasks"]["additionalProperties"]["properties"]["timeout_seconds"]["maximum"] == 300


def test_probe_is_fail_closed_without_config_and_never_calls_docker(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    adapter = ReadyAdapter()

    capability = probe_project_sandbox(root, process_adapter=adapter)

    assert capability.available is False
    assert capability.code == "sandbox_unconfigured"
    assert capability.tasks == ()
    assert capability.network == "none"
    assert adapter.calls == []


def test_windows_docker_resolution_ignores_project_path_outside_cwd(
    tmp_path, monkeypatch
):
    if os.name != "nt":
        pytest.skip("Windows-only executable resolution")
    project = tmp_path / "untrusted-project"
    project.mkdir()
    (project / "docker.cmd").write_text("@echo compromised\n", encoding="utf-8")
    (project / "docker.exe").write_bytes(b"MZuntrusted")
    outside = tmp_path / "outside-project"
    outside.mkdir()
    missing = tmp_path / "trusted-program-files"
    monkeypatch.chdir(outside)
    monkeypatch.setenv("PATH", str(project))
    import backend.agent.sandbox as sandbox_mod

    monkeypatch.setattr(
        sandbox_mod, "_trusted_windows_program_files_roots", lambda: (missing,)
    )

    resolved = DefaultProcessAdapter().which("docker")

    assert resolved is None


def test_default_process_adapter_uses_executable_directory_as_cwd(
    tmp_path, monkeypatch
):
    untrusted = tmp_path / "untrusted-project"
    untrusted.mkdir()
    monkeypatch.chdir(untrusted)

    result = DefaultProcessAdapter().run(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        timeout_seconds=5,
        output_limit_bytes=1024,
    )

    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == Path(sys.executable).resolve().parent


def test_probe_accepts_only_local_npipe_linux_and_preexisting_pinned_image(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()

    capability = probe_project_sandbox(root, process_adapter=adapter)

    assert capability.to_dict() == {
        "backend": "docker",
        "available": True,
        "code": "ready",
        "message": "Docker task sandbox ready",
        "tasks": ["lint", "test"],
        "image": IMAGE,
        "network": "none",
    }
    flattened = [item.lower() for call in adapter.calls for item in call]
    assert not {"pull", "build", "run", "start"}.intersection(flattened)
    endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine"
    for call in adapter.calls[1:]:
        assert call[1:3] == ["--host", endpoint]


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_probe_refuses_linked_project_config_without_reading_target(
    tmp_path, link_kind
):
    root = tmp_path / "project"
    apt = root / ".apt"
    apt.mkdir(parents=True)
    outside = tmp_path / "outside.jsonc"
    outside_root = tmp_path / "outside-project"
    outside_root.mkdir()
    source = _write_project_config(outside_root)
    outside.write_bytes(source.read_bytes())
    target = apt / "apt.jsonc"
    try:
        if link_kind == "symlink":
            os.symlink(outside, target)
        else:
            os.link(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip(f"{link_kind} creation unavailable")
    adapter = ReadyAdapter()

    capability = probe_project_sandbox(root, process_adapter=adapter)

    assert capability.available is False
    assert capability.code == "sandbox_unconfigured"
    assert adapter.calls == []


def test_probe_refuses_linked_apt_directory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside_root = tmp_path / "outside-project"
    outside_root.mkdir()
    _write_project_config(outside_root)
    try:
        os.symlink(outside_root / ".apt", root / ".apt", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation unavailable")
    adapter = ReadyAdapter()

    capability = probe_project_sandbox(root, process_adapter=adapter)

    assert capability.available is False
    assert capability.code == "sandbox_unconfigured"
    assert adapter.calls == []


def test_probe_refuses_remote_context_before_daemon_or_image_calls(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)

    class RemoteAdapter(ReadyAdapter):
        def run(self, argv, **kwargs):
            args = [str(v) for v in argv]
            self.calls.append(args)
            assert "context" in args and "inspect" in args
            return ProcessResult(returncode=0, stdout='"tcp://192.0.2.1:2375"\n')

    adapter = RemoteAdapter()
    capability = probe_project_sandbox(root, process_adapter=adapter)

    assert capability.available is False
    assert capability.code == "docker_context_refused"
    assert len(adapter.calls) == 1


def test_probe_refuses_remote_named_pipe_and_image_declared_volumes(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)

    class RemotePipeAdapter(ReadyAdapter):
        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            self.calls.append(args)
            assert "context" in args and "inspect" in args
            return ProcessResult(
                returncode=0,
                stdout='"npipe:////remote-host/pipe/docker_engine"\n',
            )

    remote = probe_project_sandbox(root, process_adapter=RemotePipeAdapter())
    assert remote.available is False
    assert remote.code == "docker_context_refused"

    class VolumeImageAdapter(ReadyAdapter):
        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            if "image" in args and "inspect" in args:
                self.calls.append(args)
                return ProcessResult(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "Id": IMAGE_ID,
                            "Os": "linux",
                            "RepoDigests": [IMAGE],
                            "Config": {"Volumes": {"/data": {}}},
                        }
                    )
                    + "\n",
                )
            return super().run(argv, **kwargs)

    volume = probe_project_sandbox(root, process_adapter=VolumeImageAdapter())
    assert volume.available is False
    assert volume.code == "docker_image_volumes_refused"


def test_prepare_freezes_task_and_exact_pending_overlay(
    isolated_home, tmp_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    config_path = _write_project_config(root)
    source = root / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    session_id = _session()
    row = persistence.stage_change(
        session_id, "earlier-run", str(root), "src/app.py", "new\n"
    )
    adapter = ReadyAdapter()
    capability = _ready(root, adapter)
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=capability,
        data_root=Path(isolated_home) / ".model-hub",
    )

    frozen = broker.prepare_task("test")

    assert frozen.permission_target == "test"
    assert frozen.approval_target == {
        "kind": "sandbox_task",
        "name": "test",
        "argv": ["python", "-m", "pytest", "-q"],
        "root": str(root.resolve()),
        "image": IMAGE,
        "image_id": IMAGE_ID,
        "timeout_seconds": 180,
        "network": "none",
        "staged_overlay_digest": frozen.staged_overlay_digest,
        "config_digest": frozen.config_digest,
        "staged_changes": [
            {
                "id": row["id"],
                "path": "src/app.py",
                "base_hash": row["base_hash"],
                "updated_at": row["updated_at"],
                "content_hash": __import__("hashlib").sha256(b"new\n").hexdigest(),
            }
        ],
    }
    assert len(frozen.staged_overlay_digest) == 64
    assert frozen.staged_change_ids == (row["id"],)
    assert frozen.config_digest == __import__("hashlib").sha256(config_path.read_bytes()).hexdigest()


def test_prepare_refuses_sensitive_staged_path(isolated_home, tmp_path):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    persistence.stage_change(session_id, "run", str(root), ".env", "SECRET=x\n")
    adapter = ReadyAdapter()
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    )

    with pytest.raises(SandboxError) as exc:
        broker.prepare_task("test")
    assert exc.value.code == "sensitive_staged_path"


def test_prepare_refuses_staged_vcs_metadata(isolated_home, tmp_path):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    persistence.stage_change(session_id, "run", str(root), ".git/config", "unsafe\n")
    adapter = ReadyAdapter()
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    )

    with pytest.raises(SandboxError) as exc:
        broker.prepare_task("test")
    assert exc.value.code == "sensitive_staged_path"


@pytest.mark.parametrize("tampered_path", ["src/app.py:payload", "NUL", "src/trailing."])
def test_prepare_refuses_tampered_nonportable_staged_path(
    isolated_home, tmp_path, tampered_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    row = persistence.stage_change(
        session_id, "run", str(root), "src/app.py", "staged\n"
    )
    conn = persistence._ensure_db()
    conn.execute(
        "UPDATE staged_changes SET path = ? WHERE id = ?",
        (tampered_path, row["id"]),
    )
    conn.commit()
    conn.close()
    adapter = ReadyAdapter()
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    )

    with pytest.raises(SandboxError) as exc:
        broker.prepare_task("test")

    assert exc.value.code == "invalid_staged_path"


def test_snapshot_manifest_omits_unlisted_files_and_refuses_unlisted_overlay(
    isolated_home, tmp_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root, snapshot_include=["src/**"])
    (root / "src").mkdir()
    (root / "src" / "included.py").write_text("ok\n", encoding="utf-8")
    (root / "unlisted.txt").write_text("must stay out\n", encoding="utf-8")
    adapter = ReadyAdapter()
    session_id = _session()

    def inspect_start(args: list[str], stdin_path: Path):
        assert "--interactive" in args
        names = _archive_names(stdin_path)
        assert "src/included.py" in names
        assert "unlisted.txt" not in names

    adapter.on_start = inspect_start
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    )
    assert broker.prepare_task("test").execute().startswith("[exit 0]")

    persistence.stage_change(
        session_id, "run-2", str(root), "unlisted.txt", "staged\n"
    )
    with pytest.raises(SandboxError) as exc:
        broker.prepare_task("test")
    assert exc.value.code == "staged_path_not_in_snapshot"


def test_snapshot_globs_are_segment_aware():
    import backend.agent.sandbox as sandbox_mod

    assert sandbox_mod._snapshot_path_included("src/app.py", ("src/*.py",))
    assert not sandbox_mod._snapshot_path_included(
        "src/private/note.py", ("src/*.py",)
    )
    assert sandbox_mod._snapshot_path_included(
        "src/private/note.py", ("src/**",)
    )
    assert sandbox_mod._snapshot_path_included(
        "src/nested/test_app.py", ("src/**/test_*.py",)
    )


def test_execute_uses_disposable_overlay_and_fixed_container_posture(
    isolated_home, tmp_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("old\n", encoding="utf-8")
    (root / ".env.local").write_text("DO_NOT_COPY=x\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret-ish\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("large cache\n", encoding="utf-8")
    session_id = _session()
    persistence.stage_change(session_id, "run", str(root), "src/app.py", "new\n")
    adapter = ReadyAdapter()
    data_root = Path(isolated_home) / ".model-hub"

    def inspect_start(args: list[str], stdin_path: Path):
        assert "--interactive" in args
        names = _archive_names(stdin_path)
        assert _archive_text(stdin_path, "src/app.py") == "new\n"
        assert ".env.local" not in names
        assert ".git" not in names
        assert "node_modules" not in names

    adapter.on_start = inspect_start
    capability = _ready(root, adapter)
    frozen = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=capability,
        data_root=data_root,
    ).prepare_task("test")

    result = frozen.execute()

    assert result == "[exit 0]\n2 passed"
    create = next(call for call in adapter.calls if "create" in call)
    required = {
        "--pull=never",
        "--interactive",
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
        "--cpus=2",
        "--user=0:65532",
        "--workdir=/workspace",
        "--tmpfs=/workspace:rw,nosuid,nodev,size=512m,mode=0770,uid=65532,gid=65532",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777",
        "--init",
        "--log-driver=none",
    }
    assert required.issubset(set(create))
    assert "--mount" not in create
    entrypoint = create.index("--entrypoint")
    assert create[entrypoint + 1] == "/bin/sh"
    image_index = create.index(IMAGE)
    assert create[image_index + 1 : image_index + 4] == [
        "-c",
        'set -eu; limit="$1"; shift; exec /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/workspace TMPDIR=/tmp LANG=C.UTF-8 LC_ALL=C.UTF-8 /usr/bin/timeout --signal=TERM --kill-after=2s "$limit" /usr/bin/setpriv --reuid=65532 --bounding-set=-all --inh-caps=-all --ambient-caps=-all --securebits=+noroot,+noroot_locked --no-new-privs --pdeathsig=KILL -- /bin/sh -c \'set -eu; /usr/bin/tar --restrict --extract --file=- --directory=/workspace --no-same-owner --no-same-permissions --keep-old-files; cd /workspace; exec "$@"\' lac-task "$@"',
        "lac-sandbox",
    ]
    assert create[image_index + 4 :] == [
        "180s",
        "python",
        "-m",
        "pytest",
        "-q",
    ]
    assert create[1:3] == [
        "--host",
        "npipe:////./pipe/dockerDesktopLinuxEngine",
    ]
    ownership_inspect = next(
        call
        for call in adapter.calls
        if "container" in call and "inspect" in call
    )
    ownership_format = ownership_inspect[ownership_inspect.index("--format") + 1]
    assert "{{json .}}" not in ownership_format
    assert "com.lac.owner" in ownership_format
    assert not (data_root / "agent-sandboxes").exists() or not any(
        (data_root / "agent-sandboxes").iterdir()
    )


def test_execute_fails_closed_on_config_or_overlay_drift_before_create(
    isolated_home, tmp_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    config_path = _write_project_config(root)
    source = root / "app.py"
    source.write_text("old\n", encoding="utf-8")
    session_id = _session()
    persistence.stage_change(session_id, "run", str(root), "app.py", "new\n")
    adapter = ReadyAdapter()
    capability = _ready(root, adapter)
    broker = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=capability,
        data_root=Path(isolated_home) / ".model-hub",
    )
    frozen = broker.prepare_task("test")
    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "sandbox_config_drift"
    assert not any("create" in call for call in adapter.calls)

    # A separate prepared execution refuses source drift too.
    config_path.write_text(config_path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
    capability = _ready(root, adapter)
    frozen = DockerTaskBroker(
        root,
        session_id,
        "run-2",
        threading.Event(),
        capability=capability,
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    source.write_text("hand edit\n", encoding="utf-8")
    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "staged_base_conflict"


def test_cancelled_task_stops_kills_and_removes_only_owned_exact_id(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    adapter = ReadyAdapter()
    adapter.start_result = ProcessResult(
        returncode=130, stdout="partial\n", cancelled=True
    )
    frozen = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    result = frozen.execute()

    assert result.startswith("error: task cancelled")
    lifecycle = [
        next((name for name in ("stop", "kill", "rm") if name in call), None)
        for call in adapter.calls
    ]
    lifecycle = [name for name in lifecycle if name]
    assert lifecycle == ["stop", "kill", "rm"]
    for call in adapter.calls:
        if any(name in call for name in ("stop", "kill", "rm")):
            assert CONTAINER_ID in call


def test_create_warning_keeps_stdout_id_and_cleans_retained_container(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    adapter.create_result = ProcessResult(
        returncode=0,
        stdout=CONTAINER_ID + "\n",
        stderr="warning from Docker\n",
    )
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    assert frozen.execute() == "[exit 0]\n2 passed"
    assert any("rm" in call and CONTAINER_ID in call for call in adapter.calls)


@pytest.mark.parametrize(
    ("create_result", "expected"),
    [
        (
            ProcessResult(
                returncode=130,
                stdout=CONTAINER_ID + "\n",
                cancelled=True,
            ),
            "error: task cancelled before container start",
        ),
        (
            ProcessResult(
                returncode=-1,
                stdout=CONTAINER_ID + "\n",
                timed_out=True,
            ),
            "docker_create_timeout",
        ),
    ],
)
def test_bounded_create_failure_retains_id_for_owned_cleanup(
    isolated_home, tmp_path, create_result, expected
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    adapter.create_result = create_result
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    if create_result.cancelled:
        assert frozen.execute() == expected
    else:
        with pytest.raises(SandboxError) as exc:
            frozen.execute()
        assert exc.value.code == expected
    assert any("rm" in call and CONTAINER_ID in call for call in adapter.calls)


def test_interrupted_create_reconciles_late_exact_owned_container(
    isolated_home, tmp_path, monkeypatch
):
    import backend.agent.sandbox as sandbox_mod

    class DelayedCreateAdapter(ReadyAdapter):
        def __init__(self):
            super().__init__()
            self.owner_queries = 0

        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            if "create" in args:
                result = super().run(argv, **kwargs)
                self.container_exists = False
                return ProcessResult(returncode=-1, timed_out=True)
            if (
                "container" in args
                and "ls" in args
                and any(value.startswith("label=com.lac.owner=") for value in args)
            ):
                self.owner_queries += 1
                if self.owner_queries == 3:
                    self.container_exists = True
            return super().run(argv, **kwargs)

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = DelayedCreateAdapter()
    monkeypatch.setattr(sandbox_mod, "DOCKER_CREATE_RECONCILE_SECONDS", 0.1)
    monkeypatch.setattr(sandbox_mod, "DOCKER_CREATE_RECONCILE_POLL_SECONDS", 0.001)
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    with pytest.raises(SandboxError) as exc:
        frozen.execute()

    assert exc.value.code == "docker_create_timeout"
    assert adapter.owner_queries >= 3
    assert any("rm" in call and CONTAINER_ID in call for call in adapter.calls)
    assert adapter.container_exists is False


def test_start_client_failure_still_removes_owned_container(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    adapter.start_result = ProcessResult(returncode=125, stderr="start failed")
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    assert frozen.execute() == "[exit 125]\nstart failed"
    assert any("rm" in call and CONTAINER_ID in call for call in adapter.calls)


def test_task_output_redacts_container_identity_and_host_snapshot_path(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    leaked_paths: list[str] = []

    def leak_runtime_details(args: list[str], stdin_path: Path):
        leaked_paths.extend((str(stdin_path), stdin_path.as_posix()))
        adapter.start_result = ProcessResult(
            returncode=125,
            stderr=(
                f"daemon {CONTAINER_ID} {CONTAINER_ID[:12]} "
                f"{leaked_paths[0]} {leaked_paths[1]}"
            ),
        )

    adapter.on_start = leak_runtime_details
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    result = frozen.execute()

    assert CONTAINER_ID not in result
    assert CONTAINER_ID[:12] not in result
    assert all(path not in result for path in leaked_paths)
    assert "<container>" in result
    assert "<snapshot>" in result


def test_cleanup_failure_is_not_reported_as_successful_cancellation(
    isolated_home, tmp_path
):
    class StuckContainerAdapter(ReadyAdapter):
        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            if any(command in args for command in ("stop", "kill", "rm")):
                self.calls.append(args)
                return ProcessResult(returncode=1, stderr="daemon refused")
            if "ls" in args and "container" in args:
                self.calls.append(args)
                return ProcessResult(returncode=0, stdout=CONTAINER_ID + "\n")
            return super().run(argv, **kwargs)

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = StuckContainerAdapter()
    adapter.start_result = ProcessResult(returncode=130, cancelled=True)
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "docker_cleanup_failed"


def test_output_limited_task_accepts_only_confirmed_auto_removed_container(
    isolated_home, tmp_path
):
    class AutoRemovedAdapter(ReadyAdapter):
        def __init__(self):
            super().__init__()
            self.started = False

        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            if "start" in args:
                self.calls.append(args)
                self.started = True
                return ProcessResult(
                    returncode=0,
                    stdout="x" * 1024,
                    output_limited=True,
                )
            if self.started and "inspect" in args and "container" in args:
                self.calls.append(args)
                return ProcessResult(returncode=1, stderr="No such container")
            if self.started and "ls" in args and "container" in args:
                self.calls.append(args)
                return ProcessResult(returncode=0, stdout="")
            return super().run(argv, **kwargs)

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = AutoRemovedAdapter()
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    assert frozen.execute() == "error: task output exceeded 65536 bytes"
    assert any("ls" in call for call in adapter.calls)


def test_snapshot_skips_symlink_without_reading_target(isolated_home, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = root / "linked.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")

    session_id = _session()
    adapter = ReadyAdapter()

    def inspect_start(args: list[str], stdin_path: Path):
        assert "linked.txt" not in _archive_names(stdin_path)

    adapter.on_start = inspect_start
    frozen = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    assert frozen.execute().startswith("[exit 0]")


def test_snapshot_excludes_secret_paths_worktree_metadata_and_hardlinks(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(
        root,
        snapshot_include=[
            ".apt/apt.jsonc",
            ".envrc*",
            ".git",
            ".aws/**",
            ".kube/**",
            ".azure/**",
            "secrets.*",
            "innocent.txt",
        ],
    )
    (root / ".envrc").write_text("export SECRET=x\n", encoding="utf-8")
    (root / ".git").write_text("gitdir: ../outside.git\n", encoding="utf-8")
    for directory, name in (
        (".aws", "credentials"),
        (".kube", "config"),
        (".azure", "accessTokens.json"),
    ):
        target = root / directory
        target.mkdir()
        (target / name).write_text("secret\n", encoding="utf-8")
    (root / "secrets.json").write_text('{"token":"x"}\n', encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    linked = root / "innocent.txt"
    try:
        os.link(outside, linked)
    except OSError:
        linked = None

    adapter = ReadyAdapter()

    def inspect_start(args: list[str], stdin_path: Path):
        names = _archive_names(stdin_path)
        for relative in (
            ".apt/apt.jsonc",
            ".envrc",
            ".git",
            ".aws/credentials",
            ".kube/config",
            ".azure/accessTokens.json",
            "secrets.json",
        ):
            assert relative not in names
        if linked is not None:
            assert linked.name not in names

    adapter.on_start = inspect_start
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    assert frozen.execute().startswith("[exit 0]")


def test_snapshot_copy_honors_deadline_and_mid_walk_cancellation(
    isolated_home, tmp_path, monkeypatch
):
    import backend.agent.sandbox as sandbox_mod

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    for index in range(4):
        (root / f"file-{index}.txt").write_text("content\n", encoding="utf-8")
    event = threading.Event()
    adapter = ReadyAdapter()
    broker = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        event,
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    )

    with pytest.raises(SandboxError) as exc:
        broker._copy_project(tmp_path / "expired", time.monotonic() - 1)
    assert exc.value.code == "task_timeout"

    real_scandir = sandbox_mod.os.scandir

    class CancellingIterator:
        def __init__(self, path):
            self.inner = real_scandir(path)
            self.seen = 0

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            value = next(self.inner)
            self.seen += 1
            if self.seen == 1:
                event.set()
            return value

    monkeypatch.setattr(sandbox_mod.os, "scandir", CancellingIterator)
    with pytest.raises(SandboxError) as exc:
        broker._copy_project(tmp_path / "cancelled", time.monotonic() + 30)
    assert exc.value.code == "task_cancelled"


@pytest.mark.parametrize(
    ("fail_on_exist_ok", "expected_code"),
    [
        (False, "snapshot_unavailable"),
        (True, "snapshot_overlay_failed"),
    ],
)
def test_snapshot_materialization_os_errors_are_sanitized(
    isolated_home,
    tmp_path,
    monkeypatch,
    fail_on_exist_ok,
    expected_code,
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    persistence.stage_change(
        session_id,
        "run",
        str(root),
        "staged.txt",
        "staged content\n",
    )
    adapter = ReadyAdapter()
    frozen = DockerTaskBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    original_mkdir = Path.mkdir

    def leaking_mkdir(path, *args, **kwargs):
        if (
            path.name == "workspace"
            and bool(kwargs.get("exist_ok", False)) is fail_on_exist_ok
        ):
            raise OSError(r"C:\Users\secret-owner\private-scratch")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", leaking_mkdir)

    ok, result = frozen.execute_outcome()

    assert ok is False
    assert result.startswith(f"error: {expected_code}:")
    assert "secret-owner" not in result


def test_oversized_staged_overlay_is_rejected_before_materialization(
    isolated_home, tmp_path, monkeypatch
):
    import backend.agent.sandbox as sandbox_mod
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    session_id = _session()
    persistence.stage_change(session_id, "run", str(root), "big.txt", "x" * 32)
    adapter = ReadyAdapter()
    writes: list[Path] = []
    original_write_text = Path.write_text

    class TightLimitBroker(DockerTaskBroker):
        def _copy_project(self, workspace, deadline):
            counters = super()._copy_project(workspace, deadline)
            monkeypatch.setattr(sandbox_mod, "MAX_SNAPSHOT_FILE_BYTES", 8)
            return counters

    def tracked_write_text(path, *args, **kwargs):
        if path.name == "big.txt":
            writes.append(path)
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracked_write_text)
    frozen = TightLimitBroker(
        root,
        session_id,
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")

    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "snapshot_file_too_large"
    assert writes == []


def test_default_process_adapter_enforces_one_combined_output_bound():
    result = DefaultProcessAdapter().run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x'*8192); sys.stderr.write('y'*8192)",
        ],
        timeout_seconds=5,
        output_limit_bytes=4096,
    )

    assert result.output_limited is True
    assert len(result.output.encode("utf-8")) <= 4096


def test_default_process_adapter_streams_binary_stdin_from_bounded_file(tmp_path):
    payload = tmp_path / "snapshot.tar"
    payload.write_bytes(b"snapshot-bytes")

    result = DefaultProcessAdapter().run(
        [
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.buffer.read(); print(len(data))",
        ],
        timeout_seconds=5,
        output_limit_bytes=4096,
        stdin_path=payload,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == str(len(b"snapshot-bytes"))


def test_busy_sandbox_fails_before_snapshot_or_container(
    isolated_home, tmp_path, monkeypatch
):
    import backend.agent.sandbox as sandbox_mod

    class NoSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

        def release(self):  # pragma: no cover - must not release unacquired slot
            raise AssertionError("unacquired slot released")

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    monkeypatch.setattr(sandbox_mod, "_SANDBOX_SLOTS", NoSlots())

    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "sandbox_busy"
    assert not any("create" in call for call in adapter.calls)


def test_cross_process_slot_and_existing_managed_tasks_enforce_global_cap(
    isolated_home, tmp_path, monkeypatch
):
    import backend.agent.sandbox as sandbox_mod

    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    adapter = ReadyAdapter()
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    original_acquire = sandbox_mod._acquire_cross_process_slot
    monkeypatch.setattr(sandbox_mod, "_acquire_cross_process_slot", lambda: None)
    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "sandbox_busy"
    assert not any("create" in call for call in adapter.calls)

    class AtCapacityAdapter(ReadyAdapter):
        def run(self, argv, **kwargs):
            args = [str(value) for value in argv]
            if (
                "container" in args
                and "ls" in args
                and "--all" in args
                and "label=com.lac.managed=true" in args
            ):
                self.calls.append(args)
                return ProcessResult(
                    returncode=0,
                    stdout=CONTAINER_ID + "\n" + ("d" * 64) + "\n",
                )
            return super().run(argv, **kwargs)

    monkeypatch.setattr(
        sandbox_mod, "_acquire_cross_process_slot", original_acquire
    )
    adapter = AtCapacityAdapter()
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-2",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=Path(isolated_home) / ".model-hub",
    ).prepare_task("test")
    with pytest.raises(SandboxError) as exc:
        frozen.execute()
    assert exc.value.code == "sandbox_busy"
    assert not any("create" in call for call in adapter.calls)


def test_snapshot_never_recursively_copies_lac_data_root(
    isolated_home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    _write_project_config(root)
    data_root = root / ".model-hub"
    data_root.mkdir()
    (data_root / "private.db").write_text("must not enter snapshot", encoding="utf-8")
    adapter = ReadyAdapter()

    def inspect_start(args: list[str], stdin_path: Path):
        assert not any(
            name == ".model-hub" or name.startswith(".model-hub/")
            for name in _archive_names(stdin_path)
        )

    adapter.on_start = inspect_start
    frozen = DockerTaskBroker(
        root,
        _session(),
        "run-1",
        threading.Event(),
        capability=_ready(root, adapter),
        data_root=data_root,
    ).prepare_task("test")
    assert frozen.execute().startswith("[exit 0]")
