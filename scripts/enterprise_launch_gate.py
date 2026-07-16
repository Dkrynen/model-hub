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
from datetime import datetime
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
    "hosted_agent_end_to_end",
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
RELEASE_SCOPES = ("local", "cloud")
EVIDENCE_SCHEMA_VERSION = 3
LOCAL_EVIDENCE_GATES = (
    "patent_clearance",
    "github_enterprise_controls",
    "cryptographic_review",
    "artifact_roundtrip",
    "clean_machine_signed_install",
)
EVIDENCE_GATES_BY_SCOPE = {
    "local": LOCAL_EVIDENCE_GATES,
    "cloud": REQUIRED_EVIDENCE_GATES,
}
_PLACEHOLDER = re.compile(r"(?:\btbd\b|\btodo\b|\bpending\b|replace|example)", re.IGNORECASE)
_SHA256 = re.compile(r"[A-Fa-f0-9]{64}")
_LOWER_SHA256 = re.compile(r"[a-f0-9]{64}")
_GIT_COMMIT = re.compile(r"[a-f0-9]{40}")
_WORKER_VERSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_B64URL = re.compile(r"[A-Za-z0-9_-]+")
_CAPABILITY_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RFC3339_UTC = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?Z"
)
_SSH_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}=?")
_SIGNED_STATES = frozenset({"G"})
_EVIDENCE_BASE_FIELDS = {
    "status", "approver", "reference", "recorded_at", "record_sha256",
    "signer_kid", "signature",
}
_EVIDENCE_RELEASE_BINDING_FIELDS = {
    "model_hub_commit", "lac_pro_commit", "lac_cloud_commit",
    "installer_sha256", "release_provenance_sha256",
}
_EVIDENCE_RECORD_FIELDS = _EVIDENCE_BASE_FIELDS | _EVIDENCE_RELEASE_BINDING_FIELDS
_LOCAL_RELEASE_BINDING_FIELDS = _EVIDENCE_RELEASE_BINDING_FIELDS - {"lac_cloud_commit"}
_LOCAL_EVIDENCE_RECORD_FIELDS = _EVIDENCE_BASE_FIELDS | _LOCAL_RELEASE_BINDING_FIELDS
_WORKER_BINDING_FIELDS = {
    "api_version_id", "agent_version_id", "runner_version_id",
}
_WORKER_EVIDENCE_FIELDS = _EVIDENCE_RECORD_FIELDS | _WORKER_BINDING_FIELDS
_MEASURED_WORKER_EVIDENCE_FIELDS = _WORKER_EVIDENCE_FIELDS | {"measured_at"}
_HOSTED_JOURNEY_DIGEST_FIELDS = {
    "journey_manifest_sha256", "price_card_payload_sha256",
    "provider_meter_sha256", "infrastructure_meter_sha256",
}
_HOSTED_JOURNEY_EVIDENCE_FIELDS = (
    _MEASURED_WORKER_EVIDENCE_FIELDS | _HOSTED_JOURNEY_DIGEST_FIELDS
)
_PRODUCTION_DEPLOYMENT_EVIDENCE_GATES = frozenset({
    "cloud_production_dark_smoke",
    "regional_latency_slo",
    "hosted_agent_end_to_end",
})
_WORKER_BOUND_EVIDENCE_GATES = (
    _PRODUCTION_DEPLOYMENT_EVIDENCE_GATES | {"cloud_staging_smoke"}
)
_PROVENANCE_FIELDS = {
    "schema_version", "version", "tag", "source_commit", "built_at_utc",
    "dependency_lock_sha256", "python_version", "pyinstaller_version",
    "installer", "application", "python_sbom", "web_sbom",
}
_SIGNED_ARTIFACT_FIELDS = {
    "filename", "bytes", "sha256", "authenticode", "rfc3161_timestamp",
}
_SBOM_FIELDS = {"filename", "bytes", "sha256"}
PROVENANCE_MAX_AGE_DAYS = 14
EVIDENCE_MANIFEST_MAX_BYTES = 1024 * 1024
EVIDENCE_OBJECT_MAX_BYTES = 256 * 1024

