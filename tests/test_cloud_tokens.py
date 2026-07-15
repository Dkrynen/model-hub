from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.cloud_tokens import DpapiTokenStore, SecureTokenStoreError


TOKEN = "r" * 43
ROOT = Path(__file__).resolve().parents[1]


def _protect(value: bytes) -> bytes:
    return b"sealed:" + bytes(byte ^ 0xA5 for byte in value)


def _unprotect(value: bytes) -> bytes:
    if not value.startswith(b"sealed:"):
        raise ValueError("invalid protected payload")
    return bytes(byte ^ 0xA5 for byte in value.removeprefix(b"sealed:"))


def test_refresh_token_is_never_written_in_plaintext(tmp_path):
    path = tmp_path / "cloud-session.bin"
    store = DpapiTokenStore(path=path, protect=_protect, unprotect=_unprotect)

    store.save(TOKEN)

    assert TOKEN.encode() not in path.read_bytes()
    assert store.load() == TOKEN


def test_refresh_token_store_rejects_invalid_tokens(tmp_path):
    store = DpapiTokenStore(path=tmp_path / "cloud-session.bin", protect=_protect, unprotect=_unprotect)

    with pytest.raises(SecureTokenStoreError, match="invalid_token"):
        store.save("not a cloud credential")


def test_corrupt_refresh_token_store_fails_closed_and_removes_blob(tmp_path):
    path = tmp_path / "cloud-session.bin"
    path.write_bytes(b"not-dpapi")
    store = DpapiTokenStore(path=path, protect=_protect, unprotect=_unprotect)

    with pytest.raises(SecureTokenStoreError, match="corrupt_store"):
        store.load()

    assert not path.exists()


def test_refresh_token_store_clear_is_idempotent(tmp_path):
    path = tmp_path / "cloud-session.bin"
    store = DpapiTokenStore(path=path, protect=_protect, unprotect=_unprotect)
    store.save(TOKEN)

    store.clear()
    store.clear()

    assert store.load() is None


def test_dpapi_failure_does_not_fall_back_to_plaintext(tmp_path):
    path = tmp_path / "cloud-session.bin"

    def unavailable(_value: bytes) -> bytes:
        raise OSError("DPAPI unavailable")

    store = DpapiTokenStore(path=path, protect=unavailable, unprotect=_unprotect)

    with pytest.raises(SecureTokenStoreError, match="secure_storage_unavailable"):
        store.save(TOKEN)

    assert not path.exists()


def test_rotation_lock_excludes_a_second_process(tmp_path):
    path = tmp_path / "cloud-session.bin"
    marker = tmp_path / "child-holds-lock"
    script = """
import sys
import time
from pathlib import Path
from backend.cloud_tokens import DpapiTokenStore

store = DpapiTokenStore(path=Path(sys.argv[1]))
with store.rotation_lock():
    Path(sys.argv[2]).write_text("ready", encoding="ascii")
    time.sleep(0.75)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(path), str(marker)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 5
    while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if not marker.exists():
        stdout, stderr = child.communicate(timeout=2)
        pytest.fail(f"lock holder did not start: {stdout} {stderr}")

    store = DpapiTokenStore(path=path, protect=_protect, unprotect=_unprotect)
    started = time.monotonic()
    with store.rotation_lock():
        elapsed = time.monotonic() - started

    stdout, stderr = child.communicate(timeout=2)
    assert child.returncode == 0, (stdout, stderr)
    assert elapsed >= 0.4
