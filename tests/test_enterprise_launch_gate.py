from __future__ import annotations

import importlib.util
import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "enterprise_launch_gate.py"
MODEL_HUB_COMMIT = "a" * 40
LAC_PRO_COMMIT = "b" * 40
CLOUD_COMMIT = "c" * 40
INSTALLER_SHA256 = "d" * 64
PROVENANCE_SHA256 = "e" * 64
NOW = 1_783_944_000.0
PRODUCTION_VERSIONS = {
    "api_version_id": "11111111-1111-1111-1111-111111111111",
    "agent_version_id": "22222222-2222-2222-2222-222222222222",
    "runner_version_id": "33333333-3333-3333-3333-333333333333",
}
STAGING_VERSIONS = {
    "api_version_id": "44444444-4444-4444-4444-444444444444",
    "agent_version_id": "55555555-5555-5555-5555-555555555555",
    "runner_version_id": "66666666-6666-6666-6666-666666666666",
}


def _load_gate():
    spec = importlib.util.spec_from_file_location("enterprise_launch_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "--initial-branch=master")
    _git(repo, "config", "user.name", "Launch Gate Test")
    _git(repo, "config", "user.email", "launch-gate@example.test")
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "unsigned fixture")
    return repo


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_content_object(objects: Path, value: object) -> str:
    objects.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    (objects / f"{digest}.json").write_bytes(payload)
    return digest


def _write_hosted_evidence_objects(evidence_path: Path) -> dict[str, str]:
    objects = evidence_path.parent / "objects"
    return {
        "journey_manifest_sha256": _write_content_object(
            objects, {"kind": "journey_manifest", "schema_version": 1},
        ),
        "price_card_payload_sha256": _write_content_object(
            objects, {"kind": "signed_price_card", "schema_version": 2},
        ),
        "provider_meter_sha256": _write_content_object(
            objects, {"kind": "provider_meter", "schema_version": 1},
        ),
        "infrastructure_meter_sha256": _write_content_object(
            objects, {"kind": "infrastructure_meter", "schema_version": 1},
        ),
    }


def _trust_evidence_signer(gate, private_key, monkeypatch) -> None:
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(gate, "TRUSTED_EVIDENCE_SIGNERS", {
        "test-reviewer-2026": {
            "public_key": base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii"),
            "approvers": ["independent-reviewer"],
            "gates": list(gate.REQUIRED_EVIDENCE_GATES),
            "not_before": 1_700_000_000,
            "not_after": 1_900_000_000,
        },
    })


def _check_evidence(
    gate, path: Path, *, release_scope: str = "cloud",
    expected_version: str = "2.7.0", **overrides,
):
    expected = {
        "expected_model_hub_commit": MODEL_HUB_COMMIT,
        "expected_lac_pro_commit": LAC_PRO_COMMIT,
        "expected_lac_cloud_commit": CLOUD_COMMIT,
        "expected_installer_sha256": INSTALLER_SHA256,
        "expected_provenance_sha256": PROVENANCE_SHA256,
        "now": NOW,
    }
    expected.update(overrides)
    return gate.check_evidence(path, release_scope, expected_version, **expected)


