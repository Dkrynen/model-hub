from pathlib import Path

import pytest

from scripts.unified_product_audit import (
    AuditError,
    CANONICAL_VECTOR_SHA256,
    audit_unified_product,
    canonical_vector_digest,
)

ROOT = Path(__file__).resolve().parents[1]


def test_unified_audit_uses_the_pinned_cloud_contract_digest():
    vector = ROOT / "tests" / "fixtures" / "public-desktop-bootstrap.v1.json"

    assert canonical_vector_digest(vector) == CANONICAL_VECTOR_SHA256


def test_unified_audit_fails_closed_when_a_private_product_root_is_missing(tmp_path):
    with pytest.raises(AuditError, match="Local Pro root is missing"):
        audit_unified_product(
            host_root=ROOT,
            pro_root=tmp_path / "missing-pro",
            cloud_root=tmp_path / "missing-cloud",
        )
