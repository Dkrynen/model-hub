# M2 Plan 4: Docker Task Sandbox

**Date:** 2026-07-11
**Status:** Accepted for implementation
**Scope:** Web Workbench Build mode only

## Objective

Let the web Build agent run user-defined verification tasks against the current
project plus its pending staged edits without exposing the host shell or writing
to the real project.

Plan 4 does **not** re-enable the existing `run_bash` tool. It adds a separate
`run_task({"name": ...})` contract backed only by a local Docker Desktop Linux
container. If Docker, the configured context, or the pinned image is unavailable,
task execution is unavailable and Build remains staged-edit-only. There is no
host fallback.

## Threat model

The boundary must prevent ordinary model-controlled task execution from:

- selecting an arbitrary host command, image, environment, mount, network, user,
  working directory, resource limit, or timeout;
- reading host secrets that were not copied into the disposable snapshot;
- writing to the selected project or importing container-generated changes;
- reaching the network or Docker socket;
- leaving an unbounded process tree, runtime, or output stream behind after
  cancellation, timeout, disconnect, or failure.

The boundary does not claim protection from a Docker or kernel escape, or from a
malicious image that the operator deliberately pinned. Docker Desktop's Linux VM
is the containment boundary; a Git worktree, temporary directory, `cwd`, Windows
Job Object, WSL invocation, or structured host subprocess alone is not.

## Project configuration

Applied on-disk `.apt/apt.jsonc` may declare one local Linux image and named
tasks:

```jsonc
{
  "sandbox": {
    "engine": "docker",
    "context": "desktop-linux",
    "image": "example/lac-dev@sha256:<64 hex digest>",
    "snapshot_include": ["src/**", "tests/**", "pyproject.toml"],
    "tasks": {
      "test": {
        "argv": ["python", "-m", "pytest", "-q"],
        "timeout_seconds": 180
      }
    }
  }
}
```

Rules:

- `engine` is `docker` only.
- `context` must resolve to the canonical local Windows
  `npipe:////./pipe/...` form. After the probe, every daemon/lifecycle call is
  pinned to that captured endpoint rather than the mutable context name.
- `image` must be a digest-pinned repository reference or exact local
  `sha256:<64 hex>` image ID.
- The image must already exist locally. LAC never starts Docker, pulls an image,
  builds a Dockerfile, or installs dependencies for a task.
- On Windows, LAC resolves `docker.exe` only at Docker Desktop's canonical
  native Program Files location read from the machine registry. It never
  searches the process CWD or `PATH`; unsupported host platforms fail closed.
  Docker subprocesses run from the executable's trusted directory with Docker
  environment selectors removed and a non-project `PATH`.
- The image must be Linux, declare no Dockerfile `VOLUME`, and provide
  `/bin/sh` plus absolute `/usr/bin/env`, `/usr/bin/setpriv`, `/usr/bin/tar`,
  and `/usr/bin/timeout` for LAC's fixed extraction, privilege-drop, and
  container-local deadline supervisor. Missing runtime support fails closed.
- `snapshot_include` is required and contains bounded, relative POSIX glob
  patterns. Empty/parent/backslash paths and whole-project catchalls (`*`,
  `**`, `**/*`) are refused. Sensitive-path hard denials still override a
  matching include pattern.
- Task names are bounded identifiers. `argv` is a non-empty bounded string list.
  Configured argv is never parsed as shell text; it is passed as quoted
  positional arguments to a fixed LAC-owned bootstrap.
- Timeout defaults to 120 seconds and is capped at 300 seconds.
- Network, mounts, environment, user, cwd, and resource controls are fixed by
  LAC and cannot be configured in v1.
- Staged edits to `.apt/apt.jsonc` do not become executable configuration in the
  same run; only the applied on-disk file is read.

## Approval contract

The model can submit only the configured task name. Before asking for approval,
the server freezes a prepared task specification containing:

- task name and exact argv;
- configured image reference and locally resolved image ID;
- canonical project root;
- timeout and fixed resource/network posture;
- exact pending staged-row IDs, relative paths, revisions, base/content hashes,
  and overlay digest;
- applied config digest.

The approval card and durable ask event are derived from that frozen object.
`run_task` uses the permission key `task`, but it always asks unless a hard policy
DENY applies. It is never rememberable. Config or staged-overlay drift between
approval and execution fails closed.

## Disposable snapshot

The real project is never mounted into the container.

After approval, LAC creates a unique scratch directory beneath its local data
root and copies only regular files matching the applied `snapshot_include`
policy into a snapshot child. The walker:

- never follows symlinks, junctions, reparse points, nested mount points, or
  multi-link files, and revalidates source identities around each open/copy;
- enforces per-file, file-count, and total-byte limits;
- excludes VCS metadata, `.apt`, `.env*`, credential/token/key files, `.ssh`,
  Docker credentials, virtual environments, dependency caches, build caches,
  model weights, and LAC private state even if an include pattern matches them;
- streams directory entries, observes the shared cancellation/deadline while
  walking and copying, and aborts rather than silently producing a partial
  snapshot when a hard limit is exceeded.

Pending staged rows are loaded only for the exact session and canonical root.
Every row must still be pending, have the same revision/content digest, and have
an unchanged disk base hash (or an absent base for a new file). Sensitive paths
are refused. Overlay rows are charged against the same per-file, file-count, and
total-byte caps before materialization.
Staged paths must also be portable relative project paths: alternate data
streams, Windows reserved device aliases, invalid characters, control bytes,
trailing dots/spaces, backslashes, absolute paths, and dot segments are refused
consistently by staging, apply/revert, snapshot preparation, and the web client.

