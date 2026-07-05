# LAC Pro Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encrypt the LAC Pro license grant at rest, validate `repo_id` against Hugging Face's real grammar at the import choke point, and compile-out the `LAC_PRO_DEV` override in release builds — all within the honest client-side "casual-bypass" boundary.

**Architecture:** Phase 1 adds a `machine_id` helper + an AES-256-GCM envelope module (HKDF-derived key) in `lac-pro`, wired into `license.py`'s single read/write path with backward-compatible plaintext read + transparent upgrade + fail-safe never-raise. Phase 4 adds a grammar validator at `import_custom_model`'s entry, surfaced through the existing honest-failed-state dict. Phase 2 bakes an `IS_RELEASE` build constant that gates the dev override, baked to `True` by the Nuitka build script only.

**Tech Stack:** Python 3.11 (ABI-locked shipped runtime), `cryptography` (AES-GCM + HKDF), stdlib `winreg`/`subprocess` for `machine_id`, pytest (`-m "not live"`).

## Global Constraints

- **Repos:** primary work in `C:\Users\User\repos\lac-pro`; one dependency add in `C:\Users\User\repos\model-hub` (`requirements.txt`). Run lac-pro tests with model-hub's venv: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q -m "not live"` from the lac-pro dir.
- **Baselines to hold green:** lac-pro 118 non-live; model-hub 285 non-live.
- **`check()` never raises; `require()` exits 3.** Every new failure mode in `license.py` resolves to "no grant", never an exception out of `check()`.
- **`import_custom_model` never raises** for the reachable input domain — failures become `{"state":"failed","error_type":...,"message":...,"updated_at":...}`.
- **Honest boundary:** casual-bypass hardening, NOT DRM. KEK is derived from a locally-readable `machine_id`; a determined local user can re-derive it. Do not claim "untamperable".
- **No plaintext key introduced anywhere.** No new logging of any key material (audit logging is deferred).
- **Commits land on `master` in both repos, per task. NEVER push to origin. lac-pro never gets a remote.**
- **Subagent dispatch rule:** every dispatch MUST say "work in the foreground, do NOT spawn agents".
- Append each task's outcome to `<repo>/.superpowers/sdd/progress.md` in the relevant repo.

**Design note (deviation from spec, measured):** the spec's in-memory decrypted-grant cache existed to amortize PBKDF2. We use **HKDF** (a single extract+expand — microseconds), so a cold decrypt already meets the sub-ms warm target. The cache is dropped as YAGNI; the verification step *measures* warm `check()` and only reinstates a cache if the measurement misses sub-ms.

---

### Task 1: Stable `machine_id` helper

**Files:**
- Create: `lac_pro/machine_id.py`
- Test: `tests/test_machine_id.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `machine_id() -> str` (a stable 64-char hex digest, cached per process via `lru_cache`); internal `_raw_machine_id() -> str` (module-qualified so tests can monkeypatch it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_machine_id.py
import lac_pro.machine_id as mid


def test_machine_id_is_nonempty_hex():
    mid.machine_id.cache_clear()
    value = mid.machine_id()
    assert isinstance(value, str) and len(value) == 64
    int(value, 16)  # raises if not hex


def test_machine_id_is_stable_across_calls():
    mid.machine_id.cache_clear()
    assert mid.machine_id() == mid.machine_id()


def test_machine_id_falls_back_deterministically(monkeypatch):
    mid.machine_id.cache_clear()
    monkeypatch.setattr(mid, "_raw_machine_id", lambda: (_ for _ in ()).throw(RuntimeError("no source")))
    a = mid.machine_id()
    mid.machine_id.cache_clear()
    b = mid.machine_id()
    assert a == b and len(a) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_machine_id.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lac_pro.machine_id'`.

- [ ] **Step 3: Write minimal implementation**