# Immutable disclosure-freeze bases. Only commits after these reviewed objects
# belong to the 2.7.0 launch range. Changing a local upstream cannot narrow it.
MODEL_HUB_RELEASE_BASE = (
    "c84d0fffae638664c6887b5786645cd4055d5c45"  # pragma: allowlist secret -- public Git commit
)
LAC_PRO_RELEASE_BASE = (
    "138898dac87a5b9ce0df4d4a4c0169f2d27a7fff"  # pragma: allowlist secret -- private local Git commit
)

# Trust roots onboarded 2026-07-14 (reviewed commits). Commit-signer
# allowlists are scoped per repository: a repository name without an entry
# resolves to an empty allowlist and fails closed, and release tags verify
# against the model_hub set only, because only that lane requests tag
# checks. Authenticode allowlists stay empty until the signing certificate
# exists; an empty allowlist fails closed.
_SIGNER_DKRYNEN = "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c"
_SIGNER_ARQUD = "SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI"
TRUSTED_COMMIT_SIGNERS_BY_REPO: dict[str, frozenset[str]] = {
    "model_hub": frozenset({_SIGNER_DKRYNEN}),
    "lac_pro": frozenset({_SIGNER_DKRYNEN}),
    "lac_cloud": frozenset({_SIGNER_DKRYNEN, _SIGNER_ARQUD}),
}
TRUSTED_EVIDENCE_SIGNERS: dict[str, dict[str, object]] = {
    "duan-review-2026": {
        # Ed25519 evidence-review public key (private key held offline).
        "public_key": (
            "I0_r-R-qvNacNY5jzOLX6-C5vQZZSee0TRVOxeFT0cI"  # pragma: allowlist secret -- Ed25519 public key, not a secret
        ),
        "approvers": ["duan-krynen"],
        "gates": list(REQUIRED_EVIDENCE_GATES),
        "not_before": 1_782_864_000,  # 2026-07-01T00:00:00Z
        "not_after": 1_846_022_400,  # 2028-07-01T00:00:00Z
    },
}
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
    "hosted_agent_end_to_end": 1,
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
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(
            args,
            124,
            "",
            "command unavailable or timed out",
        )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args])


def _quiet_git_success(result: subprocess.CompletedProcess[str]) -> bool:
    """Require a successful Git command with no ignored diagnostics."""
    return result.returncode == 0 and not result.stderr.strip()


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


def _rfc3339_utc_timestamp(value: object) -> float | None:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or _RFC3339_UTC.fullmatch(value) is None
    ):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except ValueError:
        return None


