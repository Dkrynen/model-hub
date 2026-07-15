"""Windows DPAPI-backed storage for the LAC Cloud refresh credential."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .cookbook.config import resolve_under_data_root

_MAGIC = b"LAC-CLOUD-SESSION\x01\x00"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_LOCK_TIMEOUT_SECONDS = 30.0
_LOCKS_GUARD = threading.Lock()
_LOCKS = {}


class SecureTokenStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _InterprocessFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    @contextmanager
    def acquire(self):
        with self._thread_lock:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            handle = self._acquire_os_lock()
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0
                self._release_os_lock(handle)

    def _acquire_os_lock(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\x00")
                handle.flush()
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    handle.seek(0)
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, 13, 36} or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        except Exception as exc:
            try:
                handle.close()
            except (NameError, OSError):
                pass
            raise SecureTokenStoreError("secure_storage_unavailable") from exc

    @staticmethod
    def _release_os_lock(handle) -> None:
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _lock_for(path: Path) -> _InterprocessFileLock:
    normalized = os.path.normcase(str(path.resolve(strict=False)))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(normalized)
        if lock is None:
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            lock = _InterprocessFileLock(path.with_name(f".{path.name}.{digest}.lock"))
            _LOCKS[normalized] = lock
        return lock


def _dpapi_protect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecureTokenStoreError("secure_storage_unavailable")
    try:
        import win32crypt

        protected = win32crypt.CryptProtectData(
            value,
            "LAC Cloud refresh credential",
            None,
            None,
            None,
            0,
        )
        if isinstance(protected, tuple):
            protected = protected[1]
        return bytes(protected)
    except SecureTokenStoreError:
        raise
    except Exception as exc:
        raise SecureTokenStoreError("secure_storage_unavailable") from exc


def _dpapi_unprotect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecureTokenStoreError("secure_storage_unavailable")
    try:
        import win32crypt

        return bytes(win32crypt.CryptUnprotectData(value, None, None, None, 0)[1])
    except SecureTokenStoreError:
        raise
    except Exception as exc:
        raise SecureTokenStoreError("secure_storage_unavailable") from exc


class DpapiTokenStore:
    """Persist one refresh token encrypted for the current Windows user."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else resolve_under_data_root("cloud-session.bin")
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect
        self._rotation_lock = _lock_for(self.path)

    def rotation_lock(self):
        return self._rotation_lock.acquire()

    def save(self, token: str) -> None:
        if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
            raise SecureTokenStoreError("invalid_token")
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with self.rotation_lock():
            try:
                protected = bytes(self._protect(token.encode("ascii")))
                if not protected:
                    raise SecureTokenStoreError("secure_storage_unavailable")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp.write_bytes(_MAGIC + protected)
                os.replace(temp, self.path)
            except SecureTokenStoreError:
                raise
            except Exception as exc:
                raise SecureTokenStoreError("secure_storage_unavailable") from exc
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self) -> str | None:
        with self.rotation_lock():
            if not self.path.exists():
                return None
            try:
                payload = self.path.read_bytes()
            except OSError as exc:
                raise SecureTokenStoreError("secure_storage_unavailable") from exc
            if not payload.startswith(_MAGIC) or len(payload) <= len(_MAGIC):
                self._discard_corrupt()
            try:
                raw = self._unprotect(payload[len(_MAGIC):])
            except SecureTokenStoreError:
                raise
            except Exception:
                self._discard_corrupt()
            try:
                token = bytes(raw).decode("ascii")
            except (UnicodeDecodeError, TypeError, ValueError):
                self._discard_corrupt()
            if _TOKEN_PATTERN.fullmatch(token) is None:
                self._discard_corrupt()
            return token

    def clear(self) -> None:
        with self.rotation_lock():
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                raise SecureTokenStoreError("secure_storage_unavailable") from exc

    def _discard_corrupt(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SecureTokenStoreError("corrupt_store")