```python
# lac_pro/machine_id.py
"""Stable, process-cached machine identifier for machine-binding the license
KEK. NOT a secret (locally readable) — used only so an encrypted grant copied
to another machine won't decrypt. Must be stable run-to-run, or every grant
silently invalidates (mass re-activation). Falls back deterministically."""
from __future__ import annotations

import functools
import hashlib
import platform
import subprocess
from pathlib import Path


def _windows_guid() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\Cryptography") as k:
        val, _ = winreg.QueryValueEx(k, "MachineGuid")
    return str(val)


def _macos_uuid() -> str:
    out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            return line.split('"')[-2]
    return ""


def _linux_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(p).read_text().strip()
        except OSError:
            continue
    return ""


def _raw_machine_id() -> str:
    system = platform.system()
    try:
        if system == "Windows":
            v = _windows_guid()
        elif system == "Darwin":
            v = _macos_uuid()
        else:
            v = _linux_machine_id()
        if v:
            return v
    except Exception:  # noqa: BLE001 — any source failure -> deterministic fallback
        pass
    return f"fallback:{platform.node()}:{platform.machine()}"


@functools.lru_cache(maxsize=1)
def machine_id() -> str:
    """Stable per-process machine id, normalised to a 64-char hex digest."""
    return hashlib.sha256(_raw_machine_id().encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_machine_id.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add lac_pro/machine_id.py tests/test_machine_id.py
git commit -m "feat(security): stable process-cached machine_id helper"
```

---

### Task 2: AES-256-GCM grant envelope + `cryptography` dependency

**Files:**
- Create: `lac_pro/grant_crypto.py`
- Modify: `C:\Users\User\repos\model-hub\requirements.txt` (add `cryptography>=42`)
- Modify: `lac_pro/pyproject.toml` (declare runtime dep for honest metadata)
- Test: `tests/test_grant_crypto.py`

**Interfaces:**
- Consumes: `lac_pro.machine_id.machine_id()` (called module-qualified so tests can swap it).
- Produces:
  - `ENVELOPE_VERSION = 2`
  - `encrypt_grant(grant: dict) -> str` — returns a JSON envelope string `{"v":2,"salt":b64,"nonce":b64,"ct":b64}`.
  - `is_envelope(raw: dict) -> bool` — True iff `raw` is a v2 envelope.
  - `decrypt_grant(envelope: dict) -> dict` — returns the grant; **raises** on any tamper/wrong-key/malformed input.

- [ ] **Step 1: Install the dependency into the test venv**

Run: `/c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pip install "cryptography>=42"`
Expected: `Successfully installed cryptography-...` (or "already satisfied").

- [ ] **Step 2: Write the failing test**

```python
# tests/test_grant_crypto.py
import json

import pytest

import lac_pro.grant_crypto as gc
import lac_pro.machine_id as mid

GRANT = {"key": "SECRET-KEY-123", "plan": "pro", "status": "granted", "expires_at": 4102444800.0}


def test_round_trip_recovers_grant():
    env = json.loads(gc.encrypt_grant(GRANT))
    assert gc.is_envelope(env)
    assert gc.decrypt_grant(env) == GRANT


def test_ciphertext_hides_the_key():
    blob = gc.encrypt_grant(GRANT)
    assert "SECRET-KEY-123" not in blob


def test_tampered_ciphertext_raises():
    env = json.loads(gc.encrypt_grant(GRANT))
    env["ct"] = env["ct"][:-4] + ("AAAA" if not env["ct"].endswith("AAAA") else "BBBB")
    with pytest.raises(Exception):
        gc.decrypt_grant(env)


def test_wrong_machine_cannot_decrypt(monkeypatch):
    env = json.loads(gc.encrypt_grant(GRANT))
    monkeypatch.setattr(mid, "_raw_machine_id", lambda: "a-totally-different-machine")
    mid.machine_id.cache_clear()
    try:
        with pytest.raises(Exception):
            gc.decrypt_grant(env)
    finally:
        mid.machine_id.cache_clear()


def test_is_envelope_rejects_plaintext_grant():
    assert not gc.is_envelope({"key": "X", "plan": "pro", "expires_at": 1.0})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_grant_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lac_pro.grant_crypto'`.

- [ ] **Step 4: Write minimal implementation**

