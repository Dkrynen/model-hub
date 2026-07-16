from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pro-gate-deploy.yml"
TRUST_POLICY = ROOT / "docs" / "security" / "SUPPLY-CHAIN-TRUST.md"


def test_pro_gate_deploy_is_manual_protected_and_exact_commit_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    trust_root = "f6ccf527b493e97ab5138afa4306241677037492"  # pragma: allowlist secret
    yaml.safe_load(text)

    assert "workflow_dispatch:" in text
    assert "approved_commit:" in text
    assert "approved_tag:" in text
    assert "environment: pro-gate-${{ inputs.environment }}" in text
    assert "persist-credentials: false" in text
    assert ".commit.verification.verified" in text
    assert ".verification.verified == true" in text
    assert '.tag == $tag' in text
    assert 'git/ref/tags/$APPROVED_TAG' in text
    assert '.object.type == "tag"' in text
    assert '.object.sha == $object' in text
    assert 'test "$actual" = "$APPROVED_COMMIT"' in text
    assert 'git cat-file -t "refs/tags/$APPROVED_TAG"' in text
    assert "gh api --paginate --slurp" in text
    assert f'TRUST_ROOT_COMMIT="{trust_root}"' in text
    assert trust_root in TRUST_POLICY.read_text(encoding="utf-8")
    assert 'EXPECTED_WORKFLOW_REF="$GITHUB_REPOSITORY/.github/workflows/pro-gate-deploy.yml@refs/heads/master"' in text
    assert 'test "$GITHUB_WORKFLOW_REF" = "$EXPECTED_WORKFLOW_REF"' in text
    assert 'test "$(git cat-file -t "$TRUST_ROOT_COMMIT")" = "commit"' in text
    assert 'git merge-base --is-ancestor "$TRUST_ROOT_COMMIT" "$actual"' in text
    assert 'git rev-list "$TRUST_ROOT_COMMIT..$actual"' in text
    assert 'compare/$TRUST_ROOT_COMMIT...$actual?per_page=100' in text
    assert 'commits/$TRUST_ROOT_COMMIT' in text
    assert 'cmp -s "$RUNNER_TEMP/expected-commits.txt" "$RUNNER_TEMP/api-commits.txt"' in text
    assert 'git rev-list --count "$actual"' not in text


def test_pro_gate_deploy_requires_concrete_contract_secrets_and_staging_promotion():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--require-receipt-signing" in text
    assert "--worker-env \"${{ inputs.environment }}\"" in text
    assert "--allow-worker-placeholders" not in text
    assert "POLAR_TOKEN" in text
    assert "ENTITLEMENT_SIGNING_PRIVATE_KEY" in text
    assert "LAC_PRO_TEST_LOCAL_KEY" in text
    assert "LAC_PRO_TEST_CLOUD_KEY" in text
    assert "PRO_GATE_STAGING_TESTED_COMMIT" in text
    assert "wrangler secret list" in text
    assert "wrangler deploy --env" in text
    assert '--secrets-file "$SECRET_FILE"' in text
    assert "PRO_GATE_WAF_EVIDENCE" in text
    assert "https://sandbox-api.polar.sh/v1" in text
    assert "https://api.polar.sh/v1" in text
    assert "CF_VERSION_METADATA" in (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8")


def test_pro_gate_deploy_runs_both_tier_smokes_and_pins_actions():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--valid-key-env LAC_PRO_TEST_LOCAL_KEY" in text
    assert "--valid-key-env LAC_PRO_TEST_CLOUD_KEY" in text
    assert text.count("--live-gate") >= 2
    assert '--strict' in text
    assert '--tag "$APPROVED_COMMIT"' in text
    assert text.count('--expected-deployment-commit "$APPROVED_COMMIT"') >= 2
    assert 'trap rollback EXIT' in text
    assert 'wrangler rollback' in text
    assert '["ENTITLEMENT_SIGNING_PRIVATE_KEY"]' in text
    references = re.findall(r"^\s*-?\s*uses:\s*[^@\s]+@([0-9a-f]+)", text, re.MULTILINE)
    assert references
    assert all(len(reference) == 40 for reference in references)