def _release_fixture(tmp_path: Path, gate) -> dict[str, object]:
    installer = tmp_path / "LAC-Setup-2.7.0.exe"
    application = tmp_path / "lac.exe"
    dependency_lock = tmp_path / "requirements-release.lock"
    python_sbom = tmp_path / "python-sbom.json"
    web_sbom = tmp_path / "web-sbom.json"
    checksums = tmp_path / "SHA256SUMS.txt"
    provenance = tmp_path / "release-provenance.json"
    installer.write_bytes(b"signed installer")
    application.write_bytes(b"signed application")
    dependency_lock.write_text("locked-dependency==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    python_sbom.write_text('{"bomFormat":"CycloneDX","component":"python"}\n', encoding="utf-8")
    web_sbom.write_text('{"bomFormat":"CycloneDX","component":"web"}\n', encoding="utf-8")
    checksums.write_text(f"{_sha256(installer)}  {installer.name}\n", encoding="ascii")
    signature = {
        "status": "Valid",
        "subject": "CN=LAC",
        "thumbprint": "A" * 40,
        "timestamp_subject": "CN=Trusted TSA",
        "timestamp_thumbprint": "B" * 40,
        "timestamp_not_before": "2026-01-01T00:00:00.0000000Z",
        "timestamp_not_after": "2027-01-01T00:00:00.0000000Z",
        "timestamped_at_utc": "2026-07-13T00:00:00.0000000Z",
        "timestamp_eku": True,
    }
    record = {
        "schema_version": 2,
        "version": "2.7.0",
        "tag": "v2.7.0",
        "source_commit": "a" * 40,
        "built_at_utc": "2026-07-13T00:00:01.0000000Z",
        "dependency_lock_sha256": _sha256(dependency_lock),
        "python_version": "Python 3.13.5",
        "pyinstaller_version": "6.14.2",
        "installer": {
            "filename": installer.name,
            "bytes": installer.stat().st_size,
            "sha256": _sha256(installer),
            "authenticode": "Valid",
            "rfc3161_timestamp": gate._timestamp_provenance(signature),
        },
        "application": {
            "filename": application.name,
            "bytes": application.stat().st_size,
            "sha256": _sha256(application),
            "authenticode": "Valid",
            "rfc3161_timestamp": gate._timestamp_provenance(signature),
        },
        "python_sbom": {
            "filename": python_sbom.name,
            "bytes": python_sbom.stat().st_size,
            "sha256": _sha256(python_sbom),
        },
        "web_sbom": {
            "filename": web_sbom.name,
            "bytes": web_sbom.stat().st_size,
            "sha256": _sha256(web_sbom),
        },
    }
    provenance.write_text(json.dumps(record), encoding="utf-8")
    return {
        "installer": installer,
        "checksums": checksums,
        "application": application,
        "provenance": provenance,
        "dependency_lock": dependency_lock,
        "python_sbom": python_sbom,
        "web_sbom": web_sbom,
        "signature": signature,
        "record": record,
    }


def _check_release_fixture(gate, fixture: dict[str, object], monkeypatch):
    monkeypatch.setattr(gate, "_authenticode", lambda path: fixture["signature"])
    monkeypatch.setattr(gate, "_verified_build_attestations", lambda subjects, source_commit: True)
    return gate.check_installer(
        fixture["installer"],
        fixture["checksums"],
        fixture["application"],
        fixture["provenance"],
        "a" * 40,
        fixture["dependency_lock"],
        fixture["python_sbom"],
        fixture["web_sbom"],
        now=NOW,
    )


def _valid_evidence(
    gate,
    private_key: Ed25519PrivateKey,
    *,
    hosted_digests: dict[str, str] | None = None,
    release_scope: str = "cloud",
    model_hub_commit: str = MODEL_HUB_COMMIT,
    lac_pro_commit: str = LAC_PRO_COMMIT,
    lac_cloud_commit: str = CLOUD_COMMIT,
    installer_sha256: str = INSTALLER_SHA256,
    provenance_sha256: str = PROVENANCE_SHA256,
    measured_at: str = "2026-07-13T00:00:00Z",
    staging_versions: dict[str, str] = STAGING_VERSIONS,
    production_versions: dict[str, str] = PRODUCTION_VERSIONS,
    regional_versions: dict[str, str] | None = None,
    hosted_versions: dict[str, str] | None = None,
) -> dict:
    document = {
        "schema_version": 3,
        "release_scope": release_scope,
        "release_version": "2.7.0",
        "gates": {},
    }
    for index, name in enumerate(
        gate.EVIDENCE_GATES_BY_SCOPE[release_scope], start=1,
    ):
        record = {
            "status": "verified",
            "approver": "independent-reviewer",
            "reference": f"review-{index:02d}",
            "recorded_at": "2026-07-13T00:00:00Z",
            "record_sha256": f"{index:064x}",
            "signer_kid": "test-reviewer-2026",
            "model_hub_commit": model_hub_commit,
            "lac_pro_commit": lac_pro_commit,
            "lac_cloud_commit": lac_cloud_commit,
            "installer_sha256": installer_sha256,
            "release_provenance_sha256": provenance_sha256,
        }
        if release_scope == "local":
            del record["lac_cloud_commit"]
        if name == "cloud_staging_smoke":
            record.update(staging_versions)
        elif name in gate._PRODUCTION_DEPLOYMENT_EVIDENCE_GATES:
            if name == "regional_latency_slo" and regional_versions is not None:
                record.update(regional_versions)
            elif name == "hosted_agent_end_to_end" and hosted_versions is not None:
                record.update(hosted_versions)
            else:
                record.update(production_versions)
        if name in {"regional_latency_slo", "hosted_agent_end_to_end"}:
            record["measured_at"] = measured_at
        if name == "hosted_agent_end_to_end":
            record.update(hosted_digests or {})
        signature = private_key.sign(
            gate.evidence_signature_payload(name, release_scope, "2.7.0", record)
        )
        record["signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        document["gates"][name] = record
    return document


def test_missing_evidence_fails_every_external_gate(tmp_path):
    gate = _load_gate()

    rows = gate.check_evidence(tmp_path / "missing.json", "cloud", "2.7.0")

    assert {row["name"] for row in rows if not row["ok"]} == {
        f"evidence_{name}" for name in gate.REQUIRED_EVIDENCE_GATES
    }
    assert all("missing" in row["detail"] for row in rows)


def test_valid_evidence_requires_scoped_signature_exact_release_and_fresh_records(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)
    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert all(row["ok"] for row in _check_evidence(gate, path))
    assert not all(row["ok"] for row in _check_evidence(
        gate, path, expected_version="2.7.1",
    ))
    evidence["schema_version"] = 1
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))
    evidence["schema_version"] = 3
    evidence["gates"]["patent_clearance"]["reference"] = "tampered-reference"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert next(
        row for row in _check_evidence(gate, path)
        if row["name"] == "evidence_patent_clearance"
    )["ok"] is False


def test_every_evidence_record_binds_exact_repositories_and_release_artifacts(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)

    invalid_bindings = (
        {"model_hub_commit": "f" * 40},
        {"lac_pro_commit": "f" * 40},
        {"lac_cloud_commit": "f" * 40},
        {"installer_sha256": "f" * 64},
        {"provenance_sha256": "f" * 64},
    )
    for override in invalid_bindings:
        evidence = _valid_evidence(
            gate, private_key, hosted_digests=digests, **override,
        )
        path.write_text(json.dumps(evidence), encoding="utf-8")
        assert all(not row["ok"] for row in _check_evidence(gate, path))


