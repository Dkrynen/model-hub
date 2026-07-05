"""Generic licensed-plugin bootstrap: fetch an artifact from a license gate
and install it into the LAC plugin directory.

``lac unlock <key>`` POSTs ``{"license_key": <key>}`` to the delivery gate
(a Cloudflare Worker, see ``worker/``); a valid key streams back a plugin
artifact — a ZIP whose ROOT holds a compiled module + its ``*.dist-info/``.
Install = extract that ZIP into ``PLUGIN_DIR``, which ``backend.plugins``
prepends to ``sys.path`` before entry-point discovery, so the plugin mounts
on the next start.

This module is deliberately plugin-agnostic: it delivers ANY licensed plugin
artifact and knows nothing about what the plugin does (no tuning, benchmark,
or license logic lives here).

``install_pro_plugin`` NEVER raises — every failure returns an honest
``{"state": "failed", "error_type": ..., "message": ...}`` with one of four
error types (the CLI decides exit codes):

- ``invalid_key``  the gate rejected the key (HTTP 403 — invalid or expired)
- ``network``      the gate could not be reached at all
- ``download``     the gate answered but the artifact did not arrive intact
                   (non-200 status, truncated read, corrupt/unsafe archive)
- ``install``      the artifact was fine but writing it to disk failed
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

#: Where licensed plugins are installed. ``backend.plugins`` puts this dir on
#: ``sys.path`` before discovery. Tests patch this module attribute.
PLUGIN_DIR = Path.home() / ".model-hub" / "plugins"

#: The live LAC Pro delivery gate (Cloudflare Worker, deployed 2026-07-05).
#: Override at runtime with the ``LAC_PRO_GATE_URL`` env var (read per call).
PRO_GATE_URL = "https://lac-pro-gate.refersal.workers.dev/pro/download"

GATE_TIMEOUT_S = 60


class _GateReadError(Exception):
    """The gate responded, but reading the body failed / was truncated."""


class _UnsafeArchiveError(Exception):
    """The archive is empty, corrupt, or tries to escape the install dir."""


def _gate_url(explicit: str | None) -> str:
    """Explicit argument > ``LAC_PRO_GATE_URL`` env (read at call time) > constant."""
    return explicit or os.environ.get("LAC_PRO_GATE_URL") or PRO_GATE_URL


def _http_post(url: str, payload: dict) -> tuple[int, bytes]:
    """POST JSON to the gate, return ``(status, body)``.

    Failures to *reach* the gate (DNS, refused, timeout) propagate raw — the
    caller maps them to ``network``. A body read that fails AFTER the gate
    responded raises ``_GateReadError`` — the caller maps it to ``download``.
    Non-2xx HTTP statuses are returned, not raised.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=GATE_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 — the status code is what matters here
            body = b""
        return exc.code, body
    try:
        with resp:
            return resp.getcode(), resp.read()
    except Exception as exc:  # noqa: BLE001 — connection dropped mid-body
        raise _GateReadError(str(exc)) from exc


def _validate_archive(zf: zipfile.ZipFile, dest_root: Path) -> None:
    """Validate the artifact fully IN MEMORY, before any filesystem write.

    - must contain at least one entry (an empty "install" would be a lie);
    - every entry must resolve to strictly inside ``dest_root`` (zip-slip
      guard — resolve-then-``parents`` containment, the same pattern as the
      workspace path safety in ``backend/cookbook/config.py``);
    - every member's CRC must check out (``testzip`` decompresses in memory).
    """
    names = zf.namelist()
    if not names:
        raise _UnsafeArchiveError("the artifact archive is empty")
    root = dest_root.resolve()
    for name in names:
        try:
            target = (root / name).resolve()
        except (OSError, ValueError) as exc:
            raise _UnsafeArchiveError(f"unsafe archive entry {name!r}: {exc}") from exc
        if target == root or root not in target.parents:
            raise _UnsafeArchiveError(
                f"archive entry {name!r} escapes the install directory"
            )
    bad = zf.testzip()
    if bad is not None:
        raise _UnsafeArchiveError(f"corrupt archive member: {bad!r}")


def _move_contents(staging: Path, dest: Path) -> None:
    """Move every top-level staged item into ``dest``, replacing what's there
    (re-running unlock = overwrite/upgrade). A locked or undeletable existing
    file surfaces as ``OSError`` → an ``install`` failure."""
    for item in staging.iterdir():
        target = dest / item.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(item), str(target))


def _install(zf: zipfile.ZipFile, plugin_dir: Path) -> None:
    """Extract to a staging dir first, then move into place — a failure
    mid-extract leaves the plugin dir's contents exactly as they were."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".lac-unlock-", dir=str(plugin_dir.parent)))
    try:
        zf.extractall(staging)
        _move_contents(staging, plugin_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _failed(error_type: str, message: str) -> dict:
    return {"state": "failed", "error_type": error_type, "message": message}


def install_pro_plugin(
    license_key: str, *, gate_url: str | None = None, http_post=None
) -> dict:
    """Fetch the licensed plugin artifact from the gate and install it.

    Returns ``{"state": "installed", "path": <plugin dir>}`` on success, or
    ``{"state": "failed", "error_type": ..., "message": ...}``. Never raises.
    """
    url = _gate_url(gate_url)
    post = http_post or _http_post

    # 1) Fetch from the gate.
    try:
        status, body = post(url, {"license_key": license_key})
    except _GateReadError as exc:
        return _failed(
            "download",
            f"The download was interrupted before it completed: {exc}. Try again.",
        )
    except Exception as exc:  # noqa: BLE001 — DNS/timeout/refused/any transport failure
        return _failed(
            "network",
            f"Could not reach the LAC Pro gate at {url}: {exc}. "
            "Check your connection and try again.",
        )

    # 2) Map the gate's answer to the honest states.
    if status == 403:
        return _failed(
            "invalid_key",
            "Your license key was not accepted (invalid or expired). "
            "Check the key and try again.",
        )
    if status != 200:
        return _failed(
            "download",
            f"The gate returned HTTP {status} instead of the artifact. "
            "Try again later.",
        )

    # 3) Validate in memory, then install via staging.
    plugin_dir = Path(PLUGIN_DIR)  # module attribute read at call time (tests patch it)
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            _validate_archive(zf, plugin_dir)
            _install(zf, plugin_dir)
    except (zipfile.BadZipFile, zlib.error, _UnsafeArchiveError) as exc:
        return _failed(
            "download", f"The downloaded artifact is not a valid plugin archive: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 — permissions, locked files, disk full
        return _failed(
            "install",
            f"Could not install into {plugin_dir}: {exc}. "
            "If LAC is running, close it and re-run `lac unlock`.",
        )

    return {"state": "installed", "path": str(plugin_dir)}
