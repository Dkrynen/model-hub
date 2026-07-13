from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_pro_gate_config.py"
SOURCE = ROOT / "worker" / "wrangler.toml"


def _module():
    spec = importlib.util.spec_from_file_location("render_pro_gate_config", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _values(**overrides):
    digest = "a" * 64
    values = {
        "POLAR_ORG_ID": "11111111-1111-4111-8111-111111111111",
        "LOCAL_PRO_BENEFIT_ID": "22222222-2222-4222-8222-222222222222",
        "PRO_CLOUD_BENEFIT_ID": "33333333-3333-4333-8333-333333333333",
        "ENTITLEMENT_SIGNING_KID": "2026-primary",
        "ENTITLEMENT_SIGNING_PUBLIC_KEY": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",  # pragma: allowlist secret -- test public key
        "ARTIFACT_KEY": f"lac-pro/2.7.0/{digest}/lac-pro.zip",
        "ARTIFACT_FILENAME": "lac-pro.zip",
        "ARTIFACT_SHA256": digest,
        "R2_BUCKET_NAME": "lac-pro-private-staging",
    }
    values.update(overrides)
    return values


def test_render_replaces_only_target_environment_and_round_trips():
    module = _module()
    source = SOURCE.read_text(encoding="utf-8")
    rendered = module.render_config(source, "staging", _values())

    import tomllib

    parsed = tomllib.loads(rendered)
    staging = parsed["env"]["staging"]
    production = parsed["env"]["production"]
    assert staging["vars"]["POLAR_ORG_ID"] == _values()["POLAR_ORG_ID"]
    assert staging["vars"]["POLAR_API_BASE_URL"] == "https://sandbox-api.polar.sh/v1"
    assert staging["r2_buckets"][0]["bucket_name"] == "lac-pro-private-staging"
    assert production["vars"]["POLAR_ORG_ID"] == "replace-from-private-operator-notes"
    assert "ENTITLEMENT_SIGNING_PRIVATE_KEY" not in staging["vars"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"PRO_CLOUD_BENEFIT_ID": "22222222-2222-4222-8222-222222222222"},
        {"ENTITLEMENT_SIGNING_KID": "../../bad"},
        {"ENTITLEMENT_SIGNING_PUBLIC_KEY": "not-a-public-key"},
        {"ARTIFACT_SHA256": "not-a-digest"},
        {"ARTIFACT_KEY": "lac-pro/latest/lac-pro.zip"},
        {"R2_BUCKET_NAME": "Bad Bucket"},
    ],
)
def test_render_rejects_malformed_or_ambiguous_contract(overrides):
    module = _module()
    with pytest.raises(module.ConfigError):
        module.render_config(SOURCE.read_text(encoding="utf-8"), "production", _values(**overrides))


def test_cli_refuses_to_write_protected_config_inside_repo(monkeypatch, tmp_path):
    module = _module()
    for name, value in _values().items():
        monkeypatch.setenv(name, value)
    with pytest.raises(module.ConfigError, match="outside the repository"):
        module.main([
            "--environment", "staging",
            "--source", str(SOURCE),
            "--output", str(ROOT / "forbidden-protected-config.toml"),
        ])


def test_reported_errors_do_not_echo_protected_values():
    module = _module()
    private = "private-benefit-value"
    with pytest.raises(module.ConfigError) as exc:
        module.render_config(
            SOURCE.read_text(encoding="utf-8"),
            "production",
            _values(LOCAL_PRO_BENEFIT_ID=private),
        )
    assert private not in str(exc.value)