The completed host snapshot is encoded as a bounded tar stream using manually
constructed regular-file/directory headers with relative names and normalized
UID/GID metadata. LAC creates the container with open stdin and streams that
archive through `docker start --attach --interactive`; no host path is mounted
or exposed to the container. A fixed bootstrap extracts it as UID/GID 65532
into a 512 MiB tmpfs at `/workspace` before executing the configured argv.
Container writes therefore stay in bounded memory-backed storage and are
discarded. They never create staged rows and never reach the real project. The
existing `write_file` staging and explicit Apply/Reject flow remains the only
host-write path.

## Container posture

LAC creates a uniquely named and labelled container using argument-list Docker
CLI calls through `backend.cookbook.proc`; it never invokes a host shell. The
effective posture is:

```text
--pull=never
--network=none
--hostname=lac-sandbox
--read-only
--cap-drop=ALL
--cap-add=KILL
--cap-add=SETPCAP
--cap-add=SETUID
--security-opt=no-new-privileges
--pids-limit=128
--memory=1g
--cpus=2
--user=0:65532
--workdir=/workspace
--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777
--tmpfs=/workspace:rw,nosuid,nodev,size=512m,mode=0770,uid=65532,gid=65532
--log-driver=none
--entrypoint=/bin/sh
```

No ports, devices, privileged mode, host namespace, Docker socket, environment
file, original-project mount, image-declared volume, or additional writable
mount is permitted. The fixed shell bootstrap contains no model text; task argv
is supplied only through quoted positional arguments and cannot become Docker
CLI options. Root exists only in the immutable timeout/setpriv supervisor with
the three listed capabilities. Before archive extraction or task execution,
`setpriv` changes to UID 65532, empties every capability set, locks `noroot`,
sets `no_new_privs` and a parent-death signal, and clears the image environment
to a fixed PATH/HOME/TMPDIR/locale. Task code cannot signal the root supervisor.

## Lifecycle and bounds

- `_AgentRun` owns a cancellation event and the approval capability token.
- An explicit cancel endpoint accepts only the exact active run capability; the
  browser also aborts its SSE reader. Cancellation is journaled once and a
  terminal event/sentinel ends a client that keeps the SSE connection open.
  Audit-store failure never blocks the cancellation signal.
- One deadline covers post-approval revalidation, snapshot/overlay preparation,
  Docker creation, and task execution. Disconnect, explicit cancel, timeout,
  output limit, and startup failure all use the same cancellation path.
- The broker retains the exact container ID returned by `docker create` and
  recovers it by unguessable owner/execution labels if CLI output is interrupted.
  When an interrupted create returns no ID, cleanup polls those exact labels for
  a bounded reconciliation window, removes a late-appearing container, and ends
  with an exact absence check. It verifies exact LAC ownership before each
  stop/kill/remove.
- Concurrency is capped at two with both an in-process semaphore and a
  cross-process byte-range lock; all labelled container states, including
  created/exited containers, count against the cap before a new task starts.
- Default timeout is 120 seconds; hard maximum is 300 seconds.
- The fixed bootstrap wraps configured argv with the image's `timeout` binary,
  so the task is killed at its approved limit and `--rm` reclaims the container
  even if the LAC host process exits unexpectedly.
- Combined stdout/stderr is continuously drained and capped at 64 KiB. Crossing
  the cap terminates the task. The bounded final result is the only output placed
  in model history and durable session events. Docker daemon logging is disabled
  so it cannot retain an unbounded second copy. The fixed hostname prevents the
  default container-ID hostname, and known container-ID forms are redacted
  before the bounded result is returned or persisted. Because the snapshot is
  stdin-streamed rather than bind-mounted, container mount metadata contains no
  host scratch source path.
- Scratch cleanup runs after success and every failure path.

## UX and audit

The Build panel reports one of:

- task sandbox ready, including the configured task names;
- Docker CLI missing;
- Docker Desktop installed but daemon unavailable;
- remote/non-Linux context refused;
- image unconfigured, unpinned, or unavailable;
- no valid tasks configured.

Unavailable task execution does not disable staged editing. Approval shows the
task name, argv, project root, pinned image, timeout, `Network disabled`, and
`Disposable snapshot; real project unchanged`. There is no Always Allow action.
Stop cancels the exact run.

Durable events preserve the bounded prepared specification, approval decision,
and final outcome without approval tokens, Docker credentials, container IDs,
host scratch paths, secrets, or unbounded output.

## Acceptance boundary

- Existing host `run_bash` remains unavailable to web Build.
- Plan and Explore remain unchanged and read-only.
- Host/Origin and loopback-only approval/cancel guards remain green.
- `model-hub` never imports `lac_pro`; `lac-pro` remains untouched and remote-free.
- Default tests use injected fake Docker/process adapters. A real Docker smoke is
  reported separately because the daemon and pinned image are external state.

## Rejected alternatives

- Reusing host `run_bash`, even with approval.
- Letting the model supply a shell string or arbitrary argv.
- Binding the real project, temporarily applying/reverting staged changes, or
  importing container output.
- Treating Git worktrees, WSL, `cwd`, Job Objects, Firewall rules, or restricted
  tokens as equivalent containment.
- Automatically starting Docker, pulling/building an image, or enabling network.
- Cloudflare Sandbox SDK: it is a cloud Worker/container product and would violate
  this local-first slice's deployment and dependency boundary.