def test_staging_worker_versions_are_required_but_not_cross_matched_to_production(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)

    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")
    rows = _check_evidence(gate, path)
    assert next(row for row in rows if row["name"] == "evidence_cloud_staging_smoke")["ok"] is True
    assert all(
        next(row for row in rows if row["name"] == f"evidence_{name}")["ok"]
        for name in gate._PRODUCTION_DEPLOYMENT_EVIDENCE_GATES
    )

    invalid_staging = _valid_evidence(
        gate,
        private_key,
        hosted_digests=digests,
        staging_versions={**STAGING_VERSIONS, "api_version_id": "replace"},
    )
    path.write_text(json.dumps(invalid_staging), encoding="utf-8")
    rows = _check_evidence(gate, path)
    assert next(row for row in rows if row["name"] == "evidence_cloud_staging_smoke")["ok"] is False
    assert next(row for row in rows if row["name"] == "evidence_cloud_production_dark_smoke")["ok"] is True


def test_only_production_worker_versions_cross_match_and_measurements_stay_fresh(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)

    mismatched_runtime = _valid_evidence(
        gate,
        private_key,
        hosted_digests=digests,
        regional_versions={
            **PRODUCTION_VERSIONS,
            "runner_version_id": "77777777-7777-7777-7777-777777777777",
        },
    )
    path.write_text(json.dumps(mismatched_runtime), encoding="utf-8")
    rows = _check_evidence(gate, path)
    assert next(row for row in rows if row["name"] == "evidence_cloud_staging_smoke")["ok"] is True
    assert all(
        next(row for row in rows if row["name"] == f"evidence_{name}")["ok"] is False
        for name in gate._PRODUCTION_DEPLOYMENT_EVIDENCE_GATES
    )

    old_measurement = _valid_evidence(
        gate,
        private_key,
        hosted_digests=digests,
        measured_at="2026-06-01T00:00:00Z",
    )
    path.write_text(json.dumps(old_measurement), encoding="utf-8")
    rows = _check_evidence(gate, path)
    assert next(row for row in rows if row["name"] == "evidence_regional_latency_slo")["ok"] is False
    assert next(row for row in rows if row["name"] == "evidence_hosted_agent_end_to_end")["ok"] is False


def test_hosted_agent_journey_requires_a_valid_content_addressed_object_bundle(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)
    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    rows = _check_evidence(gate, path)
    assert next(
        row for row in rows if row["name"] == "evidence_hosted_agent_end_to_end"
    )["ok"] is True

    provider_object = path.parent / "objects" / f"{digests['provider_meter_sha256']}.json"
    provider_object.unlink()
    rows = _check_evidence(gate, path)
    assert next(
        row for row in rows if row["name"] == "evidence_hosted_agent_end_to_end"
    )["ok"] is False


def test_hosted_agent_journey_rejects_tampered_content_object(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)
    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    object_path = path.parent / "objects" / f"{digests['journey_manifest_sha256']}.json"
    object_path.write_text('{"kind":"tampered"}\n', encoding="utf-8")

    rows = _check_evidence(gate, path)
    assert next(
        row for row in rows if row["name"] == "evidence_hosted_agent_end_to_end"
    )["ok"] is False


def test_hosted_agent_journey_rejects_oversized_content_object(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)
    oversized = {"kind": "provider_meter", "padding": "x" * gate.EVIDENCE_OBJECT_MAX_BYTES}
    digests["provider_meter_sha256"] = _write_content_object(path.parent / "objects", oversized)
    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    rows = _check_evidence(gate, path)
    assert next(
        row for row in rows if row["name"] == "evidence_hosted_agent_end_to_end"
    )["ok"] is False


def test_cloud_product_readiness_requires_exact_strict_cli_success(tmp_path, monkeypatch):
    gate = _load_gate()
    cloud = tmp_path / "lac-cloud"
    script = cloud / "scripts" / "product-readiness.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture\n", encoding="utf-8")
    calls = []

    def fake_run(args, *, cwd=None):
        calls.append((args, cwd))
        report = {
            "schemaVersion": 1,
            "valid": True,
            "localEngineeringReady": True,
            "status": "hosted_agent_local_complete",
            "missingCapabilities": [],
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(report), "")

    monkeypatch.setattr(gate, "_run", fake_run)
    row = gate.check_cloud_product_readiness(cloud)

    assert row["ok"] is True
    assert calls == [([
        "node",
        str(script),
        "--require-hosted-agent-local-complete",
    ], cloud)]


def test_cloud_product_readiness_fails_closed_on_incomplete_or_ambiguous_output(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    cloud = tmp_path / "lac-cloud"
    script = cloud / "scripts" / "product-readiness.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture\n", encoding="utf-8")
    reports = [
        subprocess.CompletedProcess(["node"], 1, json.dumps({
            "schemaVersion": 1,
            "valid": True,
            "localEngineeringReady": False,
            "status": "platform_foundation_complete",
            "missingCapabilities": ["provider_metering", "provider_broker"],
        }), ""),
        subprocess.CompletedProcess(["node"], 1, json.dumps({
            "schemaVersion": 1,
            "valid": False,
            "localEngineeringReady": False,
            "status": "invalid",
            "missingCapabilities": ["provider_broker"],
        }), ""),
        subprocess.CompletedProcess(["node"], 0, json.dumps({
            "schemaVersion": 1,
            "valid": True,
            "localEngineeringReady": True,
            "status": "hosted_agent_local_complete",
            "missingCapabilities": [],
            "unexpected": True,
        }), ""),
        subprocess.CompletedProcess(["node"], 1, json.dumps({
            "schemaVersion": True,
            "valid": False,
            "localEngineeringReady": False,
            "status": "invalid",
            "missingCapabilities": ["provider_broker"],
        }), ""),
        subprocess.CompletedProcess(["node"], 0, "not-json", ""),
    ]

    details = []
    data = []
    for result in reports:
        monkeypatch.setattr(gate, "_run", lambda args, cwd=None, result=result: result)
        row = gate.check_cloud_product_readiness(cloud)
        assert row["ok"] is False
        details.append(row["detail"])
        data.append(row["data"])

    assert details[:2] == [
        "missing hosted-agent capabilities: provider_metering, provider_broker",
        "missing or unverified hosted-agent capabilities: provider_broker",
    ]
    assert details[2:] == [
        "hosted-agent product capabilities remain incomplete or unverified",
        "hosted-agent product capabilities remain incomplete or unverified",
        "hosted-agent product capabilities remain incomplete or unverified",
    ]
    assert data[0] == {
        "diagnostic_validated": True,
        "missing_capabilities": ["provider_metering", "provider_broker"],
    }
    assert data[2] == {"diagnostic_validated": False, "missing_capabilities": []}
    assert data[3] == {"diagnostic_validated": False, "missing_capabilities": []}