```python
# lac_pro/grant_crypto.py
"""AES-256-GCM envelope for the license grant, keyed by HKDF over machine_id.

Honest boundary: raises the bar against casual snooping/hand-editing, NOT DRM —
the KEK derives from a locally-readable machine_id. HKDF (not PBKDF2): the input
key material is not a guessable password, so KDF stretching would only add
latency, not security. GCM's auth tag is the tamper-detector — no separate HMAC.
"""
from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from lac_pro import machine_id as _mid  # module-qualified so tests can swap the source

ENVELOPE_VERSION = 2
_INFO = b"lac-pro-license-v2"


def _derive_key(salt: bytes) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO)
    return hkdf.derive(_mid.machine_id().encode("utf-8"))


def encrypt_grant(grant: dict) -> str:
    import os
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ct = AESGCM(_derive_key(salt)).encrypt(nonce, json.dumps(grant).encode("utf-8"), None)
    return json.dumps({
        "v": ENVELOPE_VERSION,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    })


def is_envelope(raw: dict) -> bool:
    return (isinstance(raw, dict) and raw.get("v") == ENVELOPE_VERSION
            and {"salt", "nonce", "ct"} <= set(raw.keys()))


def decrypt_grant(envelope: dict) -> dict:
    """Recover the grant. Raises (InvalidTag / KeyError / ValueError) on any
    tamper, wrong machine, or malformed envelope — the caller fails safe."""
    salt = base64.b64decode(envelope["salt"])
    nonce = base64.b64decode(envelope["nonce"])
    ct = base64.b64decode(envelope["ct"])
    pt = AESGCM(_derive_key(salt)).decrypt(nonce, ct, None)
    return json.loads(pt.decode("utf-8"))
```

Also add the dependency:
- In `C:\Users\User\repos\model-hub\requirements.txt`, append a line: `cryptography>=42`
- In `lac_pro/pyproject.toml`, under `[project]` add (if no `dependencies` key exists): `dependencies = ["cryptography>=42"]`

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_grant_crypto.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add lac_pro/grant_crypto.py tests/test_grant_crypto.py pyproject.toml
git commit -m "feat(security): AES-256-GCM grant envelope (HKDF/machine_id KEK)"
cd /c/Users/User/repos/model-hub
git add requirements.txt
git commit -m "build: add cryptography dep (bundled for the Pro plugin's at-rest encryption)"
```

---

### Task 3: Encrypt the grant on write, decrypt on read (backward-compatible)

**Files:**
- Modify: `lac_pro/license.py` (`save_grant` ~L56-62, `_load_raw` ~L49-53)
- Modify: `lac_pro/activate.py` (`do_activate` save_grant call site ~L62-72)
- Test: `tests/test_license.py` (add cases; keep existing green)

**Interfaces:**
- Consumes: `lac_pro.grant_crypto.encrypt_grant / is_envelope / decrypt_grant`.
- Produces: unchanged public surface — `save_grant(dict)` now writes a v2 envelope; `_load_raw() -> dict | None` transparently decrypts v2 and still accepts legacy plaintext; `check()` semantics unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_license.py  (append)
import lac_pro.grant_crypto as gc


def test_saved_grant_is_encrypted_on_disk():
    lic.save_grant({"key": "K-SECRET", "plan": "pro", "expires_at": time.time() + 3600})
    on_disk = lic.GRANT_PATH.read_text()
    assert "K-SECRET" not in on_disk
    assert gc.is_envelope(json.loads(on_disk))


def test_encrypted_grant_round_trips_through_check():
    lic.save_grant({"key": "K-RT", "plan": "pro", "expires_at": time.time() + 3600})
    grant = lic.check()
    assert grant is not None and grant.key == "K-RT"


def test_legacy_plaintext_grant_still_loads():
    lic.GRANT_PATH.write_text(json.dumps({"key": "OLD", "plan": "pro", "expires_at": time.time() + 3600}))
    grant = lic.check()
    assert grant is not None and grant.key == "OLD"


def test_tampered_envelope_is_unlicensed():
    lic.save_grant({"key": "K", "plan": "pro", "expires_at": time.time() + 3600})
    env = json.loads(lic.GRANT_PATH.read_text())
    env["ct"] = env["ct"][:-4] + ("AAAA" if not env["ct"].endswith("AAAA") else "BBBB")
    lic.GRANT_PATH.write_text(json.dumps(env))
    assert lic.check() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_license.py -v`
Expected: `test_saved_grant_is_encrypted_on_disk` FAILS (`K-SECRET` present — grant written plaintext); tamper test FAILS (plaintext edit still parses).

- [ ] **Step 3: Write minimal implementation**

In `lac_pro/license.py`, replace `_load_raw` and `save_grant`:

```python
def _load_raw() -> dict | None:
    try:
        raw = json.loads(GRANT_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt == unlicensed
        return None
    try:
        from lac_pro.grant_crypto import is_envelope, decrypt_grant  # noqa: PLC0415
        if is_envelope(raw):
            return decrypt_grant(raw)
    except Exception:  # noqa: BLE001 — tamper/wrong-machine/crypto-missing -> unlicensed
        return None
    return raw  # legacy plaintext grant (v1 or plaintext v2); upgraded on next save


def save_grant(data: dict) -> None:
    GRANT_PATH.parent.mkdir(parents=True, exist_ok=True)
    from lac_pro.grant_crypto import encrypt_grant  # noqa: PLC0415
    GRANT_PATH.write_text(encrypt_grant(data))
    try:
        os.chmod(GRANT_PATH, 0o600)
    except OSError:
        pass
```

In `lac_pro/activate.py`, guard the `save_grant` call in `do_activate` so a crypto/disk failure is an honest activation failure, not a traceback. Replace the `save_grant({...})` block (~L62-72) with:

```python
    try:
        save_grant({
            "key": key,
            "activation_id": activation_id,
            "organization_id": lk.get("organization_id", org_id),
            "benefit_id": got,
            "plan": "pro",
            "status": lk.get("status", "granted"),
            "expires_at": lk.get("expires_at"),
            "last_validated_at": time.time(),
        })
    except Exception as e:  # noqa: BLE001 — could not secure the grant on disk
        return False, f"could not secure license on disk: {e}"
    return True, "activated — LAC Pro unlocked on this machine"
```

- [ ] **Step 4: Run the full lac-pro suite to verify green**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live"`
Expected: PASS — prior 118 + new cases, 0 failures. (The existing revalidation tests still pass: `check()` re-writes via `save_grant` which now encrypts, and re-reads via `_load_raw` which decrypts.)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add lac_pro/license.py lac_pro/activate.py tests/test_license.py
git commit -m "feat(security): encrypt license grant at rest; legacy plaintext still loads + upgrades"
```

---

### Task 4: `repo_id` grammar validation at the import choke point

**Files:**
- Modify: `lac_pro/hf_import.py` (add validator + error class near the other error classes; call at the top of `import_custom_model` ~L592; add an `except` branch ~L690)
- Test: `tests/test_hf_import.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `class InvalidRepoIdError(ValueError)` (module-level, alongside the other import error classes).
  - `validate_repo_id(repo_id: str) -> None` — raises `InvalidRepoIdError` with an honest message on any invalid id; returns None on valid.
  - `import_custom_model(...)` returns `{"state":"failed","error_type":"invalid_repo_id","message":...}` for a bad id, before any network/filesystem work.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hf_import.py  (append)
import pytest

import lac_pro.hf_import as hf


@pytest.mark.parametrize("ok", [
    "Qwen/Qwen2.5-0.5B-Instruct", "meta-llama/Llama-2-7b-hf",
    "TheBloke/Llama-2-7B-GGUF", "gpt2",
])
def test_validate_repo_id_accepts_real_ids(ok):
    hf.validate_repo_id(ok)  # must not raise


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd", "$(whoami)", "; rm -rf /", "x@evil.com/y",
    "https://evil.com/x", "a/b/c", "org/", "/model", "", "a b/c",
    "org/model\n", "-lead/model", "org/trail-", "org/dou..ble",
    "x" * 97 + "/y",
])
def test_validate_repo_id_rejects_payloads(bad):
    with pytest.raises(hf.InvalidRepoIdError):
        hf.validate_repo_id(bad)


