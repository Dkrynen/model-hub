from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.cloud_session import (
    CloudSessionError,
    parse_public_account_response_v1,
    parse_public_entitlements_response_v1,
    parse_public_oauth_token_response_v1,
    parse_public_stable_failure_response_v1,
    parse_public_usage_response_v1,
)

ROOT = Path(__file__).resolve().parents[1]
VECTOR_PATH = ROOT / "tests" / "fixtures" / "public-desktop-bootstrap.v1.json"
CANONICAL_VECTOR_SHA256 = "36c5060b3e429fa8c52271004effcfa6eca4e7b4da0a9e4c1661786ed3ea29a7"
PARSERS = {
    "oauth_token_response_v1": parse_public_oauth_token_response_v1,
    "account_response_v1": parse_public_account_response_v1,
    "entitlements_response_v1": parse_public_entitlements_response_v1,
    "usage_response_v1": parse_public_usage_response_v1,
    "stable_failure_response_v1": parse_public_stable_failure_response_v1,
}


def test_python_client_matches_all_public_desktop_bootstrap_vectors():
    fixture = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert fixture["schemaVersion"] == 1
    assert fixture["contract"] == "lac.public.desktop-bootstrap"
    for vector in fixture["vectors"]:
        parser = PARSERS[vector["parser"]]
        if vector["valid"]:
            parser(vector["value"])
        else:
            with pytest.raises((CloudSessionError, TypeError, ValueError), match="invalid_response"):
                parser(vector["value"])


def test_desktop_bootstrap_vector_matches_the_canonical_semantic_digest():
    fixture = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(
        fixture,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == CANONICAL_VECTOR_SHA256


def test_local_cloud_and_desktop_vector_copies_are_byte_identical_when_co_located():
    cloud_vector = (
        ROOT.parent
        / "lac-cloud"
        / "packages"
        / "contracts"
        / "test-vectors"
        / VECTOR_PATH.name
    )
    if not cloud_vector.exists():
        pytest.skip("lac-cloud sibling checkout is not present")
    assert json.loads(VECTOR_PATH.read_text(encoding="utf-8")) == json.loads(
        cloud_vector.read_text(encoding="utf-8")
    )
