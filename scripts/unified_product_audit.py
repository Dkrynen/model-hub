#!/usr/bin/env python3
"""Fail-closed local integration audit for LAC, Local Pro, and LAC Cloud."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

CANONICAL_VECTOR_SHA256 = "36c5060b3e429fa8c52271004effcfa6eca4e7b4da0a9e4c1661786ed3ea29a7"
_CLOUD_EXECUTION_GATES = {
    "authenticated_client_surface",
    "provider_broker",
    "provider_metering",
    "infrastructure_metering",
    "hosted_workspace_execution",
}


class AuditError(RuntimeError):
    pass


def canonical_vector_digest(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"Invalid contract vector: {path}") from exc
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_root(label: str, root: Path, marker: str) -> Path:
    resolved = root.resolve(strict=False)
    if not (resolved / marker).is_file():
        raise AuditError(f"{label} root is missing or invalid: {resolved}")
    return resolved


def audit_unified_product(*, host_root: Path, pro_root: Path, cloud_root: Path) -> dict:
    host_root = _require_root("LAC host", host_root, "backend/plugins.py")
    pro_root = _require_root("Local Pro", pro_root, "lac_pro/plugin.py")
    cloud_root = _require_root("LAC Cloud", cloud_root, "config/product-readiness.v1.json")

    host_vector = host_root / "tests" / "fixtures" / "public-desktop-bootstrap.v1.json"
    cloud_vector = (
        cloud_root
        / "packages"
        / "contracts"
        / "test-vectors"
        / "public-desktop-bootstrap.v1.json"
    )
    for label, vector in (("desktop", host_vector), ("cloud", cloud_vector)):
        digest = canonical_vector_digest(vector)
        if digest != CANONICAL_VECTOR_SHA256:
            raise AuditError(f"{label} bootstrap contract drifted: {digest}")

    for source_root in (str(pro_root), str(host_root)):
        if source_root in sys.path:
            sys.path.remove(source_root)
        sys.path.insert(0, source_root)

    import backend.plugins as host_plugins
    import lac_pro.license as pro_license
    from lac_pro.plugin import ProPlugin

    entry_point = SimpleNamespace(name="pro", load=lambda: ProPlugin())
    with (
        patch.object(pro_license, "check", return_value=None),
        patch.object(pro_license, "_load_raw", return_value={}),
        patch.object(host_plugins, "_entry_points", return_value=[entry_point]),
    ):
        loaded = host_plugins.discover()
    if len(loaded) != 1 or loaded[0].state != "ready" or loaded[0].product_id != "local_pro":
        raise AuditError("The real Local Pro plugin did not satisfy the host discovery contract")
    entitlement = (loaded[0].product_state or {}).get("entitlement")
    if entitlement != {
        "state": "inactive",
        "plan": None,
        "expires_human": None,
        "checked": None,
    }:
        raise AuditError("The Local Pro public entitlement contract is not fail-closed")

    try:
        readiness = json.loads(
            (cloud_root / "config" / "product-readiness.v1.json").read_text(encoding="utf-8")
        )
        capabilities = readiness["capabilities"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuditError("The LAC Cloud product-readiness manifest is invalid") from exc
    enabled_execution = sorted(
        key for key in _CLOUD_EXECUTION_GATES if capabilities.get(key) is not False
    )
    if enabled_execution:
        raise AuditError(f"Cloud execution gates are not fail-closed: {', '.join(enabled_execution)}")

    return {
        "ready": True,
        "execution_default": "local",
        "local_pro": {
            "state": "ready",
            "plugin_version": loaded[0].version,
            "host_api_version": loaded[0].host_api_version,
        },
        "cloud": {
            "contract_sha256": CANONICAL_VECTOR_SHA256,
            "execution_available": False,
            "readiness": readiness.get("status"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pro-root", required=True, type=Path)
    parser.add_argument("--cloud-root", required=True, type=Path)
    parser.add_argument(
        "--host-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)
    try:
        result = audit_unified_product(
            host_root=args.host_root,
            pro_root=args.pro_root,
            cloud_root=args.cloud_root,
        )
    except AuditError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
