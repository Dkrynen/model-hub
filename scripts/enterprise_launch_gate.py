from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.version import __version__ as APP_VERSION  # noqa: E402


REQUIRED_EVIDENCE_GATES = (
    "patent_clearance",
    "github_enterprise_controls",
    "polar_products_ready",
    "cloudflare_account_boundary",
    "turnstile_validation",
    "waf_abuse_protection",
    "cloud_staging_smoke",
    "cloud_production_dark_smoke",
    "regional_latency_slo",
    "private_paid_beta",
    "external_pentest",
    "cryptographic_review",
    "remediation_verified",
    "incident_response_tabletop",
    "credential_rotation_drill",
    "restore_rollback_deletion_drills",
    "artifact_roundtrip",
    "clean_machine_signed_install",
)
_PLACEHOLDER = re.compile(r"(?:\btbd\b|\btodo\b|\bpending\b|replace|example)", re.IGNORECASE)
_SHA256 = re.compile(r"[A-Fa-f0-9]{64}")
_GIT_COMMIT = re.compile(r"[a-f0-9]{40}")
_WORKER_VERSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_B64URL = re.compile(r"[A-Za-z0-9_-]+")
_SIGNED_STATES = frozenset({"G"})
_EVIDENCE_RECORD_FIELDS = {
    "status", "approver", "reference", "recorded_at", "record_sha256",
    "signer_kid", "signature",
}
_DEPLOYMENT_BINDING_FIELDS = {
    "deployment_commit", "api_version_id", "agent_version_id", "runner_version_id",
}
_DEPLOYMENT_EVIDENCE_FIELDS = _EVIDENCE_RECORD_FIELDS | _DEPLOYMENT_BINDING_FIELDS
_LATENCY_EVIDENCE_FIELDS = _DEPLOYMENT_EVIDENCE_FIELDS | {"measured_at"}
_DEPLOYMENT_EVIDENCE_GATES = frozenset({
    "cloud_production_dark_smoke",
    "regional_latency_slo",
})

# Immutable disclosure-freeze bases. Only commits after these reviewed objects
# belong to the 2.7.0 launch range. Changing a local upstream cannot narrow it.
MODEL_HUB_RELEASE_BASE = (
    "c84d0fffae638664c6887b5786645cd4055d5c45"  # pragma: allowlist secret -- public Git commit
)
LAC_PRO_RELEASE_BASE = (
    "138898dac87a5b9ce0df4d4a4c0169f2d27a7fff"  # pragma: allowlist secret -- private local Git commit
)

# Trust roots are intentionally empty until approved signer identities are
# onboarded in a reviewed commit. An empty trust root fails closed.
TRUSTED_COMMIT_SIGNERS: frozenset[str] = frozenset()
TRUSTED_EVIDENCE_SIGNERS: dict[str, dict[str, object]] = {}
EXPECTED_AUTHENTICODE_SUBJECTS: frozenset[str] = frozenset()
EXPECTED_AUTHENTICODE_THUMBPRINTS: frozenset[str] = frozenset()
EXPECTED_GITHUB_REPOSITORY = "Dkrynen/lac"
EXPECTED_SIGNER_WORKFLOW = "Dkrynen/lac/.github/workflows/build.yml"

EVIDENCE_MAX_AGE_DAYS = {
    "patent_clearance": 365,
    "github_enterprise_controls": 30,
    "polar_products_ready": 30,
    "cloudflare_account_boundary": 30,
    "turnstile_validation": 30,
    "waf_abuse_protection": 30,
    "cloud_staging_smoke": 14,
    "cloud_production_dark_smoke": 7,
    "regional_latency_slo": 1,
    "private_paid_beta": 90,
    "external_pentest": 90,
    "cryptographic_review": 180,
    "remediation_verified": 90,
    "incident_response_tabletop": 180,
    "credential_rotation_drill": 180,
    "restore_rollback_deletion_drills": 180,
    "artifact_roundtrip": 14,
    "clean_machine_signed_install": 14,
}


def _result(
    name: str,
    ok: bool,
    detail: str,
    *,
    lane: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "lane": lane,
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "data": data or {},
    }


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args])


def _nonplaceholder(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) >= 3 and _PLACEHOLDER.search(value) is None