def test_repository_checks_report_dirty_unsigned_and_remote_policy(tmp_path):
    gate = _load_gate()
    repo = _repo(tmp_path, "private")
    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    _git(repo, "remote", "add", "origin", "https://github.com/example/wrong.git")

    rows = gate.check_repository(
        "lac_cloud",
        repo,
        required_remote="https://github.com/Acend-co/lac-cloud.git",
    )
    indexed = {row["name"]: row for row in rows}

    assert indexed["lac_cloud_clean"]["ok"] is False
    assert indexed["lac_cloud_signed_commits"]["ok"] is False
    assert indexed["lac_cloud_remote"]["ok"] is False
    assert "unsigned_count" in indexed["lac_cloud_signed_commits"]["data"]


@pytest.mark.parametrize(
    ("warning_command", "failed_check"),
    [
        ("status", "model_hub_clean"),
        ("log", "model_hub_signed_commits"),
        ("remote", "model_hub_remote"),
    ],
)
def test_repository_checks_fail_closed_on_unexpected_git_diagnostics(
    tmp_path, monkeypatch, warning_command, failed_check,
):
    gate = _load_gate()
    repo = tmp_path / "core"
    (repo / ".git").mkdir(parents=True)
    remote = "https://github.com/Dkrynen/lac.git"
    signer = next(iter(gate.TRUSTED_COMMIT_SIGNERS_BY_REPO["model_hub"]))

    def fake_git(path, *args):
        command = args[0]
        stdout = ""
        if command == "log":
            stdout = f"G\0{signer}\n"
        elif command == "remote":
            stdout = f"origin\t{remote} (fetch)\norigin\t{remote} (push)\n"
        stderr = "warning: unreadable path\n" if command == warning_command else ""
        return subprocess.CompletedProcess(["git", *args], 0, stdout, stderr)

    monkeypatch.setattr(gate, "_git", fake_git)
    rows = gate.check_repository("model_hub", repo, required_remote=remote)
    indexed = {row["name"]: row for row in rows}

    assert indexed[failed_check]["ok"] is False


def test_zero_remote_policy_accepts_clean_local_repository(tmp_path):
    gate = _load_gate()
    repo = _repo(tmp_path, "local-only")

    rows = gate.check_repository("lac_pro", repo, require_zero_remotes=True)
    indexed = {row["name"]: row for row in rows}

    assert indexed["lac_pro_clean"]["ok"] is True
    assert indexed["lac_pro_remote"]["ok"] is True
    assert indexed["lac_pro_signed_commits"]["ok"] is False


def test_main_returns_nonzero_and_emits_json_for_currently_blocked_fixture(tmp_path, capsys):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")
    cloud = _repo(tmp_path, "lac-cloud")

    rc = gate.main([
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(cloud),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])

    report = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert report["ready"] is False
    assert report["failed_count"] > 0
    assert all("secret" not in json.dumps(row).lower() for row in report["checks"])