def _normalise_signer(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if re.fullmatch(r"[A-Fa-f0-9]{40,64}", candidate):
        return candidate.upper()
    if _SSH_FINGERPRINT.fullmatch(candidate):
        return candidate
    return ""


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


def evidence_signature_payload(
    gate: str, release_scope: str, release_version: str, record: dict[str, Any],
) -> bytes:
    signed = {key: value for key, value in record.items() if key != "signature"}
    return json.dumps(
        {
            "gate": gate,
            "record": signed,
            "release_scope": release_scope,
            "release_version": release_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _verify_evidence_record(
    name: str,
    release_scope: str,
    release_version: str,
    record: object,
    *,
    expected_model_hub_commit: str,
    expected_lac_pro_commit: str,
    expected_lac_cloud_commit: str,
    expected_installer_sha256: str,
    expected_provenance_sha256: str,
    now: float,
) -> bool:
    if release_scope == "local":
        expected_fields = _LOCAL_EVIDENCE_RECORD_FIELDS
    else:
        expected_fields = (
            _HOSTED_JOURNEY_EVIDENCE_FIELDS if name == "hosted_agent_end_to_end"
            else _MEASURED_WORKER_EVIDENCE_FIELDS if name == "regional_latency_slo"
            else _WORKER_EVIDENCE_FIELDS if name in _WORKER_BOUND_EVIDENCE_GATES
            else _EVIDENCE_RECORD_FIELDS
        )
    if not isinstance(record, dict) or set(record) != expected_fields:
        return False
    expected_commits = {
        "model_hub_commit": expected_model_hub_commit,
        "lac_pro_commit": expected_lac_pro_commit,
    }
    if release_scope != "local":
        expected_commits["lac_cloud_commit"] = expected_lac_cloud_commit
    expected_artifacts = {
        "installer_sha256": expected_installer_sha256,
        "release_provenance_sha256": expected_provenance_sha256,
    }
    if (
        any(_GIT_COMMIT.fullmatch(value) is None for value in expected_commits.values())
        or any(_LOWER_SHA256.fullmatch(value) is None for value in expected_artifacts.values())
        or any(record.get(field) != value for field, value in expected_commits.items())
        or any(record.get(field) != value for field, value in expected_artifacts.items())
    ):
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
    if name in _WORKER_BOUND_EVIDENCE_GATES:
        if any(
            not isinstance(record.get(field), str)
            or _WORKER_VERSION_ID.fullmatch(record[field]) is None
            for field in _WORKER_BINDING_FIELDS
        ):
            return False
    if name in {"regional_latency_slo", "hosted_agent_end_to_end"}:
        measured_at = _recorded_at(record.get("measured_at"))
        if (
            measured_at is None
            or measured_at > now + 300
            or measured_at > recorded_at + 300
            or now - measured_at > max_age
        ):
            return False
    if name == "hosted_agent_end_to_end":
        digests = [record.get(field) for field in _HOSTED_JOURNEY_DIGEST_FIELDS]
        if (
            any(not isinstance(digest, str) or _LOWER_SHA256.fullmatch(digest) is None
                for digest in digests)
            or len(set(digests)) != len(digests)
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
            evidence_signature_payload(name, release_scope, release_version, record),
        )
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def _content_addressed_evidence_object_valid(evidence_dir: Path, digest: str) -> bool:
    if _LOWER_SHA256.fullmatch(digest) is None:
        return False
    objects_dir = evidence_dir / "objects"
    object_path = objects_dir / f"{digest}.json"
    try:
        if (
            objects_dir.is_symlink()
            or object_path.is_symlink()
            or not object_path.is_file()
        ):
            return False
        size = object_path.stat().st_size
        if size <= 0 or size > EVIDENCE_OBJECT_MAX_BYTES:
            return False
        payload = object_path.read_bytes()
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            return False
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and bool(value)


def _hosted_journey_objects_valid(evidence_path: Path, record: object) -> bool:
    if not isinstance(record, dict):
        return False
    return all(
        isinstance(digest := record.get(field), str)
        and _content_addressed_evidence_object_valid(evidence_path.parent, digest)
        for field in _HOSTED_JOURNEY_DIGEST_FIELDS
    )


def check_evidence(
    path: Path,
    release_scope: str,
    expected_version: str,
    *,
    expected_model_hub_commit: str = "",
    expected_lac_pro_commit: str = "",
    expected_lac_cloud_commit: str = "",
    expected_installer_sha256: str = "",
    expected_provenance_sha256: str = "",
    now: float | None = None,
) -> list[dict[str, Any]]:
    required = EVIDENCE_GATES_BY_SCOPE[release_scope]
    missing = [
        _result(
            f"evidence_{name}",
            False,
            "evidence manifest is missing",
            lane="external_evidence",
        )
        for name in required
    ]
    try:
        if (
            not path.is_file()
            or path.stat().st_size <= 0
            or path.stat().st_size > EVIDENCE_MANIFEST_MAX_BYTES
        ):
            return missing
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return [
            {**row, "detail": "evidence manifest is invalid"}
            for row in missing
        ]
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "release_scope", "release_version", "gates",
    }:
        return [{**row, "detail": "evidence manifest is invalid"} for row in missing]
    version_ok = (
        document.get("schema_version") == EVIDENCE_SCHEMA_VERSION
        and document.get("release_scope") == release_scope
        and document.get("release_version") == expected_version
    )
    gates = document.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(required):
        gates = {}
    current = time.time() if now is None else now
    verified: dict[str, bool] = {}
    for name in required:
        record = gates.get(name)
        verified[name] = version_ok and _verify_evidence_record(
            name,
            release_scope,
            expected_version,
            record,
            expected_model_hub_commit=expected_model_hub_commit,
            expected_lac_pro_commit=expected_lac_pro_commit,
            expected_lac_cloud_commit=expected_lac_cloud_commit,
            expected_installer_sha256=expected_installer_sha256,
            expected_provenance_sha256=expected_provenance_sha256,
            now=current,
        )
    if release_scope == "cloud":
        if verified.get("hosted_agent_end_to_end") and not _hosted_journey_objects_valid(
            path, gates.get("hosted_agent_end_to_end"),
        ):
            verified["hosted_agent_end_to_end"] = False
        deployment_records = [
            gates.get(name) for name in _PRODUCTION_DEPLOYMENT_EVIDENCE_GATES
        ]
        if all(verified.get(name) for name in _PRODUCTION_DEPLOYMENT_EVIDENCE_GATES):
            expected_binding = tuple(
                deployment_records[0].get(field) for field in _WORKER_BINDING_FIELDS
            )
            bindings_match = all(
                tuple(record.get(field) for field in _WORKER_BINDING_FIELDS)
                == expected_binding
                for record in deployment_records[1:]
            )
            if not bindings_match:
                for name in _PRODUCTION_DEPLOYMENT_EVIDENCE_GATES:
                    verified[name] = False
    rows: list[dict[str, Any]] = []
    for name in required:
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


def check_cloud_product_readiness(lac_cloud_root: Path) -> dict[str, Any]:
    """Require the private cloud repo's exact strict hosted-product truth gate."""
    lane = "cloud_product"
    script = lac_cloud_root / "scripts" / "product-readiness.mjs"
    if not script.is_file():
        return _result(
            "cloud_product_local_complete",
            False,
            "hosted-product readiness gate is missing",
            lane=lane,
        )
    detail = "hosted-agent product capabilities remain incomplete or unverified"
    diagnostic_validated = False
    diagnostic_missing: list[str] = []
    try:
        result = _run(
            ["node", str(script), "--require-hosted-agent-local-complete"],
            cwd=lac_cloud_root,
        )
        if len(result.stdout.encode("utf-8")) > 4_096:
            raise ValueError("readiness report is oversized")
        report = json.loads(result.stdout, object_pairs_hook=_unique_object)
        exact_schema = bool(
            isinstance(report, dict)
            and set(report) == {
                "schemaVersion", "valid", "localEngineeringReady", "status",
                "missingCapabilities",
            }
        )
        missing = report.get("missingCapabilities") if exact_schema else None
        explainable_incomplete = bool(
            result.returncode == 1
            and exact_schema
            and type(report.get("schemaVersion")) is int
            and report.get("schemaVersion") == 1
            and type(report.get("valid")) is bool
            and report.get("localEngineeringReady") is False
            and report.get("status") == (
                "platform_foundation_complete" if report.get("valid") else "invalid"
            )
            and isinstance(missing, list)
            and 0 < len(missing) <= 64
            and all(isinstance(item, str) and _CAPABILITY_ID.fullmatch(item) for item in missing)
            and len(missing) == len(set(missing))
        )
        if explainable_incomplete:
            prefix = (
                "missing hosted-agent capabilities"
                if report["valid"] else "missing or unverified hosted-agent capabilities"
            )
            detail = f"{prefix}: {', '.join(missing)}"
            diagnostic_validated = True
            diagnostic_missing = list(missing)
        ready = bool(
            result.returncode == 0
            and exact_schema
            and type(report.get("schemaVersion")) is int
            and report.get("schemaVersion") == 1
            and report.get("valid") is True
            and report.get("localEngineeringReady") is True
            and report.get("status") == "hosted_agent_local_complete"
            and report.get("missingCapabilities") == []
        )
        if ready:
            diagnostic_validated = True
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ):
        ready = False
    return _result(
        "cloud_product_local_complete",
        ready,
        "all required hosted-agent capabilities are locally complete"
        if ready else detail,
        lane=lane,
        data={
            "diagnostic_validated": diagnostic_validated,
            "missing_capabilities": diagnostic_missing,
        },
    )


def check_repository(
    name: str,
    path: Path,
    *,
    require_zero_remotes: bool = False,
    required_remote: str | None = None,
    base_commit: str | None = None,
    release_tag: str | None = None,
    expected_tag_target: str | None = None,
) -> list[dict[str, Any]]:
    lane = "repositories"
    if not (path / ".git").exists():
        rows = [
            _result(f"{name}_exists", False, "Git repository is missing", lane=lane),
            _result(f"{name}_clean", False, "Git repository is missing", lane=lane),
            _result(f"{name}_signed_commits", False, "Git repository is missing", lane=lane),
            _result(f"{name}_remote", False, "Git repository is missing", lane=lane),
        ]
        if release_tag is not None:
            rows.append(_result(
                f"{name}_signed_release_tag",
                False,
                "Git repository is missing",
                lane=lane,
            ))
        return rows

    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    status_ok = _quiet_git_success(status)
    dirty_count = len([line for line in status.stdout.splitlines() if line]) if status_ok else -1
    clean = status_ok and dirty_count == 0

    base_ok = True
    revision = "HEAD"
    if base_commit:
        base_exists = _git(path, "cat-file", "-e", f"{base_commit}^{{commit}}")
        ancestor = _git(path, "merge-base", "--is-ancestor", base_commit, "HEAD")
        base_ok = _quiet_git_success(base_exists) and _quiet_git_success(ancestor)
        revision = f"{base_commit}..HEAD"
    signatures = _git(path, "log", revision, "--format=%G?%x00%GF") if base_ok else None
    signature_rows: list[tuple[str, str]] = []
    if signatures is not None and _quiet_git_success(signatures):
        for line in signatures.stdout.splitlines():
            state, _, fingerprint = line.partition("\0")
            if state:
                signature_rows.append((state, _normalise_signer(fingerprint)))
    trusted_signers = {
        normalised
        for signer in TRUSTED_COMMIT_SIGNERS_BY_REPO.get(name, frozenset())
        if (normalised := _normalise_signer(signer))
    }
    unsigned_count = sum(state not in _SIGNED_STATES for state, _ in signature_rows)
    untrusted_count = sum(
        state in _SIGNED_STATES and fingerprint not in trusted_signers
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
    remotes_ok = _quiet_git_success(remotes)
    remote_count = len({name for name, _, _ in remote_records}) if remotes_ok else -1
    if require_zero_remotes:
        remote_ok = remotes_ok and remote_count == 0
        remote_detail = "repository has zero remotes" if remote_ok else "local-only repository has a remote"
    elif required_remote:
        expected = {
            ("origin", required_remote, "fetch"),
            ("origin", required_remote, "push"),
        }
        remote_ok = remotes_ok and remote_records == expected
        remote_detail = "exact approved remote is configured" if remote_ok else "remote set differs from the approved contract"
    else:
        remote_ok = remotes_ok and remote_count > 0
        remote_detail = "repository has a remote" if remote_ok else "repository remote is missing"

    rows = [
        _result(f"{name}_exists", True, "Git repository exists", lane=lane),
        _result(
            f"{name}_clean",
            clean,
            "worktree is clean" if clean else (
                "worktree status could not be verified" if not status_ok else "worktree has changes"
            ),
            lane=lane,
            data={"dirty_count": dirty_count},
        ),
        _result(
            f"{name}_signed_commits",
            signed_ok,
            "release-range commits are signed" if signed_ok else (
                "release-range signature status could not be verified"
                if signatures is None or not _quiet_git_success(signatures)
                else "release-range contains unsigned commits"
            ),
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
    if release_tag is not None:
        tag_ref = f"refs/tags/{release_tag}"
        tag_type = _git(path, "cat-file", "-t", tag_ref)
        tag_object = _git(path, "cat-file", "tag", tag_ref)
        tag_target = _git(path, "rev-parse", f"{tag_ref}^{{commit}}")
        verification = _git(path, "verify-tag", "--raw", tag_ref)
        verification_output = f"{verification.stdout}\n{verification.stderr}"
        gpg_signers = re.findall(
            r"(?m)^\[GNUPG:\] VALIDSIG ([A-Fa-f0-9]{40,64})\b",
            verification_output,
        )
        adverse_gpg_status = re.search(
            r"(?m)^\[GNUPG:\] (?:BADSIG|ERRSIG|EXPKEYSIG|KEYEXPIRED|"
            r"NO_PUBKEY|REVKEYSIG|SIGEXPIRED)\b",
            verification_output,
        ) is not None
        ssh_signers = re.findall(
            r'(?m)^Good "git" signature for .+ with \S+ key (SHA256:[A-Za-z0-9+/]{43}=?)\s*$',
            verification_output,
        )
        tag_signers = {
            normalised
            for signer in [*gpg_signers, *ssh_signers]
            if (normalised := _normalise_signer(signer))
        }
        tag_header = tag_object.stdout.split("\n\n", 1)[0] if _quiet_git_success(tag_object) else ""
        embedded_tag_names = [
            line.removeprefix("tag ")
            for line in tag_header.splitlines()
            if line.startswith("tag ")
        ]
        embedded_name_matches = embedded_tag_names == [release_tag]
        tag_ok = bool(
            re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", release_tag)
            and _GIT_COMMIT.fullmatch(str(expected_tag_target or ""))
            and tag_type.returncode == 0
            and tag_type.stdout.strip() == "tag"
            and embedded_name_matches
            and tag_target.returncode == 0
            and tag_target.stdout.strip() == expected_tag_target
            and verification.returncode == 0
            and not adverse_gpg_status
            and len(tag_signers) == 1
            and tag_signers <= trusted_signers
        )
        rows.append(_result(
            f"{name}_signed_release_tag",
            tag_ok,
            "annotated release tag has the exact name, targets HEAD, and has an approved signature"
            if tag_ok
            else "release tag is missing, lightweight, misnamed, mistargeted, unsigned, or untrusted",
            lane=lane,
            data={
                "annotated": tag_type.returncode == 0 and tag_type.stdout.strip() == "tag",
                "embedded_name_matches": embedded_name_matches,
                "targets_expected_commit": (
                    tag_target.returncode == 0
                    and tag_target.stdout.strip() == expected_tag_target
                ),
                "trusted_signature_count": len(tag_signers & trusted_signers),
            },
        ))
    return rows


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _evidence_subject_sha256(path: Path, *, max_bytes: int | None = None) -> str:
    try:
        if not path.is_file():
            return ""
        size = path.stat().st_size
        if size <= 0 or (max_bytes is not None and size > max_bytes):
            return ""
        return _sha256_file(path).lower()
    except OSError:
        return ""


def _checksum_entry(path: Path, filename: str) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return None
    entries: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line.strip():
            continue
        parts = raw_line.split(maxsplit=1)
        if len(parts) != 2 or _SHA256.fullmatch(parts[0]) is None:
            return None
        entry_name = parts[1].strip()
        if entry_name.startswith("*"):
            entry_name = entry_name[1:]
        if not entry_name or entry_name in entries:
            return None
        entries[entry_name] = parts[0].upper()
    return entries.get(filename)


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


def _verified_build_attestations(
    subjects: tuple[Path, ...], source_commit: str,
) -> bool:
    """Require GitHub's signed SLSA attestation for every exact release subject."""
    if (
        not subjects
        or len(set(subjects)) != len(subjects)
        or _GIT_COMMIT.fullmatch(source_commit) is None
    ):
        return False
    for subject in subjects:
        if not subject.is_file() or subject.stat().st_size <= 0:
            return False
        result = _run([
            "gh", "attestation", "verify", str(subject),
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
        if not isinstance(attestations, list) or not attestations:
            return False
    return True


def _positive_file_size(value: object, path: Path) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
        and path.is_file()
        and value == path.stat().st_size
    )


def _bounded_version(value: object) -> bool:
    return bool(
        _nonplaceholder(value)
        and isinstance(value, str)
        and len(value) <= 128
        and re.fullmatch(r"[ -~]+", value)
    )


def _signed_artifact_binding(
    record: object,
    *,
    path: Path,
    filename: str,
    signature: dict[str, Any],
) -> bool:
    return bool(
        isinstance(record, dict)
        and set(record) == _SIGNED_ARTIFACT_FIELDS
        and record.get("filename") == filename
        and _positive_file_size(record.get("bytes"), path)
        and record.get("sha256") == _sha256_file(path).lower()
        and record.get("authenticode") == "Valid"
        and record.get("rfc3161_timestamp") == _timestamp_provenance(signature)
    )


def _sbom_binding(record: object, *, path: Path, filename: str) -> bool:
    return bool(
        isinstance(record, dict)
        and set(record) == _SBOM_FIELDS
        and record.get("filename") == filename
        and _positive_file_size(record.get("bytes"), path)
        and record.get("sha256") == _sha256_file(path).lower()
    )


def _release_provenance_valid(
    record: object,
    *,
    installer: Path,
    application: Path,
    dependency_lock: Path,
    python_sbom: Path,
    web_sbom: Path,
    source_commit: str,
    installer_signature: dict[str, Any],
    application_signature: dict[str, Any],
    now: float,
) -> bool:
    if not isinstance(record, dict) or set(record) != _PROVENANCE_FIELDS:
        return False
    built_at = _rfc3339_utc_timestamp(record.get("built_at_utc"))
    installer_signed_at = _rfc3339_utc_timestamp(
        installer_signature.get("timestamped_at_utc")
    )
    application_signed_at = _rfc3339_utc_timestamp(
        application_signature.get("timestamped_at_utc")
    )
    if (
        built_at is None
        or built_at > now + 300
        or now - built_at > PROVENANCE_MAX_AGE_DAYS * 86_400
        or installer_signed_at is None
        or application_signed_at is None
        or not 0 <= built_at - installer_signed_at <= 86_400
        or not 0 <= built_at - application_signed_at <= 86_400
        or dependency_lock.name != "requirements-release.lock"
        or not dependency_lock.is_file()
        or dependency_lock.stat().st_size <= 0
        or python_sbom.name != "python-sbom.json"
        or web_sbom.name != "web-sbom.json"
    ):
        return False
    return bool(
        record.get("schema_version") == 2
        and record.get("version") == APP_VERSION
        and record.get("tag") == f"v{APP_VERSION}"
        and _GIT_COMMIT.fullmatch(source_commit) is not None
        and record.get("source_commit") == source_commit
        and record.get("dependency_lock_sha256") == _sha256_file(dependency_lock).lower()
        and _bounded_version(record.get("python_version"))
        and _bounded_version(record.get("pyinstaller_version"))
        and _signed_artifact_binding(
            record.get("installer"),
            path=installer,
            filename=f"LAC-Setup-{APP_VERSION}.exe",
            signature=installer_signature,
        )
        and _signed_artifact_binding(
            record.get("application"),
            path=application,
            filename="lac.exe",
            signature=application_signature,
        )
        and _sbom_binding(
            record.get("python_sbom"), path=python_sbom, filename="python-sbom.json"
        )
        and _sbom_binding(
            record.get("web_sbom"), path=web_sbom, filename="web-sbom.json"
        )
    )


def check_installer(
    installer: Path,
    checksums: Path,
    application: Path,
    provenance: Path,
    source_commit: str,
    dependency_lock: Path | None = None,
    python_sbom: Path | None = None,
    web_sbom: Path | None = None,
    *,
    now: float | None = None,
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
    dependency_lock = dependency_lock or ROOT / "requirements-release.lock"
    python_sbom = python_sbom or ROOT / "dist" / "python-sbom.json"
    web_sbom = web_sbom or ROOT / "dist" / "web-sbom.json"
    actual = _sha256_file(installer)
    expected = _checksum_entry(checksums, installer.name)
    checksum_ok = expected is not None and expected == actual
    installer_signature = _authenticode(installer)
    application_exists = application.is_file() and application.stat().st_size > 0
    application_signature = _authenticode(application) if application_exists else {
        "status": "missing", "subject": "", "thumbprint": "",
    }
    provenance_ok = False
    try:
        if not provenance.is_file() or provenance.stat().st_size > 256 * 1024:
            raise ValueError("release provenance is missing or oversized")
        record = json.loads(
            provenance.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_unique_object,
        )
        provenance_ok = _release_provenance_valid(
            record,
            installer=installer,
            application=application,
            dependency_lock=dependency_lock,
            python_sbom=python_sbom,
            web_sbom=web_sbom,
            source_commit=source_commit,
            installer_signature=installer_signature,
            application_signature=application_signature,
            now=time.time() if now is None else now,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        provenance_ok = False
    attestation_ok = _verified_build_attestations(
        (installer, application, checksums, provenance, python_sbom, web_sbom),
        source_commit,
    )
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
            "GitHub SLSA attestations bind every release subject, source commit, tag, and workflow"
            if attestation_ok
            else "one or more GitHub SLSA attestations are missing or do not match the approved build",
            lane=lane,
        ),
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed LAC 2.7 enterprise launch gate.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--release-scope",
        choices=RELEASE_SCOPES,
        default="cloud",
        help="Which release this run authorizes: the local installer release "
        "or the full cloud launch.",
    )
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
    parser.add_argument("--python-sbom", type=Path, default=ROOT / "dist" / "python-sbom.json")
    parser.add_argument("--web-sbom", type=Path, default=ROOT / "dist" / "web-sbom.json")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    release_scope = args.release_scope
    source = _git(args.repo_root, "rev-parse", "HEAD")
    source_commit = source.stdout.strip() if source.returncode == 0 else ""
    pro_source = _git(args.lac_pro_root, "rev-parse", "HEAD")
    pro_source_commit = pro_source.stdout.strip() if pro_source.returncode == 0 else ""
    cloud_source_commit = ""
    installer_sha256 = _evidence_subject_sha256(args.installer)
    provenance_sha256 = _evidence_subject_sha256(
        args.provenance, max_bytes=256 * 1024,
    )
    checks = [
        *check_repository(
            "model_hub",
            args.repo_root,
            required_remote="https://github.com/Dkrynen/lac.git",
            base_commit=MODEL_HUB_RELEASE_BASE,
            release_tag=f"v{APP_VERSION}",
            expected_tag_target=source_commit,
        ),
        *check_repository(
            "lac_pro",
            args.lac_pro_root,
            require_zero_remotes=True,
            base_commit=LAC_PRO_RELEASE_BASE,
        ),
    ]
    if release_scope == "cloud":
        cloud_source = _git(args.lac_cloud_root, "rev-parse", "HEAD")
        cloud_source_commit = (
            cloud_source.stdout.strip() if cloud_source.returncode == 0 else ""
        )
        checks += [
            *check_repository(
                "lac_cloud",
                args.lac_cloud_root,
                required_remote="https://github.com/Acend-co/lac-cloud.git",
            ),
            check_cloud_product_readiness(args.lac_cloud_root),
        ]
    checks += [
        *check_installer(
            args.installer,
            args.checksums,
            args.application,
            args.provenance,
            source_commit,
            args.repo_root / "requirements-release.lock",
            args.python_sbom,
            args.web_sbom,
        ),
        *check_evidence(
            args.evidence,
            release_scope,
            APP_VERSION,
            expected_model_hub_commit=source_commit,
            expected_lac_pro_commit=pro_source_commit,
            expected_lac_cloud_commit=cloud_source_commit,
            expected_installer_sha256=installer_sha256,
            expected_provenance_sha256=provenance_sha256,
        ),
    ]
    failed = [row for row in checks if not row["ok"]]
    return {
        "schema_version": 2,
        "release_scope": release_scope,
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