def _recorded_at(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _b64decode(value: object) -> bytes:
    if not isinstance(value, str) or _B64URL.fullmatch(value) is None:
        raise ValueError("invalid base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise ValueError("non-canonical base64url")
    return decoded


def evidence_signature_payload(gate: str, release_version: str, record: dict[str, Any]) -> bytes:
    signed = {key: value for key, value in record.items() if key != "signature"}
    return json.dumps(
        {"gate": gate, "release_version": release_version, "record": signed},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _verify_evidence_record(
    name: str,
    release_version: str,
    record: object,
    *,
    expected_cloud_commit: str,
    now: float,
) -> bool:
    expected_fields = (
        _LATENCY_EVIDENCE_FIELDS if name == "regional_latency_slo"
        else _DEPLOYMENT_EVIDENCE_FIELDS if name == "cloud_production_dark_smoke"
        else _EVIDENCE_RECORD_FIELDS
    )
    if not isinstance(record, dict) or set(record) != expected_fields:
        return False
    recorded_at = _recorded_at(record.get("recorded_at"))
    max_age = EVIDENCE_MAX_AGE_DAYS[name] * 86_400
    if (
        record.get("status") not in {"approved", "passed", "verified"}
        or not _nonplaceholder(record.get("approver"))
        or not _nonplaceholder(record.get("reference"))
        or not isinstance(record.get("record_sha256"), str)
        or _SHA256.fullmatch(str(record.get("record_sha256"))) is None
        or recorded_at is None
        or recorded_at > now + 300
        or now - recorded_at > max_age
    ):
        return False
    if name in _DEPLOYMENT_EVIDENCE_GATES:
        if (
            _GIT_COMMIT.fullmatch(expected_cloud_commit) is None
            or record.get("deployment_commit") != expected_cloud_commit
            or any(
                not isinstance(record.get(field), str)
                or _WORKER_VERSION_ID.fullmatch(record[field]) is None
                for field in ("api_version_id", "agent_version_id", "runner_version_id")
            )
        ):
            return False
    if name == "regional_latency_slo":
        measured_at = _recorded_at(record.get("measured_at"))
        if (
            measured_at is None
            or measured_at > now + 300
            or measured_at > recorded_at + 300
            or now - measured_at > max_age
        ):
            return False
    kid = record.get("signer_kid")
    signer = TRUSTED_EVIDENCE_SIGNERS.get(kid) if isinstance(kid, str) else None
    if not isinstance(signer, dict) or set(signer) != {
        "public_key", "approvers", "gates", "not_before", "not_after",
    }:
        return False
    if (
        record.get("approver") not in signer.get("approvers", [])
        or name not in signer.get("gates", [])
        or not isinstance(signer.get("not_before"), int)
        or not isinstance(signer.get("not_after"), int)
        or not int(signer["not_before"]) <= recorded_at <= int(signer["not_after"])
    ):
        return False
    try:
        public_key = _b64decode(signer.get("public_key"))
        signature = _b64decode(record.get("signature"))
        if len(public_key) != 32 or len(signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            evidence_signature_payload(name, release_version, record),
        )
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def check_evidence(
    path: Path,
    expected_version: str,
    *,
    expected_cloud_commit: str = "",
    now: float | None = None,
) -> list[dict[str, Any]]:
    missing = [
        _result(
            f"evidence_{name}",
            False,
            "evidence manifest is missing",
            lane="external_evidence",
        )
        for name in REQUIRED_EVIDENCE_GATES
    ]
    if not path.is_file():
        return missing
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return [
            {**row, "detail": "evidence manifest is invalid"}
            for row in missing
        ]
    if not isinstance(document, dict):
        return [{**row, "detail": "evidence manifest is invalid"} for row in missing]
    version_ok = document.get("schema_version") == 1 and document.get("release_version") == expected_version
    gates = document.get("gates")
    if not isinstance(gates, dict):
        gates = {}
    current = time.time() if now is None else now
    verified: dict[str, bool] = {}
    for name in REQUIRED_EVIDENCE_GATES:
        record = gates.get(name)
        verified[name] = version_ok and _verify_evidence_record(
            name,
            expected_version,
            record,
            expected_cloud_commit=expected_cloud_commit,
            now=current,
        )
    deployment_records = [gates.get(name) for name in _DEPLOYMENT_EVIDENCE_GATES]
    if all(verified.get(name) for name in _DEPLOYMENT_EVIDENCE_GATES):
        bindings_match = all(
            deployment_records[0].get(field) == deployment_records[1].get(field)
            for field in _DEPLOYMENT_BINDING_FIELDS
        )
        if not bindings_match:
            for name in _DEPLOYMENT_EVIDENCE_GATES:
                verified[name] = False
    rows: list[dict[str, Any]] = []
    for name in REQUIRED_EVIDENCE_GATES:
        record_ok = verified[name]
        rows.append(_result(
            f"evidence_{name}",
            record_ok,
            "verified evidence record is present" if record_ok else (
                "evidence release version does not match" if not version_ok
                else "evidence record is missing, stale, untrusted, or invalid"
            ),
            lane="external_evidence",
        ))
    return rows


def check_repository(
    name: str,
    path: Path,
    *,
    require_zero_remotes: bool = False,
    required_remote: str | None = None,
    base_commit: str | None = None,
) -> list[dict[str, Any]]:
    lane = "repositories"
    if not (path / ".git").exists():
        return [
            _result(f"{name}_exists", False, "Git repository is missing", lane=lane),
            _result(f"{name}_clean", False, "Git repository is missing", lane=lane),
            _result(f"{name}_signed_commits", False, "Git repository is missing", lane=lane),
            _result(f"{name}_remote", False, "Git repository is missing", lane=lane),
        ]

    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    dirty_count = len([line for line in status.stdout.splitlines() if line]) if status.returncode == 0 else -1
    clean = status.returncode == 0 and dirty_count == 0

    base_ok = True
    revision = "HEAD"
    if base_commit:
        base_exists = _git(path, "cat-file", "-e", f"{base_commit}^{{commit}}")
        ancestor = _git(path, "merge-base", "--is-ancestor", base_commit, "HEAD")
        base_ok = base_exists.returncode == 0 and ancestor.returncode == 0
        revision = f"{base_commit}..HEAD"
    signatures = _git(path, "log", revision, "--format=%G?%x00%GF") if base_ok else None
    signature_rows: list[tuple[str, str]] = []
    if signatures is not None and signatures.returncode == 0:
        for line in signatures.stdout.splitlines():
            state, _, fingerprint = line.partition("\0")
            if state:
                signature_rows.append((state, fingerprint.strip().upper()))
    unsigned_count = sum(state not in _SIGNED_STATES for state, _ in signature_rows)
    untrusted_count = sum(
        state in _SIGNED_STATES and fingerprint not in TRUSTED_COMMIT_SIGNERS
        for state, fingerprint in signature_rows
    )
    signed_ok = base_ok and bool(signature_rows) and unsigned_count == 0 and untrusted_count == 0

    remotes = _git(path, "remote", "-v")
    remote_lines = [line for line in remotes.stdout.splitlines() if line]
    remote_records = {
        (parts[0], parts[1], parts[2].strip("()"))
        for line in remote_lines
        if len(parts := line.split()) == 3
    }
    remote_count = len({name for name, _, _ in remote_records}) if remotes.returncode == 0 else -1
    if require_zero_remotes:
        remote_ok = remote_count == 0
        remote_detail = "repository has zero remotes" if remote_ok else "local-only repository has a remote"
    elif required_remote:
        expected = {
            ("origin", required_remote, "fetch"),
            ("origin", required_remote, "push"),
        }
        remote_ok = remote_records == expected
        remote_detail = "exact approved remote is configured" if remote_ok else "remote set differs from the approved contract"
    else:
        remote_ok = remote_count > 0
        remote_detail = "repository has a remote" if remote_ok else "repository remote is missing"

    return [
        _result(f"{name}_exists", True, "Git repository exists", lane=lane),
        _result(
            f"{name}_clean",
            clean,
            "worktree is clean" if clean else "worktree has changes",
            lane=lane,
            data={"dirty_count": dirty_count},
        ),
        _result(
            f"{name}_signed_commits",
            signed_ok,
            "release-range commits are signed" if signed_ok else "release-range contains unsigned commits",
            lane=lane,
            data={
                "base_commit_verified": base_ok,
                "commit_count": len(signature_rows),
                "unsigned_count": unsigned_count,
                "untrusted_signer_count": untrusted_count,
            },
        ),
        _result(
            f"{name}_remote",
            remote_ok,
            remote_detail,
            lane=lane,
            data={"remote_count": remote_count},
        ),
    ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _checksum_entry(path: Path, filename: str) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw_line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename and re.fullmatch(r"[A-Fa-f0-9]{64}", parts[0]):
            return parts[0].upper()
    return None


def _authenticode(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "platform_unavailable", "subject": "", "thumbprint": ""}
    path_literal = str(path).replace("'", "''")
    command = (
        f"& {{ $Path='{path_literal}'; "
        "$ErrorActionPreference='Stop'; "
        "$s=Get-AuthenticodeSignature -LiteralPath $Path; "
        "$tsa=$s.TimeStamperCertificate; "
        "$tsaEku=$false; "
        "if ($null -ne $tsa) { $ekuExtension=$tsa.Extensions | "
        "Where-Object { $_.Oid.Value -eq '2.5.29.37' }; "
        "$tsaEku=@($ekuExtension.EnhancedKeyUsages | "
        "Where-Object { $_.Value -eq '1.3.6.1.5.5.7.3.8' }).Count -gt 0 }; "
        "$signtool=Get-ChildItem \"${env:ProgramFiles(x86)}\\Windows Kits\\10\\bin\" "
        "-Filter signtool.exe -Recurse | Where-Object FullName -Match "
        "'\\\\x64\\\\signtool\\.exe$' | Sort-Object FullName -Descending | "
        "Select-Object -First 1 -ExpandProperty FullName; "
        "if (-not $signtool) { throw 'signtool.exe was not found' }; "
        "$verified=@(& $signtool verify /pa /all /v $Path 2>&1); "
        "if ($LASTEXITCODE -ne 0) { throw 'signtool verification failed' }; "
        "$m=[regex]::Match(($verified -join \"`n\"), "
        "'(?im)^\\s*The signature is timestamped:\\s*(.+?)\\s*$'); "
        "if (-not $m.Success) { throw 'RFC3161 timestamp is missing' }; "
        "$parsedStamp=[DateTimeOffset]::MinValue; "
        "$style=[Globalization.DateTimeStyles]::AllowWhiteSpaces -bor "
        "[Globalization.DateTimeStyles]::AssumeLocal; "
        "$parsedOk=[DateTimeOffset]::TryParseExact($m.Groups[1].Value, "
        "'ddd MMM dd HH:mm:ss yyyy', [Globalization.CultureInfo]::InvariantCulture, "
        "$style, [ref]$parsedStamp); "
        "if (-not $parsedOk) { $parsedOk=[DateTimeOffset]::TryParse("
        "$m.Groups[1].Value, [Globalization.CultureInfo]::CurrentCulture, "
        "$style, [ref]$parsedStamp) }; "
        "if (-not $parsedOk) { throw 'timestamp time is invalid' }; "
        "$stamp=$parsedStamp.UtcDateTime; "
        "if ($null -eq $tsa -or -not $tsaEku -or "
        "$stamp -lt $tsa.NotBefore.ToUniversalTime() -or "
        "$stamp -gt $tsa.NotAfter.ToUniversalTime()) { throw 'timestamp certificate is invalid' }; "
        "[ordered]@{status=$s.Status.ToString();subject=$s.SignerCertificate.Subject;"
        "thumbprint=$s.SignerCertificate.Thumbprint;timestamp_subject=$tsa.Subject;"
        "timestamp_thumbprint=$tsa.Thumbprint;"
        "timestamp_not_before=$tsa.NotBefore.ToUniversalTime().ToString('o');"
        "timestamp_not_after=$tsa.NotAfter.ToUniversalTime().ToString('o');"
        "timestamped_at_utc=$stamp.ToString('o');timestamp_eku=$tsaEku} | "
        "ConvertTo-Json -Compress }"
    )
    result = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command])
    if result.returncode != 0:
        return {"status": "check_failed", "subject": "", "thumbprint": ""}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "check_failed", "subject": "", "thumbprint": ""}
    if not isinstance(value, dict):
        return {"status": "check_failed", "subject": "", "thumbprint": ""}
    return {
        "status": str(value.get("status") or "check_failed"),
        "subject": str(value.get("subject") or ""),
        "thumbprint": str(value.get("thumbprint") or "").upper(),
        "timestamp_subject": str(value.get("timestamp_subject") or ""),
        "timestamp_thumbprint": str(value.get("timestamp_thumbprint") or "").upper(),
        "timestamp_not_before": str(value.get("timestamp_not_before") or ""),
        "timestamp_not_after": str(value.get("timestamp_not_after") or ""),
        "timestamped_at_utc": str(value.get("timestamped_at_utc") or ""),
        "timestamp_eku": value.get("timestamp_eku") is True,
    }


def _trusted_signature(signature: dict[str, Any]) -> bool:
    timestamped_at = _recorded_at(signature.get("timestamped_at_utc"))
    timestamp_not_before = _recorded_at(signature.get("timestamp_not_before"))
    timestamp_not_after = _recorded_at(signature.get("timestamp_not_after"))
    return bool(
        signature.get("status") == "Valid"
        and EXPECTED_AUTHENTICODE_SUBJECTS
        and EXPECTED_AUTHENTICODE_THUMBPRINTS
        and signature.get("subject") in EXPECTED_AUTHENTICODE_SUBJECTS
        and signature.get("thumbprint") in EXPECTED_AUTHENTICODE_THUMBPRINTS
        and signature.get("timestamp_eku") is True
        and bool(signature.get("timestamp_subject"))
        and re.fullmatch(
            r"[A-Fa-f0-9]{40,64}", str(signature.get("timestamp_thumbprint") or "")
        ) is not None
        and timestamped_at is not None
        and timestamp_not_before is not None
        and timestamp_not_after is not None
        and timestamp_not_before <= timestamped_at <= timestamp_not_after
    )


def _timestamp_provenance(signature: dict[str, Any]) -> dict[str, object]:
    return {
        "protocol": "RFC3161",
        "timestamped_at_utc": signature.get("timestamped_at_utc"),
        "certificate_subject": signature.get("timestamp_subject"),
        "certificate_thumbprint": signature.get("timestamp_thumbprint"),
        "certificate_not_before": signature.get("timestamp_not_before"),
        "certificate_not_after": signature.get("timestamp_not_after"),
        "timestamping_eku": signature.get("timestamp_eku") is True,
    }


def _verified_build_attestation(installer: Path, source_commit: str) -> bool:
    """Require GitHub's signed SLSA attestation for this exact release input."""
    result = _run([
        "gh", "attestation", "verify", str(installer),
        "--repo", EXPECTED_GITHUB_REPOSITORY,
        "--signer-workflow", EXPECTED_SIGNER_WORKFLOW,
        "--source-digest", source_commit,
        "--source-ref", f"refs/tags/v{APP_VERSION}",
        "--deny-self-hosted-runners",
        "--format", "json",
    ])
    if result.returncode != 0:
        return False
    try:
        attestations = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return isinstance(attestations, list) and len(attestations) > 0


def check_installer(
    installer: Path,
    checksums: Path,
    application: Path,
    provenance: Path,
    source_commit: str,
) -> list[dict[str, Any]]:
    lane = "release_artifact"
    expected_filename = f"LAC-Setup-{APP_VERSION}.exe"
    exists = installer.is_file() and installer.stat().st_size > 0 and installer.name == expected_filename
    if not exists:
        return [
            _result("installer_exists", False, "exact 2.7.0 installer is missing", lane=lane),
            _result("installer_checksum", False, "installer checksum cannot be verified", lane=lane),
            _result("installer_authenticode", False, "installer signature cannot be verified", lane=lane),
            _result("application_authenticode", False, "packaged application signature cannot be verified", lane=lane),
            _result("release_provenance", False, "release provenance cannot be verified", lane=lane),
            _result("build_provenance_attestation", False, "signed build attestation cannot be verified", lane=lane),
        ]
    actual = _sha256_file(installer)
    expected = _checksum_entry(checksums, installer.name)
    checksum_ok = expected is not None and expected == actual
    installer_signature = _authenticode(installer)
    application_exists = application.is_file() and application.stat().st_size > 0
    application_signature = _authenticode(application) if application_exists else {
        "status": "missing", "subject": "", "thumbprint": "",
    }
    application_sha256 = _sha256_file(application) if application_exists else ""
    provenance_ok = False
    try:
        record = json.loads(provenance.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
        installer_record = record.get("installer") if isinstance(record, dict) else None
        application_record = record.get("application") if isinstance(record, dict) else None
        provenance_ok = bool(
            isinstance(record, dict)
            and record.get("schema_version") == 1
            and record.get("version") == APP_VERSION
            and record.get("tag") == f"v{APP_VERSION}"
            and record.get("source_commit") == source_commit
            and isinstance(installer_record, dict)
            and installer_record.get("filename") == expected_filename
            and installer_record.get("bytes") == installer.stat().st_size
            and str(installer_record.get("sha256") or "").upper() == actual
            and installer_record.get("authenticode") == "Valid"
            and installer_record.get("rfc3161_timestamp")
            == _timestamp_provenance(installer_signature)
            and isinstance(application_record, dict)
            and application_record.get("filename") == "lac.exe"
            and application_record.get("bytes") == application.stat().st_size
            and str(application_record.get("sha256") or "").upper() == application_sha256
            and application_record.get("authenticode") == "Valid"
            and application_record.get("rfc3161_timestamp")
            == _timestamp_provenance(application_signature)
            and _SHA256.fullmatch(str(record.get("dependency_lock_sha256") or "")) is not None
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        provenance_ok = False
    attestation_ok = _verified_build_attestation(installer, source_commit)
    return [
        _result(
            "installer_exists",
            True,
            "2.7.0 installer exists",
            lane=lane,
            data={"size_bytes": installer.stat().st_size, "sha256": actual},
        ),
        _result(
            "installer_checksum",
            checksum_ok,
            "checksum manifest matches" if checksum_ok else "checksum manifest is missing or mismatched",
            lane=lane,
        ),
        _result(
            "installer_authenticode",
            _trusted_signature(installer_signature),
            "trusted Authenticode signer is valid" if _trusted_signature(installer_signature)
            else "Authenticode status or approved signing identity is invalid",
            lane=lane,
            data={"status": installer_signature["status"]},
        ),
        _result(
            "application_authenticode",
            _trusted_signature(application_signature),
            "packaged application has the trusted signer" if _trusted_signature(application_signature)
            else "packaged application is missing or has an unapproved signature",
            lane=lane,
            data={"status": application_signature["status"]},
        ),
        _result(
            "release_provenance",
            provenance_ok,
            "release provenance binds version, source, signatures, and installer" if provenance_ok
            else "release provenance is missing or does not bind the release",
            lane=lane,
        ),
        _result(
            "build_provenance_attestation",
            attestation_ok,
            "GitHub SLSA attestation binds the installer, source commit, tag, and workflow"
            if attestation_ok
            else "GitHub SLSA attestation is missing or does not match the approved build",
            lane=lane,
        ),
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed LAC 2.7 enterprise launch gate.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--lac-pro-root", type=Path, default=ROOT.parent / "lac-pro")
    parser.add_argument("--lac-cloud-root", type=Path, default=ROOT.parent / "lac-cloud")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path.home() / "LAC-Launch-Evidence" / f"{APP_VERSION}.json",
        help="Operator-supplied non-secret launch evidence manifest.",
    )
    parser.add_argument("--installer", type=Path, default=ROOT / "dist" / f"LAC-Setup-{APP_VERSION}.exe")
    parser.add_argument("--checksums", type=Path, default=ROOT / "dist" / "SHA256SUMS.txt")
    parser.add_argument("--application", type=Path, default=ROOT / "dist" / "lac" / "lac.exe")
    parser.add_argument("--provenance", type=Path, default=ROOT / "dist" / "release-provenance.json")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source = _git(args.repo_root, "rev-parse", "HEAD")
    source_commit = source.stdout.strip() if source.returncode == 0 else ""
    cloud_source = _git(args.lac_cloud_root, "rev-parse", "HEAD")
    cloud_source_commit = cloud_source.stdout.strip() if cloud_source.returncode == 0 else ""
    checks = [
        *check_repository(
            "model_hub",
            args.repo_root,
            required_remote="https://github.com/Dkrynen/lac.git",
            base_commit=MODEL_HUB_RELEASE_BASE,
        ),
        *check_repository(
            "lac_pro",
            args.lac_pro_root,
            require_zero_remotes=True,
            base_commit=LAC_PRO_RELEASE_BASE,
        ),
        *check_repository(
            "lac_cloud",
            args.lac_cloud_root,
            required_remote="https://github.com/Acend-co/lac-cloud.git",
        ),
        *check_installer(
            args.installer,
            args.checksums,
            args.application,
            args.provenance,
            source_commit,
        ),
        *check_evidence(
            args.evidence,
            APP_VERSION,
            expected_cloud_commit=cloud_source_commit,
        ),
    ]
    failed = [row for row in checks if not row["ok"]]
    return {
        "schema_version": 1,
        "release_version": APP_VERSION,
        "ready": not failed,
        "failed_count": len(failed),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    output = json.dumps(report, indent=2, sort_keys=True)
    print(output)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