def test_build_report_derives_and_passes_exact_evidence_subject_bindings(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    model = tmp_path / "model-hub"
    pro = tmp_path / "lac-pro"
    cloud = tmp_path / "lac-cloud"
    for repo in (model, pro, cloud):
        repo.mkdir()
    installer = tmp_path / "LAC-Setup-2.7.0.exe"
    provenance = tmp_path / "release-provenance.json"
    installer.write_bytes(b"exact signed installer")
    provenance.write_bytes(b'{"schema_version":2}\n')
    args = gate.parse_args([
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(cloud),
        "--installer", str(installer),
        "--provenance", str(provenance),
        "--evidence", str(tmp_path / "evidence.json"),
    ])
    commits = {
        model: MODEL_HUB_COMMIT,
        pro: LAC_PRO_COMMIT,
        cloud: CLOUD_COMMIT,
    }
    captured = {}

    def fake_git(repo, *git_args):
        assert git_args == ("rev-parse", "HEAD")
        return subprocess.CompletedProcess(git_args, 0, commits[repo] + "\n", "")

    def fake_evidence(path, release_scope, version, **expected):
        captured.update(expected)
        return []

    monkeypatch.setattr(gate, "_git", fake_git)
    monkeypatch.setattr(gate, "check_repository", lambda *args, **kwargs: [])
    monkeypatch.setattr(gate, "check_cloud_product_readiness", lambda *args: {
        "lane": "cloud_product", "name": "cloud_product_local_complete",
        "ok": True, "detail": "fixture", "data": {},
    })
    monkeypatch.setattr(gate, "check_installer", lambda *args, **kwargs: [])
    monkeypatch.setattr(gate, "check_evidence", fake_evidence)

    report = gate.build_report(args)

    assert report["ready"] is True
    assert captured == {
        "expected_model_hub_commit": MODEL_HUB_COMMIT,
        "expected_lac_pro_commit": LAC_PRO_COMMIT,
        "expected_lac_cloud_commit": CLOUD_COMMIT,
        "expected_installer_sha256": _sha256(installer),
        "expected_provenance_sha256": _sha256(provenance),
    }


def test_build_attestations_bind_every_subject_to_repository_workflow_commit_and_tag(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    subjects = tuple(
        tmp_path / name
        for name in (
            "LAC-Setup-2.7.0.exe",
            "lac.exe",
            "SHA256SUMS.txt",
            "release-provenance.json",
            "python-sbom.json",
            "web-sbom.json",
        )
    )
    for subject in subjects:
        subject.write_bytes(f"signed release subject: {subject.name}".encode())
    calls = []

    def fake_run(args, *, cwd=None):
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, 0, '[{"verificationResult": {}}]', "")

    monkeypatch.setattr(gate, "_run", fake_run)
    source_commit = "a" * 40

    assert gate._verified_build_attestations(subjects, source_commit) is True
    assert [Path(command[0][3]) for command in calls] == list(subjects)
    for command, cwd in calls:
        assert cwd is None
        assert command[:3] == ["gh", "attestation", "verify"]
        assert command[command.index("--repo") + 1] == "Dkrynen/lac"
        assert command[command.index("--signer-workflow") + 1].endswith("/.github/workflows/build.yml")
        assert command[command.index("--source-digest") + 1] == source_commit
        assert command[command.index("--source-ref") + 1] == "refs/tags/v2.7.0"
        assert "--deny-self-hosted-runners" in command


def test_build_attestations_fail_closed_when_any_subject_is_unverified(tmp_path, monkeypatch):
    gate = _load_gate()
    subjects = tuple(tmp_path / f"subject-{index}" for index in range(3))
    for subject in subjects:
        subject.write_bytes(b"release subject")
    calls = []

    def fake_run(args, *, cwd=None):
        calls.append(args)
        output = "[]" if args[3] == str(subjects[1]) else '[{"verificationResult": {}}]'
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_run)

    assert gate._verified_build_attestations(subjects, "a" * 40) is False
    assert [Path(command[3]) for command in calls] == list(subjects[:2])


def test_release_gate_requests_attestation_for_all_exact_subjects(tmp_path, monkeypatch):
    gate = _load_gate()
    fixture = _release_fixture(tmp_path, gate)
    captured = []
    monkeypatch.setattr(gate, "_authenticode", lambda path: fixture["signature"])

    def verify(subjects, source_commit):
        captured.extend(subjects)
        assert source_commit == "a" * 40
        return True

    monkeypatch.setattr(gate, "_verified_build_attestations", verify)

    rows = gate.check_installer(
        fixture["installer"],
        fixture["checksums"],
        fixture["application"],
        fixture["provenance"],
        "a" * 40,
        fixture["dependency_lock"],
        fixture["python_sbom"],
        fixture["web_sbom"],
        now=NOW,
    )

    assert next(row for row in rows if row["name"] == "build_provenance_attestation")["ok"] is True
    assert captured == [
        fixture["installer"],
        fixture["application"],
        fixture["checksums"],
        fixture["provenance"],
        fixture["python_sbom"],
        fixture["web_sbom"],
    ]


def test_release_range_starts_at_the_public_upstream_commit():
    gate = _load_gate()
    expected = (
        "c84d0fffae638664c6887b5786645cd4055d5c45"  # pragma: allowlist secret -- public Git commit
    )
    assert gate.MODEL_HUB_RELEASE_BASE == expected


def test_gate_defaults_to_canonical_dist_web_sbom():
    gate = _load_gate()

    assert gate.parse_args([]).web_sbom == gate.ROOT / "dist" / "web-sbom.json"


def test_regional_latency_is_a_fresh_signed_launch_gate():
    gate = _load_gate()

    assert "regional_latency_slo" in gate.REQUIRED_EVIDENCE_GATES
    assert gate.EVIDENCE_MAX_AGE_DAYS["regional_latency_slo"] == 1


def test_authenticode_trust_requires_rfc3161_timestamp_identity_and_time(monkeypatch):
    gate = _load_gate()
    monkeypatch.setattr(gate, "EXPECTED_AUTHENTICODE_SUBJECTS", frozenset({"CN=LAC"}))
    monkeypatch.setattr(gate, "EXPECTED_AUTHENTICODE_THUMBPRINTS", frozenset({"A" * 40}))
    valid = {
        "status": "Valid",
        "subject": "CN=LAC",
        "thumbprint": "A" * 40,
        "timestamp_subject": "CN=Trusted TSA",
        "timestamp_thumbprint": "B" * 40,
        "timestamp_not_before": "2026-01-01T00:00:00.0000000Z",
        "timestamp_not_after": "2027-01-01T00:00:00.0000000Z",
        "timestamped_at_utc": "2026-07-13T00:00:00.0000000Z",
        "timestamp_eku": True,
    }

    assert gate._trusted_signature(valid) is True
    assert gate._trusted_signature({**valid, "timestamped_at_utc": ""}) is False
    assert gate._trusted_signature({**valid, "timestamp_eku": False}) is False
    assert gate._trusted_signature({
        **valid,
        "timestamped_at_utc": "2028-01-01T00:00:00.0000000Z",
    }) is False


