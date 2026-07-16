from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = ROOT / "scripts" / "enterprise_launch_gate.py"
SIGNER_SCRIPT = ROOT / "scripts" / "sign_enterprise_evidence.py"
NOW = 1_784_179_200.0  # 2026-07-16T00:00:00Z


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trusted_signer(signer, private_key: Ed25519PrivateKey) -> None:
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    signer.gate.TRUSTED_EVIDENCE_SIGNERS = {
        "test-reviewer-2026": {
            "public_key": base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii"),
            "approvers": ["reviewer"],
            "gates": list(signer.gate.REQUIRED_EVIDENCE_GATES),
            "not_before": 1_700_000_000,
            "not_after": 1_900_000_000,
        },
    }


def _local_draft(signer) -> dict:
    records = {}
    for index, name in enumerate(signer.gate.LOCAL_EVIDENCE_GATES, start=1):
        records[name] = {
            "status": "verified",
            "approver": "reviewer",
            "reference": f"authoritative-record-{index}",
            "recorded_at": "2026-07-16T00:00:00Z",
            "record_sha256": f"{index:064x}",
            "signer_kid": "test-reviewer-2026",
            "model_hub_commit": "a" * 40,
            "lac_pro_commit": "b" * 40,
            "installer_sha256": "c" * 64,
            "release_provenance_sha256": "d" * 64,
        }
    return {
        "schema_version": 3,
        "release_scope": "local",
        "release_version": "2.7.0",
        "gates": records,
    }


def _cloud_draft(signer) -> dict:
    document = {
        "schema_version": 3,
        "release_scope": "cloud",
        "release_version": "2.7.0",
        "gates": {},
    }
    versions = {
        "api_version_id": "11111111-1111-1111-1111-111111111111",
        "agent_version_id": "22222222-2222-2222-2222-222222222222",
        "runner_version_id": "33333333-3333-3333-3333-333333333333",
    }
    for index, name in enumerate(signer.gate.REQUIRED_EVIDENCE_GATES, start=1):
        record = {
            "status": "verified",
            "approver": "reviewer",
            "reference": f"authoritative-record-{index}",
            "recorded_at": "2026-07-16T00:00:00Z",
            "record_sha256": f"{index:064x}",
            "signer_kid": "test-reviewer-2026",
            "model_hub_commit": "a" * 40,
            "lac_pro_commit": "b" * 40,
            "lac_cloud_commit": "e" * 40,
            "installer_sha256": "c" * 64,
            "release_provenance_sha256": "d" * 64,
        }
        if name in signer.gate._WORKER_BOUND_EVIDENCE_GATES:
            record.update(versions)
        if name in {"regional_latency_slo", "hosted_agent_end_to_end"}:
            record["measured_at"] = "2026-07-16T00:00:00Z"
        if name == "hosted_agent_end_to_end":
            record.update({
                "journey_manifest_sha256": "1" * 64,
                "price_card_payload_sha256": "2" * 64,
                "provider_meter_sha256": "3" * 64,
                "infrastructure_meter_sha256": "4" * 64,
            })
        document["gates"][name] = record
    return document


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sign_document_matches_gate_contract_without_mutating_draft():
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    draft = _local_draft(signer)

    signed = signer.sign_document(draft, private_key, now=NOW)

    assert all("signature" not in record for record in draft["gates"].values())
    assert all(record["signature"] for record in signed["gates"].values())
    for name, record in signed["gates"].items():
        assert signer.gate._verify_evidence_record(
            name,
            "local",
            "2.7.0",
            record,
            expected_model_hub_commit="a" * 40,
            expected_lac_pro_commit="b" * 40,
            expected_lac_cloud_commit="",
            expected_installer_sha256="c" * 64,
            expected_provenance_sha256="d" * 64,
            now=NOW,
        )


def test_sign_document_rejects_wrong_private_key_and_presigned_records():
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_wrong_key")
    trusted_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, trusted_key)
    draft = _local_draft(signer)

    with pytest.raises(ValueError, match="does not match"):
        signer.sign_document(draft, Ed25519PrivateKey.generate(), now=NOW)

    draft["gates"]["patent_clearance"]["signature"] = "already-signed"
    with pytest.raises(ValueError, match="unsigned"):
        signer.sign_document(draft, trusted_key, now=NOW)


def test_sign_document_rejects_invalid_or_incomplete_authoritative_record_metadata():
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_invalid")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    draft = _local_draft(signer)
    draft["gates"]["cryptographic_review"]["reference"] = "pending"

    with pytest.raises(ValueError, match="cryptographic_review"):
        signer.sign_document(draft, private_key, now=NOW)


def test_sign_document_rejects_mixed_production_worker_bindings():
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_mixed_workers")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    draft = _cloud_draft(signer)
    draft["gates"]["regional_latency_slo"]["api_version_id"] = (
        "99999999-9999-9999-9999-999999999999"
    )

    with pytest.raises(ValueError, match="inconsistent Worker bindings"):
        signer.sign_document(draft, private_key, now=NOW)