def test_import_custom_model_rejects_bad_repo_id_before_any_work(monkeypatch):
    called = {"fetched": False}
    monkeypatch.setattr(hf, "fetch_hf_model_info",
                        lambda *a, **k: called.__setitem__("fetched", True))
    result = hf.import_custom_model("../../etc/passwd")
    assert result["state"] == "failed"
    assert result["error_type"] == "invalid_repo_id"
    assert called["fetched"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_hf_import.py -v -k "repo_id"`
Expected: FAIL with `AttributeError: module 'lac_pro.hf_import' has no attribute 'validate_repo_id'`.

- [ ] **Step 3: Write minimal implementation**

In `lac_pro/hf_import.py`, add near the other error classes:

```python
class InvalidRepoIdError(ValueError):
    """repo_id failed Hugging Face's namespace grammar at the entrypoint."""


import re as _re  # noqa: E402 — local to validation

# HF component grammar: alphanumeric runs joined by single . _ - , no leading/
# trailing/consecutive separators. Accept a bare legacy id (gpt2) OR org/model;
# never more than one slash. Security comes from this per-segment whitelist, not
# the slash count: ../, @host, full URLs, $(...), control chars all die here.
_HF_SEGMENT = _re.compile(r"^[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$")


def validate_repo_id(repo_id: str) -> None:
    if not isinstance(repo_id, str) or not repo_id or len(repo_id) > 200:
        raise InvalidRepoIdError(
            f"Invalid model id {repo_id!r}. Use the Hugging Face 'org/model' "
            f"form, e.g. Qwen/Qwen2.5-0.5B-Instruct."
        )
    segments = repo_id.split("/")
    if len(segments) > 2:
        raise InvalidRepoIdError(
            f"Invalid model id {repo_id!r}: too many '/'. Use 'org/model', "
            f"e.g. Qwen/Qwen2.5-0.5B-Instruct."
        )
    for seg in segments:
        if len(seg) > 96 or not _HF_SEGMENT.match(seg):
            raise InvalidRepoIdError(
                f"Invalid model id {repo_id!r}. Use the Hugging Face 'org/model' "
                f"form, e.g. Qwen/Qwen2.5-0.5B-Instruct."
            )
```

At the very top of `import_custom_model` body (before the `_write_import_status(... "checking" ...)` line at ~L592), add:

```python
    try:
        validate_repo_id(repo_id)
    except InvalidRepoIdError as e:
        result = {"state": "failed", "error_type": "invalid_repo_id",
                  "message": str(e), "updated_at": time.time()}
        _write_import_status(repo_id, result)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_hf_import.py -v -k "repo_id"`
Expected: PASS (all parametrized valid + payload cases + the import-choke-point case).

- [ ] **Step 5: Run the full lac-pro suite**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live"`
Expected: PASS, 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "feat(security): validate repo_id against HF grammar at the import entrypoint"
```

---

### Task 5: Gate the dev override behind an `IS_RELEASE` build constant

**Files:**
- Create: `lac_pro/_build.py`
- Modify: `lac_pro/license.py` (`check()` ~L84; add a small helper)
- Modify: `lac_pro/plugin.py` (`_cmd_status` ~L35)
- Test: `tests/test_license.py` (append), `tests/test_plugin.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `lac_pro/_build.py` with `IS_RELEASE = False` (source default; the build script rewrites it to `True`).
  - `license._dev_override_active() -> bool` — True iff `LAC_PRO_DEV == "1"` **and** the plugin is not a release build (neither `_build.IS_RELEASE` nor a compiled `.pyd` load).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_license.py  (append)
import lac_pro._build as _build


def test_dev_override_honored_in_source_build(monkeypatch):
    monkeypatch.setattr(_build, "IS_RELEASE", False)
    monkeypatch.setattr(lic, "_is_compiled", lambda: False)
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    grant = lic.check()
    assert grant is not None and grant.plan == "dev"


def test_dev_override_ignored_in_release_build(monkeypatch):
    monkeypatch.setattr(_build, "IS_RELEASE", True)
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    assert lic.check() is None  # no real grant on disk -> unlicensed


def test_dev_override_ignored_when_compiled(monkeypatch):
    monkeypatch.setattr(_build, "IS_RELEASE", False)
    monkeypatch.setattr(lic, "_is_compiled", lambda: True)
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    assert lic.check() is None
```

```python
# tests/test_plugin.py  (append)
import lac_pro._build as _build
import lac_pro.plugin as plugin


def test_status_hides_dev_hint_in_release_build(monkeypatch, capsys):
    monkeypatch.setattr(_build, "IS_RELEASE", True)
    monkeypatch.setattr("lac_pro.license.check", lambda *a, **k: None)
    plugin._cmd_status(None)
    out = capsys.readouterr().out
    assert "LAC_PRO_DEV" not in out


def test_status_shows_dev_hint_in_source_build(monkeypatch, capsys):
    monkeypatch.setattr(_build, "IS_RELEASE", False)
    monkeypatch.setattr("lac_pro.license.check", lambda *a, **k: None)
    plugin._cmd_status(None)
    out = capsys.readouterr().out
    assert "LAC_PRO_DEV" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_license.py tests/test_plugin.py -v -k "dev or release or hint"`
Expected: FAIL with `ModuleNotFoundError: No module named 'lac_pro._build'` / `AttributeError: ... _is_compiled`.

- [ ] **Step 3: Write minimal implementation**

```python
# lac_pro/_build.py
"""Build-time flags. Committed default is a SOURCE build. build/build_artifact.py
regenerates this file with IS_RELEASE = True immediately before the Nuitka
compile, so ONLY the shipped .pyd bakes True — the repo copy stays False for
Duan's dev venv."""
IS_RELEASE = False
```

In `lac_pro/license.py`, add a helper and use it in `check()`. Replace the first line of `check()`'s body (the `if os.environ.get("LAC_PRO_DEV") == "1":` guard, ~L84):

```python
def _is_compiled() -> bool:
    """True when running from the Nuitka-compiled .pyd (SPIKE: __file__/__spec__
    are faked by Nuitka, but the loader is a reliable compiled-vs-source signal).
    A committed release constant is primary; this is the belt-and-suspenders
    backstop so a forgotten bake still fails safe to 'override ignored'."""
    loader = type(__loader__).__module__ if "__loader__" in globals() else ""
    return "nuitka" in loader.lower()


def _dev_override_active() -> bool:
    from lac_pro import _build  # noqa: PLC0415
    if _build.IS_RELEASE or _is_compiled():
        return False
    return os.environ.get("LAC_PRO_DEV") == "1"


def check(validate_fn=None) -> Grant | None:
    """Return the active grant, or None. Never raises, never blocks long."""
    if _dev_override_active():
        return Grant(key="dev", plan="dev", expires_at=time.time() + 86400)
    # ... (rest of check() unchanged) ...
```

In `lac_pro/plugin.py`, make `_cmd_status` hide the dev hint in a release build. Replace the `if grant is None:` line (~L34-36):

```python
    if grant is None:
        from lac_pro import _build  # noqa: PLC0415
        if _build.IS_RELEASE:
            print("  license : none — run: lac pro activate <key>")
        else:
            print("  license : none — set LAC_PRO_DEV=1 (dev) or run: lac pro activate <key>")
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_license.py tests/test_plugin.py -v -k "dev or release or hint"`
Expected: PASS.

Then confirm the existing dev test still passes (source build is the test default):
Run: `... -m pytest tests/test_license.py::test_dev_env_grants -v`
Expected: PASS (source build, `_is_compiled()` is False under pytest → override honored).

- [ ] **Step 5: Run both full suites**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live"`
Expected: PASS, 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add lac_pro/_build.py lac_pro/license.py lac_pro/plugin.py tests/test_license.py tests/test_plugin.py
git commit -m "feat(security): compile-out LAC_PRO_DEV in release builds (IS_RELEASE + loader backstop)"
```

---

### Task 6: Bake `IS_RELEASE = True` into the Nuitka artifact

**Files:**
- Modify: `build/build_artifact.py` (`build()` ~L192-201, after the hermetic `copytree`, before `_compile_pyd`)
- Test: `tests/test_build_artifact.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_bake_release_flag(src_pkg: Path) -> None` — overwrites `<src_pkg>/_build.py` with `IS_RELEASE = True`; called on the hermetic temp copy only (never the repo).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_artifact.py  (append)
import lac_pro
from build import build_artifact  # if the build dir isn't importable, see note below


def test_bake_release_flag_writes_true(tmp_path):
    pkg = tmp_path / "lac_pro"
    pkg.mkdir()
    (pkg / "_build.py").write_text("IS_RELEASE = False\n")
    build_artifact._bake_release_flag(pkg)
    assert "IS_RELEASE = True" in (pkg / "_build.py").read_text()


def test_repo_build_flag_stays_false():
    # The committed source default must never be True (only the artifact copy).
    import importlib
    importlib.reload(lac_pro._build)
    assert lac_pro._build.IS_RELEASE is False
```

> Note: `build/build_artifact.py` is executed as a script (`REPO_ROOT/build/...`). If `from build import build_artifact` doesn't resolve under pytest's rootdir, import via path instead:
> ```python
> import importlib.util, pathlib
> _spec = importlib.util.spec_from_file_location(
>     "build_artifact",
>     pathlib.Path(__file__).resolve().parents[1] / "build" / "build_artifact.py")
> build_artifact = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(build_artifact)
> ```
> Use whichever import the existing `test_build_artifact.py` already uses for this module (match its convention).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_build_artifact.py -v -k "release or flag"`
Expected: FAIL with `AttributeError: module 'build_artifact' has no attribute '_bake_release_flag'`.

- [ ] **Step 3: Write minimal implementation**

In `build/build_artifact.py`, add the helper:

```python
def _bake_release_flag(src_pkg: Path) -> None:
    """Stamp IS_RELEASE = True into the HERMETIC COPY's _build.py before the
    Nuitka compile, so only the shipped .pyd is a release build. The repo copy
    stays False. Overwrites the whole file (single source of truth)."""
    (src_pkg / "_build.py").write_text(
        '"""Build-time flags — baked by build/build_artifact.py for the release '
        'artifact."""\nIS_RELEASE = True\n',
        encoding="utf-8",
    )
```

In `build()`, right after the `shutil.copy2(REPO_ROOT / "pyproject.toml", ...)` line (~L198) and before `dist_info = _build_dist_info(...)`:

```python
        _bake_release_flag(src / PACKAGE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_build_artifact.py -v -k "release or flag"`
Expected: PASS.

- [ ] **Step 5: Run the full lac-pro suite (non-live; the slow real-build test is deselected)**

Run: `cd /c/Users/User/repos/lac-pro && /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live and not slow"`
Expected: PASS, 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/User/repos/lac-pro
git add build/build_artifact.py tests/test_build_artifact.py
git commit -m "feat(security): bake IS_RELEASE=True into the Nuitka artifact (dev override compiled out)"
```

---

## Final verification (subagent-driven final whole-branch review + these checks)

- [ ] Both suites green at final state: lac-pro `-m "not live"` (≥118 + new) and model-hub `-m "not live"` (285) — the latter to confirm the `cryptography` add + no core regressions.
- [ ] **Latency measurement (settles the dropped-cache decision):**
  ```bash
  cd /c/Users/User/repos/lac-pro
  /c/Users/User/repos/model-hub/.venv/Scripts/python.exe -c "import timeit, lac_pro.license as l, time; l.save_grant({'key':'K','plan':'pro','expires_at':time.time()+3600}); print('warm check() us:', timeit.timeit(l.check, number=1000)/1000*1e6)"
  ```
  Expected: warm `check()` well under 1 ms. If it misses, add the mtime-keyed in-memory cache from the spec and re-measure.
- [ ] Manual security checks (describe/perform as feasible; note any Duan-gated ones):
  - Copy an encrypted `~/.model-hub/license.json` to a different HOME/machine → `check()` returns None (won't decrypt).
  - Hand-edit the envelope `ct` → `check()` returns None (GCM auth fails).
  - Simulate a release build (`_build.IS_RELEASE = True`) with `LAC_PRO_DEV=1` → override ignored.
  - Write a legacy plaintext grant → loads, then `save_grant` upgrades it to an envelope on next write.
- [ ] **Duan-gated build smoke (flag, do not block):** rebuild the shipped exe and confirm `cryptography` is bundled (PyInstaller hidden-import survival) and a real `lac pro activate` → encrypted `license.json`. This is the same class as every prior build/push gate.
- [ ] Ledger entries appended to both repos' `.superpowers/sdd/progress.md`.
- [ ] Nothing pushed to origin; lac-pro still has no remote.

## Self-review (author checklist — completed)

- **Spec coverage:** Phase 1 → Tasks 1-3 (machine_id, envelope, wire-in + backward-compat + fail-safe). Phase 4 → Task 4 (grammar, choke-point, honest failed state). Phase 2 → Tasks 5-6 (IS_RELEASE gate + loader backstop + status copy + build bake). Dropped/deferred phases carry no tasks (correct). Dependency-delivery consequence covered in Task 2. Latency + build-smoke covered in Final verification.
- **Placeholder scan:** none — every code/test step carries real content.
- **Type consistency:** `machine_id()`, `_raw_machine_id`, `encrypt_grant`/`is_envelope`/`decrypt_grant`, `validate_repo_id`/`InvalidRepoIdError`, `_dev_override_active`/`_is_compiled`, `_bake_release_flag` names are used identically across producing and consuming tasks.