def test_checksum_manifest_rejects_duplicate_installer_entries(tmp_path, monkeypatch):
    gate = _load_gate()
    fixture = _release_fixture(tmp_path, gate)
    digest = _sha256(fixture["installer"])
    fixture["checksums"].write_text(
        f"{digest}  LAC-Setup-2.7.0.exe\n{digest} *LAC-Setup-2.7.0.exe\n",
        encoding="ascii",
    )

    rows = _check_release_fixture(gate, fixture, monkeypatch)

    assert next(row for row in rows if row["name"] == "installer_checksum")["ok"] is False


def test_release_provenance_v2_binds_lock_and_both_sboms(tmp_path, monkeypatch):
    gate = _load_gate()
    fixture = _release_fixture(tmp_path, gate)

    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is True

    fixture["dependency_lock"].write_text("tampered lock\n", encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    fixture["dependency_lock"].write_text(
        "locked-dependency==1.0 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    fixture["python_sbom"].write_text("tampered SBOM\n", encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    fixture["python_sbom"].write_text(
        '{"bomFormat":"CycloneDX","component":"python"}\n', encoding="utf-8",
    )
    fixture["web_sbom"].write_text("tampered SBOM\n", encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False


def test_release_provenance_requires_exact_schema_and_bounded_utc_build_time(tmp_path, monkeypatch):
    gate = _load_gate()
    fixture = _release_fixture(tmp_path, gate)
    record = fixture["record"]

    record["unexpected"] = "accepted by loose schemas"
    fixture["provenance"].write_text(json.dumps(record), encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    del record["unexpected"]
    record["built_at_utc"] = "2026-07-13T02:00:01+02:00"
    fixture["provenance"].write_text(json.dumps(record), encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    record["built_at_utc"] = "2025-01-01T00:00:00Z"
    fixture["provenance"].write_text(json.dumps(record), encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    record["built_at_utc"] = "2026-07-14T00:00:00Z"
    fixture["provenance"].write_text(json.dumps(record), encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False

    record["built_at_utc"] = "2026-07-13T00:00:01.0000000Z"
    record["installer"]["unexpected"] = True
    fixture["provenance"].write_text(json.dumps(record), encoding="utf-8")
    rows = _check_release_fixture(gate, fixture, monkeypatch)
    assert next(row for row in rows if row["name"] == "release_provenance")["ok"] is False


def test_release_tag_must_be_annotated_signed_target_head_and_use_trusted_signer(tmp_path, monkeypatch):
    gate = _load_gate()
    repo = _repo(tmp_path, "tagged")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "-a", "v2.7.0", "-m", "release 2.7.0")
    real_git = gate._git
    signer = "A" * 40

    def signed_tag(repo_path, *args):
        if args == ("verify-tag", "--raw", "refs/tags/v2.7.0"):
            return subprocess.CompletedProcess(
                args,
                0,
                "",
                f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG {signer} 2026-07-13 0 4 0 1 10 00 {signer}\n",
            )
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", signed_tag)
    monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS_BY_REPO", {"model_hub": frozenset({signer})})
    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )
    assert next(row for row in rows if row["name"] == "model_hub_signed_release_tag")["ok"] is True

    def mismatched_embedded_name(repo_path, *args):
        if args == ("verify-tag", "--raw", "refs/tags/v2.7.0"):
            return signed_tag(repo_path, *args)
        if args == ("cat-file", "tag", "refs/tags/v2.7.0"):
            tag_object = real_git(repo_path, *args)
            return subprocess.CompletedProcess(
                args,
                tag_object.returncode,
                tag_object.stdout.replace("tag v2.7.0", "tag v9.9.9", 1),
                tag_object.stderr,
            )
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", mismatched_embedded_name)
    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )
    mismatched = next(row for row in rows if row["name"] == "model_hub_signed_release_tag")
    assert mismatched["ok"] is False
    assert mismatched["data"]["embedded_name_matches"] is False

    monkeypatch.setattr(gate, "_git", signed_tag)

    for adverse_status in ("EXPKEYSIG", "REVKEYSIG", "KEYEXPIRED", "SIGEXPIRED"):
        def adverse_tag(repo_path, *args, adverse_status=adverse_status):
            if args == ("verify-tag", "--raw", "refs/tags/v2.7.0"):
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "",
                    (
                        f"[GNUPG:] VALIDSIG {signer} 2026-07-13 0 4 0 1 10 00 {signer}\n"
                        f"[GNUPG:] {adverse_status} {signer} release signer\n"
                    ),
                )
            return real_git(repo_path, *args)

        monkeypatch.setattr(gate, "_git", adverse_tag)
        rows = gate.check_repository(
            "model_hub",
            repo,
            release_tag="v2.7.0",
            expected_tag_target=head,
        )
        assert next(
            row for row in rows if row["name"] == "model_hub_signed_release_tag"
        )["ok"] is False

    monkeypatch.setattr(gate, "_git", signed_tag)

    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target="b" * 40,
    )
    assert next(row for row in rows if row["name"] == "model_hub_signed_release_tag")["ok"] is False

    monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS_BY_REPO", {"model_hub": frozenset()})
    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )
    assert next(row for row in rows if row["name"] == "model_hub_signed_release_tag")["ok"] is False

    monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS_BY_REPO", {"model_hub": frozenset({signer})})
    _git(repo, "tag", "-d", "v2.7.0")
    _git(repo, "tag", "v2.7.0")
    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )
    assert next(row for row in rows if row["name"] == "model_hub_signed_release_tag")["ok"] is False