def test_cli_requires_acknowledgement_and_refuses_output_overwrite(tmp_path):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_cli")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    draft_path = tmp_path / "draft.json"
    output_path = tmp_path / "signed.json"
    key_path = tmp_path / "review-key.pem"
    draft_path.write_text(json.dumps(_local_draft(signer)), encoding="utf-8")
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))

    assert signer.main([
        "--input", str(draft_path),
        "--expected-input-sha256", _digest(draft_path),
        "--private-key", str(key_path),
        "--output", str(output_path),
    ]) == 2
    assert not output_path.exists()

    args = [
        "--input", str(draft_path),
        "--expected-input-sha256", _digest(draft_path),
        "--private-key", str(key_path),
        "--output", str(output_path),
        "--acknowledge-authoritative-records-reviewed",
    ]
    assert signer.main(args, now=NOW) == 2
    assert not output_path.exists()

    args.insert(-1, "--allow-unencrypted-private-key")
    assert signer.main(args, now=NOW) == 0
    assert output_path.is_file()
    original = output_path.read_bytes()
    assert signer.main(args, now=NOW) == 2
    assert output_path.read_bytes() == original


def test_cli_rejects_hash_mismatch_repo_local_evidence_and_missing_cloud_objects(tmp_path):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_paths")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    private_root = tmp_path / "private"
    repo_root = tmp_path / "repo"
    private_root.mkdir()
    repo_root.mkdir()
    signer.ROOT = repo_root
    key_path = private_root / "review-key.pem"
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))

    local_path = private_root / "local-draft.json"
    local_path.write_text(json.dumps(_local_draft(signer)), encoding="utf-8")
    common = [
        "--private-key", str(key_path),
        "--allow-unencrypted-private-key",
        "--acknowledge-authoritative-records-reviewed",
    ]
    assert signer.main([
        "--input", str(local_path),
        "--expected-input-sha256", "0" * 64,
        "--output", str(private_root / "hash-mismatch.json"),
        *common,
    ], now=NOW) == 2

    repo_draft = repo_root / "draft.json"
    repo_draft.write_text(json.dumps(_local_draft(signer)), encoding="utf-8")
    assert signer.main([
        "--input", str(repo_draft),
        "--expected-input-sha256", _digest(repo_draft),
        "--output", str(repo_root / "signed.json"),
        *common,
    ], now=NOW) == 2

    cloud_path = private_root / "cloud-draft.json"
    cloud_path.write_text(json.dumps(_cloud_draft(signer)), encoding="utf-8")
    assert signer.main([
        "--input", str(cloud_path),
        "--expected-input-sha256", _digest(cloud_path),
        "--output", str(private_root / "cloud-signed.json"),
        *common,
    ], now=NOW) == 2
    assert not (private_root / "cloud-signed.json").exists()


def test_cli_reads_encrypted_key_via_password_prompt(tmp_path, monkeypatch):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_encrypted")
    private_key = Ed25519PrivateKey.generate()
    _trusted_signer(signer, private_key)
    draft_path = tmp_path / "draft.json"
    output_path = tmp_path / "signed.json"
    key_path = tmp_path / "review-key.pem"
    draft_path.write_text(json.dumps(_local_draft(signer)), encoding="utf-8")
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"test-password"),
    ))
    monkeypatch.setattr(signer.getpass, "getpass", lambda _prompt: "test-password")

    assert signer.main([
        "--input", str(draft_path),
        "--expected-input-sha256", _digest(draft_path),
        "--private-key", str(key_path),
        "--output", str(output_path),
        "--prompt-key-password",
        "--acknowledge-authoritative-records-reviewed",
    ], now=NOW) == 0


def test_read_document_rejects_duplicate_json_members(tmp_path):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_duplicate_json")
    draft_path = tmp_path / "draft.json"
    draft_path.write_text('{"schema_version":3,"schema_version":3}', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        signer._read_document(draft_path, expected_sha256=_digest(draft_path))


def test_write_exclusive_rejects_redirected_open_handle(tmp_path, monkeypatch):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_redirected_output")
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    repo_root.mkdir()
    private_root.mkdir()
    signer.ROOT = repo_root
    output_path = private_root / "signed.json"
    redirected_path = repo_root / "redirected.json"
    redirected_path.write_text("redirected", encoding="utf-8")
    monkeypatch.setattr(
        signer,
        "_open_handle_path",
        lambda _descriptor: redirected_path,
    )

    with pytest.raises(ValueError, match="outside the repository"):
        signer._write_exclusive(output_path, {"signed": True})
    assert not output_path.exists()


def test_darwin_open_handle_path_uses_f_getpath(tmp_path, monkeypatch):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_darwin_handle")
    target = tmp_path / "signed.json"
    target.write_text("signed", encoding="utf-8")
    calls = []

    def fake_fcntl(descriptor, command, buffer):
        calls.append((descriptor, command, len(buffer)))
        path = os.fsencode(target)
        return path + b"\0" + (b"\0" * (len(buffer) - len(path) - 1))

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(fcntl=fake_fcntl))

    assert signer._darwin_open_handle_path(17) == target.resolve(strict=True)
    assert calls == [(17, 50, 1024)]


def test_darwin_open_handle_path_fails_closed_on_unterminated_result(monkeypatch):
    signer = _load(SIGNER_SCRIPT, "sign_enterprise_evidence_darwin_unterminated")
    monkeypatch.setitem(
        sys.modules,
        "fcntl",
        SimpleNamespace(fcntl=lambda _descriptor, _command, buffer: b"x" * len(buffer)),
    )

    with pytest.raises(ValueError, match="final path could not be verified"):
        signer._darwin_open_handle_path(17)
