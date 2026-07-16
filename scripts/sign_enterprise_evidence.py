from __future__ import annotations

import argparse
import base64
import copy
import getpass
import hashlib
import hmac
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import enterprise_launch_gate as gate  # noqa: E402


PRIVATE_KEY_MAX_BYTES = 16 * 1024
_DARWIN_F_GETPATH = 50
_DARWIN_MAXPATHLEN = 1024
_TOP_LEVEL_FIELDS = {"schema_version", "release_scope", "release_version", "gates"}
_RELEASE_BINDING_FIELDS = (
    "model_hub_commit",
    "lac_pro_commit",
    "installer_sha256",
    "release_provenance_sha256",
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _trusted_public_key(signer_kid: object) -> bytes:
    signer = gate.TRUSTED_EVIDENCE_SIGNERS.get(signer_kid) if isinstance(signer_kid, str) else None
    if not isinstance(signer, dict) or set(signer) != {
        "public_key", "approvers", "gates", "not_before", "not_after",
    }:
        raise ValueError("evidence record names an untrusted signer")
    try:
        public_key = gate._b64decode(signer["public_key"])
    except (TypeError, ValueError) as error:
        raise ValueError("trusted signer public key is invalid") from error
    if len(public_key) != 32:
        raise ValueError("trusted signer public key is invalid")
    return public_key


def _release_bindings(document: dict[str, Any]) -> dict[str, str]:
    records = document["gates"]
    first_name = gate.EVIDENCE_GATES_BY_SCOPE[document["release_scope"]][0]
    first = records[first_name]
    fields = list(_RELEASE_BINDING_FIELDS)
    if document["release_scope"] == "cloud":
        fields.append("lac_cloud_commit")
    bindings = {field: first.get(field) for field in fields}
    if any(not isinstance(value, str) for value in bindings.values()):
        raise ValueError("evidence release bindings are incomplete")
    for name, record in records.items():
        if any(record.get(field) != value for field, value in bindings.items()):
            raise ValueError(f"evidence gate {name} has inconsistent release bindings")
    return bindings


def _validate_cross_gate_bindings(document: dict[str, Any]) -> None:
    if document["release_scope"] != "cloud":
        return
    records = document["gates"]
    production = [records[name] for name in gate._PRODUCTION_DEPLOYMENT_EVIDENCE_GATES]
    expected = tuple(production[0].get(field) for field in gate._WORKER_BINDING_FIELDS)
    if any(
        tuple(record.get(field) for field in gate._WORKER_BINDING_FIELDS) != expected
        for record in production[1:]
    ):
        raise ValueError("production deployment evidence has inconsistent Worker bindings")


def sign_document(
    document: object,
    private_key: Ed25519PrivateKey,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Create an accountable-reviewer attestation over one schema-v3 document.

    This function validates record shape, freshness, trust-root authorization,
    and exact release bindings. It does not retrieve or review the external
    records named by each reference and does not establish reviewer
    independence; the operator acknowledgement remains a separate CLI
    requirement.
    """
    if not isinstance(document, dict) or set(document) != _TOP_LEVEL_FIELDS:
        raise ValueError("evidence document has an invalid top-level shape")
    release_scope = document.get("release_scope")
    if release_scope not in gate.RELEASE_SCOPES:
        raise ValueError("evidence document has an invalid release scope")
    if (
        document.get("schema_version") != gate.EVIDENCE_SCHEMA_VERSION
        or document.get("release_version") != gate.APP_VERSION
    ):
        raise ValueError("evidence document schema or release version is invalid")
    records = document.get("gates")
    required = gate.EVIDENCE_GATES_BY_SCOPE[release_scope]
    if not isinstance(records, dict) or set(records) != set(required):
        raise ValueError("evidence document does not contain the exact required gate set")
    if any(not isinstance(record, dict) or "signature" in record for record in records.values()):
        raise ValueError("every evidence record must be an unsigned object")

    derived_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    for name in required:
        trusted_public_key = _trusted_public_key(records[name].get("signer_kid"))
        if not hmac.compare_digest(derived_public_key, trusted_public_key):
            raise ValueError(f"private key does not match the trusted signer for {name}")

    signed = copy.deepcopy(document)
    bindings = _release_bindings(signed)
    current = time.time() if now is None else now
    for name in required:
        record = signed["gates"][name]
        signature = private_key.sign(
            gate.evidence_signature_payload(name, release_scope, gate.APP_VERSION, record)
        )
        record["signature"] = _b64url(signature)
        if not gate._verify_evidence_record(
            name,
            release_scope,
            gate.APP_VERSION,
            record,
            expected_model_hub_commit=bindings["model_hub_commit"],
            expected_lac_pro_commit=bindings["lac_pro_commit"],
            expected_lac_cloud_commit=bindings.get("lac_cloud_commit", ""),
            expected_installer_sha256=bindings["installer_sha256"],
            expected_provenance_sha256=bindings["release_provenance_sha256"],
            now=current,
        ):
            raise ValueError(f"evidence gate {name} is incomplete, stale, unauthorized, or invalid")
    _validate_cross_gate_bindings(signed)
    return signed


def _outside_repository(path: Path, *, label: str, strict: bool) -> Path:
    try:
        resolved = path.resolve(strict=strict)
        repository = ROOT.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{label} path could not be resolved") from error
    if resolved.is_relative_to(repository):
        raise ValueError(f"{label} must stay outside the repository")
    return resolved


def _read_regular_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise ValueError(f"{label} has an invalid file type or size")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            payload = handle.read(max_bytes + 1)
        if len(payload) != metadata.st_size or len(payload) > max_bytes:
            raise ValueError(f"{label} changed while it was being read")
        return payload
    except OSError as error:
        raise ValueError(f"{label} could not be read safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_document(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    if gate._LOWER_SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("expected input SHA-256 must be 64 lowercase hexadecimal characters")
    payload = _read_regular_file(
        path,
        label="input evidence draft",
        max_bytes=gate.EVIDENCE_MANIFEST_MAX_BYTES,
    )
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise ValueError("input evidence draft SHA-256 does not match the reviewed bytes")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=gate._unique_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("input evidence draft is invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError("input evidence draft must be a JSON object")
    return document


def _read_private_key(
    path: Path,
    *,
    prompt_password: bool,
    allow_unencrypted: bool,
) -> Ed25519PrivateKey:
    resolved = _outside_repository(path, label="private key", strict=True)
    payload = _read_regular_file(resolved, label="private key", max_bytes=PRIVATE_KEY_MAX_BYTES)
    encrypted = b"-----BEGIN ENCRYPTED PRIVATE KEY-----" in payload
    if encrypted and not prompt_password:
        raise ValueError("encrypted private key requires --prompt-key-password")
    if not encrypted and not allow_unencrypted:
        raise ValueError("unencrypted private key requires --allow-unencrypted-private-key")
    password = (
        getpass.getpass("Evidence private-key password: ").encode("utf-8")
        if prompt_password else None
    )
    try:
        loaded = serialization.load_pem_private_key(payload, password=password)
    except (TypeError, ValueError) as error:
        hint = " (use --prompt-key-password for an encrypted PEM)" if not prompt_password else ""
        raise ValueError(f"private key PEM could not be loaded{hint}") from error
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ValueError("private key must be Ed25519")
    return loaded


def _write_exclusive(path: Path, document: dict[str, Any]) -> None:
    if not path.parent.is_dir():
        raise ValueError("output directory does not exist")
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > gate.EVIDENCE_MANIFEST_MAX_BYTES:
        raise ValueError("signed evidence manifest is oversized")
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        created_identity = (metadata.st_dev, metadata.st_ino)
    except FileExistsError as error:
        raise ValueError("output file already exists") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            _verify_open_output_path(handle.fileno(), path)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            current = path.stat(follow_symlinks=False)
            if created_identity == (current.st_dev, current.st_ino):
                path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _darwin_open_handle_path(descriptor: int) -> Path:
    import fcntl

    try:
        raw_path = fcntl.fcntl(
            descriptor,
            _DARWIN_F_GETPATH,
            b"\0" * _DARWIN_MAXPATHLEN,
        )
    except (OSError, ValueError) as error:
        raise ValueError("signed evidence output final path could not be verified") from error
    if not isinstance(raw_path, bytes) or len(raw_path) != _DARWIN_MAXPATHLEN:
        raise ValueError("signed evidence output final path could not be verified")
    terminator = raw_path.find(b"\0")
    if terminator <= 0:
        raise ValueError("signed evidence output final path could not be verified")
    path = Path(os.fsdecode(raw_path[:terminator]))
    if not path.is_absolute():
        raise ValueError("signed evidence output final path could not be verified")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("signed evidence output final path could not be verified") from error


def _open_handle_path(descriptor: int) -> Path:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        get_final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32_768)
        length = get_final_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
        if length == 0 or length >= len(buffer):
            raise ValueError("signed evidence output final path could not be verified")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value).resolve(strict=True)
    if sys.platform == "darwin":
        return _darwin_open_handle_path(descriptor)
    for link in (Path(f"/proc/self/fd/{descriptor}"), Path(f"/dev/fd/{descriptor}")):
        try:
            if link.exists():
                return Path(os.readlink(link)).resolve(strict=True)
        except OSError:
            continue
    raise ValueError("signed evidence output final path could not be verified")


def _verify_open_output_path(descriptor: int, expected_path: Path) -> None:
    actual_path = _open_handle_path(descriptor)
    _outside_repository(actual_path, label="signed evidence output", strict=True)
    if os.path.normcase(str(actual_path)) != os.path.normcase(str(expected_path)):
        raise ValueError("signed evidence output was redirected after path validation")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline accountable-reviewer attestation for complete LAC launch evidence.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Unsigned schema-v3 evidence draft")
    parser.add_argument(
        "--expected-input-sha256",
        required=True,
        help="Exact SHA-256 of the reviewed draft bytes",
    )
    parser.add_argument("--private-key", type=Path, required=True, help="Offline Ed25519 PEM outside the repository")
    parser.add_argument("--output", type=Path, required=True, help="New signed manifest; existing files are never overwritten")
    parser.add_argument(
        "--prompt-key-password",
        action="store_true",
        help="Prompt securely for an encrypted PEM password",
    )
    parser.add_argument(
        "--allow-unencrypted-private-key",
        action="store_true",
        help="Explicitly accept an unencrypted PEM protected by external storage/ACL controls",
    )
    parser.add_argument(
        "--acknowledge-authoritative-records-reviewed",
        action="store_true",
        help="Confirm the accountable reviewer checked every referenced authoritative record",
    )
    return parser


def main(argv: list[str] | None = None, *, now: float | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.acknowledge_authoritative_records_reviewed:
        print(
            "Refusing to sign: --acknowledge-authoritative-records-reviewed is required.",
            file=sys.stderr,
        )
        return 2
    try:
        input_path = _outside_repository(args.input, label="input evidence draft", strict=True)
        output_path = _outside_repository(args.output, label="signed evidence output", strict=False)
        if input_path.parent != output_path.parent:
            raise ValueError("input and output must share one evidence-bundle directory")
        if args.prompt_key_password and args.allow_unencrypted_private_key:
            raise ValueError("choose either encrypted-key prompting or the unencrypted-key exception")
        draft = _read_document(input_path, expected_sha256=args.expected_input_sha256)
        private_key = _read_private_key(
            args.private_key,
            prompt_password=args.prompt_key_password,
            allow_unencrypted=args.allow_unencrypted_private_key,
        )
        signed = sign_document(
            draft,
            private_key,
            now=now,
        )
        if signed["release_scope"] == "cloud" and not gate._hosted_journey_objects_valid(
            input_path,
            signed["gates"]["hosted_agent_end_to_end"],
        ):
            raise ValueError("hosted journey content-addressed evidence objects are invalid")
        _write_exclusive(output_path, signed)
    except (OSError, ValueError) as error:
        print(f"Refusing to sign: {error}", file=sys.stderr)
        return 2
    print(f"Signed {len(signed['gates'])} evidence records to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