def test_subprocess_timeout_returns_a_structured_failure(monkeypatch):
    gate = _load_gate()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "attestation", "verify"], timeout=30)

    monkeypatch.setattr(gate.subprocess, "run", timeout)
    result = gate._run(["gh", "attestation", "verify"])

    assert result.returncode == 124
    assert result.stdout == ""
    assert result.stderr == "command unavailable or timed out"


def test_local_scope_membership_is_an_exact_subset_with_max_ages():
    gate = _load_gate()

    assert gate.RELEASE_SCOPES == ("local", "cloud")
    assert gate.EVIDENCE_SCHEMA_VERSION == 3
    assert gate.LOCAL_EVIDENCE_GATES == (
        "patent_clearance",
        "github_enterprise_controls",
        "cryptographic_review",
        "artifact_roundtrip",
        "clean_machine_signed_install",
    )
    assert gate.EVIDENCE_GATES_BY_SCOPE == {
        "local": gate.LOCAL_EVIDENCE_GATES,
        "cloud": gate.REQUIRED_EVIDENCE_GATES,
    }
    assert set(gate.LOCAL_EVIDENCE_GATES) < set(gate.REQUIRED_EVIDENCE_GATES)
    assert len(gate.REQUIRED_EVIDENCE_GATES) == 19
    for name in gate.REQUIRED_EVIDENCE_GATES:
        assert gate.EVIDENCE_MAX_AGE_DAYS[name] >= 1
    assert not (set(gate.LOCAL_EVIDENCE_GATES) & gate._WORKER_BOUND_EVIDENCE_GATES)
    assert gate._LOCAL_EVIDENCE_RECORD_FIELDS == (
        gate._EVIDENCE_BASE_FIELDS | {
            "model_hub_commit", "lac_pro_commit",
            "installer_sha256", "release_provenance_sha256",
        }
    )


def test_signature_payload_binds_release_scope():
    gate = _load_gate()
    record = {"status": "verified"}

    local = gate.evidence_signature_payload(
        "patent_clearance", "local", "2.7.0", record,
    )
    cloud = gate.evidence_signature_payload(
        "patent_clearance", "cloud", "2.7.0", record,
    )

    assert local != cloud
    assert b'"release_scope":"local"' in local
    assert b'"release_scope":"cloud"' in cloud


def test_local_scope_evidence_passes_with_zero_cloud_evidence(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    evidence = _valid_evidence(gate, private_key, release_scope="local")
    path.write_text(json.dumps(evidence), encoding="utf-8")

    rows = _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    )

    assert {row["name"] for row in rows} == {
        f"evidence_{name}" for name in gate.LOCAL_EVIDENCE_GATES
    }
    assert all(row["ok"] for row in rows)
    assert all(
        "lac_cloud_commit" not in record
        for record in evidence["gates"].values()
    )


def test_local_and_cloud_manifests_cannot_cross_authorize(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)

    local_manifest = _valid_evidence(gate, private_key, release_scope="local")
    path.write_text(json.dumps(local_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))

    digests = _write_hosted_evidence_objects(path)
    cloud_manifest = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(cloud_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    ))

    forged = _valid_evidence(gate, private_key, release_scope="local")
    forged["release_scope"] = "cloud"
    forged["gates"] = {
        name: forged["gates"].get(name)
        for name in gate.REQUIRED_EVIDENCE_GATES
        if forged["gates"].get(name) is not None
    }
    path.write_text(json.dumps(forged), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))


def test_schema_v2_manifests_fail_closed_in_both_scopes(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)

    cloud_manifest = _valid_evidence(gate, private_key, hosted_digests=digests)
    cloud_manifest["schema_version"] = 2
    path.write_text(json.dumps(cloud_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))

    local_manifest = _valid_evidence(gate, private_key, release_scope="local")
    local_manifest["schema_version"] = 2
    path.write_text(json.dumps(local_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    ))

    genuine_v2_cloud = _valid_evidence(gate, private_key, hosted_digests=digests)
    genuine_v2_cloud["schema_version"] = 2
    del genuine_v2_cloud["release_scope"]
    path.write_text(json.dumps(genuine_v2_cloud), encoding="utf-8")
    rows = _check_evidence(gate, path)
    assert all(not row["ok"] for row in rows)
    assert all(row["detail"] == "evidence manifest is invalid" for row in rows)

    genuine_v2_local = _valid_evidence(gate, private_key, release_scope="local")
    genuine_v2_local["schema_version"] = 2
    del genuine_v2_local["release_scope"]
    path.write_text(json.dumps(genuine_v2_local), encoding="utf-8")
    rows = _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    )
    assert all(not row["ok"] for row in rows)
    assert all(row["detail"] == "evidence manifest is invalid" for row in rows)


def test_release_scope_defaults_to_cloud():
    gate = _load_gate()

    args = gate.parse_args([])
    assert args.release_scope == "cloud"
    assert gate.parse_args(["--release-scope", "local"]).release_scope == "local"

    with pytest.raises(SystemExit):
        gate.parse_args(["--release-scope", "bogus"])


def test_local_scope_report_omits_cloud_lanes_and_binds_scope(tmp_path):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")

    rc = gate.main([
        "--release-scope", "local",
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(tmp_path / "does-not-exist"),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])
    assert rc == 1


