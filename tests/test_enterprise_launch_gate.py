from __future__ import annotations

import importlib.util
import base64
import json
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "enterprise_launch_gate.py"
CLOUD_COMMIT = "c" * 40
NOW = 1_783_944_000.0
DEPLOYMENT_VERSIONS = {
    "api_version_id": "11111111-1111-1111-1111-111111111111",
    "agent_version_id": "22222222-2222-2222-2222-222222222222",
    "runner_version_id": "33333333-3333-3333-3333-333333333333",
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


def _valid_evidence(
    gate,
    private_key: Ed25519PrivateKey,
    *,
    cloud_commit: str = CLOUD_COMMIT,
    measured_at: str = "2026-07-13T00:00:00Z",
    deployment_versions: dict[str, str] = DEPLOYMENT_VERSIONS,
    latency_versions: dict[str, str] | None = None,
) -> dict:
    document = {
        "schema_version": 1,
        "release_version": "2.7.0",
        "gates": {},
    }
    for index, name in enumerate(gate.REQUIRED_EVIDENCE_GATES, start=1):
        record = {
            "status": "verified",
            "approver": "independent-reviewer",
            "reference": f"review-{index:02d}",
            "recorded_at": "2026-07-13T00:00:00Z",
            "record_sha256": f"{index:064x}",
            "signer_kid": "test-reviewer-2026",
        }
        if name in {"cloud_production_dark_smoke", "regional_latency_slo"}:
            record["deployment_commit"] = cloud_commit
            record.update(
                latency_versions
                if name == "regional_latency_slo" and latency_versions is not None
                else deployment_versions
            )
        if name == "regional_latency_slo":
            record["measured_at"] = measured_at
        signature = private_key.sign(gate.evidence_signature_payload(name, "2.7.0", record))
        record["signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        document["gates"][name] = record
    return document


def test_missing_evidence_fails_every_external_gate(tmp_path):
    gate = _load_gate()

    rows = gate.check_evidence(tmp_path / "missing.json", "2.7.0")

    assert {row["name"] for row in rows if not row["ok"]} == {
        f"evidence_{name}" for name in gate.REQUIRED_EVIDENCE_GATES
    }
    assert all("missing" in row["detail"] for row in rows)


def test_valid_evidence_requires_scoped_signature_exact_release_and_fresh_records(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
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
    evidence = _valid_evidence(gate, private_key)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    rows = gate.check_evidence(path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW)

    assert all(row["ok"] for row in gate.check_evidence(
        path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    ))
    assert not all(row["ok"] for row in gate.check_evidence(
        path, "2.7.1", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    ))
    evidence["gates"]["patent_clearance"]["reference"] = "tampered-reference"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert next(
        row for row in gate.check_evidence(
            path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
        )
        if row["name"] == "evidence_patent_clearance"
    )["ok"] is False


def test_regional_latency_evidence_binds_deployment_commit_and_measurement_time(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
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

    wrong_commit = _valid_evidence(gate, private_key, cloud_commit="d" * 40)
    path.write_text(json.dumps(wrong_commit), encoding="utf-8")
    rows = gate.check_evidence(
        path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    )
    assert next(row for row in rows if row["name"] == "evidence_regional_latency_slo")["ok"] is False

    mismatched_runtime = _valid_evidence(
        gate,
        private_key,
        latency_versions={
            **DEPLOYMENT_VERSIONS,
            "runner_version_id": "44444444-4444-4444-4444-444444444444",
        },
    )
    path.write_text(json.dumps(mismatched_runtime), encoding="utf-8")
    rows = gate.check_evidence(
        path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    )
    assert next(row for row in rows if row["name"] == "evidence_cloud_production_dark_smoke")["ok"] is False
    assert next(row for row in rows if row["name"] == "evidence_regional_latency_slo")["ok"] is False

    placeholder_runtime = _valid_evidence(
        gate,
        private_key,
        deployment_versions={**DEPLOYMENT_VERSIONS, "api_version_id": "replace"},
        latency_versions={**DEPLOYMENT_VERSIONS, "api_version_id": "replace"},
    )
    path.write_text(json.dumps(placeholder_runtime), encoding="utf-8")
    rows = gate.check_evidence(
        path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    )
    assert next(row for row in rows if row["name"] == "evidence_cloud_production_dark_smoke")["ok"] is False
    assert next(row for row in rows if row["name"] == "evidence_regional_latency_slo")["ok"] is False

    old_measurement = _valid_evidence(
        gate,
        private_key,
        measured_at="2026-06-01T00:00:00Z",
    )
    path.write_text(json.dumps(old_measurement), encoding="utf-8")
    rows = gate.check_evidence(
        path, "2.7.0", expected_cloud_commit=CLOUD_COMMIT, now=NOW,
    )
    assert next(row for row in rows if row["name"] == "evidence_regional_latency_slo")["ok"] is False


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


def test_build_attestation_is_bound_to_repository_workflow_commit_and_tag(tmp_path, monkeypatch):
    gate = _load_gate()
    installer = tmp_path / "LAC-Setup-2.7.0.exe"
    installer.write_bytes(b"signed release candidate")
    calls = []

    def fake_run(args, *, cwd=None):
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, 0, '[{"verificationResult": {}}]', "")

    monkeypatch.setattr(gate, "_run", fake_run)
    source_commit = "a" * 40

    assert gate._verified_build_attestation(installer, source_commit) is True
    command = calls[0][0]
    assert command[:3] == ["gh", "attestation", "verify"]
    assert command[command.index("--repo") + 1] == "Dkrynen/lac"
    assert command[command.index("--signer-workflow") + 1].endswith("/.github/workflows/build.yml")
    assert command[command.index("--source-digest") + 1] == source_commit
    assert command[command.index("--source-ref") + 1] == "refs/tags/v2.7.0"
    assert "--deny-self-hosted-runners" in command


def test_release_range_starts_at_the_public_upstream_commit():
    gate = _load_gate()
    expected = (
        "c84d0fffae638664c6887b5786645cd4055d5c45"  # pragma: allowlist secret -- public Git commit
    )
    assert gate.MODEL_HUB_RELEASE_BASE == expected


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