def test_local_scope_report_content(tmp_path, capsys):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")

    gate.main([
        "--release-scope", "local",
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(tmp_path / "does-not-exist"),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])

    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 2
    assert report["release_scope"] == "local"
    names = [row["name"] for row in report["checks"]]
    lanes = {row["lane"] for row in report["checks"]}
    assert not any(name.startswith("lac_cloud_") for name in names)
    assert "cloud_product" not in lanes
    assert {
        f"evidence_{name}" for name in gate.LOCAL_EVIDENCE_GATES
    } == {name for name in names if name.startswith("evidence_")}
    assert any(name.startswith("model_hub_") for name in names)
    assert any(name.startswith("lac_pro_") for name in names)
    assert any(name == "installer_exists" for name in names)


def test_cloud_scope_report_keeps_full_lane_set(tmp_path, capsys):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")
    cloud = _repo(tmp_path, "lac-cloud")

    gate.main([
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(cloud),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])

    report = json.loads(capsys.readouterr().out)
    assert report["release_scope"] == "cloud"
    names = [row["name"] for row in report["checks"]]
    assert any(name.startswith("lac_cloud_") for name in names)
    assert "cloud_product_local_complete" in names
    assert {
        f"evidence_{name}" for name in gate.REQUIRED_EVIDENCE_GATES
    } == {name for name in names if name.startswith("evidence_")}


def test_cloud_signed_records_stripped_of_lac_cloud_commit_fail_only_on_signature(
    tmp_path, monkeypatch,
):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)
    cloud_manifest = _valid_evidence(gate, private_key, hosted_digests=digests)

    forged_gates = {}
    for name in gate.LOCAL_EVIDENCE_GATES:
        record = dict(cloud_manifest["gates"][name])
        del record["lac_cloud_commit"]
        forged_gates[name] = record
    forged = {
        "schema_version": 3,
        "release_scope": "local",
        "release_version": "2.7.0",
        "gates": forged_gates,
    }
    path.write_text(json.dumps(forged), encoding="utf-8")

    rows = _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    )

    assert all(not row["ok"] for row in rows)
    stripped_patent_clearance = forged_gates["patent_clearance"]
    assert set(stripped_patent_clearance) == gate._LOCAL_EVIDENCE_RECORD_FIELDS


def test_trust_roots_are_onboarded_and_well_formed():
    gate = _load_gate()

    assert gate.TRUSTED_COMMIT_SIGNERS_BY_REPO == {
        "model_hub": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
        }),
        "lac_pro": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
        }),
        "lac_cloud": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
            "SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI",
        }),
    }
    assert all(
        gate._normalise_signer(signer)
        for signers in gate.TRUSTED_COMMIT_SIGNERS_BY_REPO.values()
        for signer in signers
    )

    assert set(gate.TRUSTED_EVIDENCE_SIGNERS) == {"duan-review-2026"}
    entry = gate.TRUSTED_EVIDENCE_SIGNERS["duan-review-2026"]
    assert set(entry) == {"public_key", "approvers", "gates", "not_before", "not_after"}
    public_key = base64.urlsafe_b64decode(entry["public_key"] + "=")
    assert len(public_key) == 32
    Ed25519PublicKey.from_public_bytes(public_key)
    assert entry["approvers"] == ["duan-krynen"]
    assert list(entry["gates"]) == list(gate.REQUIRED_EVIDENCE_GATES)
    assert isinstance(entry["not_before"], int)
    assert isinstance(entry["not_after"], int)
    assert entry["not_before"] < entry["not_after"]

    assert gate.EXPECTED_AUTHENTICODE_SUBJECTS == frozenset()
    assert gate.EXPECTED_AUTHENTICODE_THUMBPRINTS == frozenset()


def test_default_evidence_trust_roots_reject_unlisted_signers(tmp_path):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    digests = _write_hosted_evidence_objects(path)
    evidence = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert all(not row["ok"] for row in _check_evidence(gate, path))


def test_cloud_only_signer_cannot_authorize_a_model_hub_release_tag(tmp_path, monkeypatch):
    gate = _load_gate()
    repo = _repo(tmp_path, "tagged")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "-a", "v2.7.0", "-m", "release 2.7.0")
    real_git = gate._git
    signer = "B" * 40

    def signed_tag(repo_path, *args):
        if args == ("verify-tag", "--raw", "refs/tags/v2.7.0"):
            return subprocess.CompletedProcess(
                args,
                0,
                "",
                f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG {signer} 2026-07-14 0 4 0 1 10 00 {signer}\n",
            )
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", signed_tag)
    monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS_BY_REPO", {
        "model_hub": frozenset(),
        "lac_cloud": frozenset({signer}),
    })

    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )

    assert next(
        row for row in rows if row["name"] == "model_hub_signed_release_tag"
    )["ok"] is False


def test_unknown_repo_name_resolves_to_an_empty_signer_allowlist(tmp_path, monkeypatch):
    gate = _load_gate()
    repo = _repo(tmp_path, "mystery")
    signer = "C" * 40
    real_git = gate._git

    def signed_log(repo_path, *args):
        if args and args[0] == "log":
            return subprocess.CompletedProcess(args, 0, f"G\x00{signer}\n", "")
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", signed_log)

    rows = gate.check_repository("mystery_repo", repo)

    assert next(
        row for row in rows if row["name"] == "mystery_repo_signed_commits"
    )["ok"] is False
