from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_requires_exact_source_version_and_protected_controls():
    text = _workflow()

    assert "scripts/verify_release_version.py --expected $tag" in text
    assert "scripts/pro_commerce_readiness.py --require-baked-gate --allow-missing-lac-pro" in text
    assert "--worker-env production --allow-worker-placeholders" in text
    assert text.count('--expected-deployment-commit "${{ github.sha }}"') >= 2
    assert "--valid-key-env LAC_PRO_TEST_LOCAL_KEY" in text
    assert "--valid-key-env LAC_PRO_TEST_CLOUD_KEY" in text
    assert "PRO_GATE_TESTED_VERSION" in text
    assert "$env:PRO_GATE_TESTED_VERSION -ne $env:GITHUB_SHA" in text
    assert "PRO_GATE_WAF_EVIDENCE" in text
    assert "REQUESTED_RELEASE_TAG:" in text
    assert "$tag = $env:REQUESTED_RELEASE_TAG" in text
    assert '$tag = "${{' not in text
    assert 'git rev-parse --verify "refs/tags/$tag^{commit}"' in text
    assert "$tagCommit -ne $env:GITHUB_SHA" in text
    assert 'git cat-file -t "refs/tags/$tag"' in text
    assert '"/repos/$env:GITHUB_REPOSITORY/git/tags/$tagObject"' in text
    assert "$tagRecord.verification.verified -ne $true" in text
    assert "$tagRecord.tag -ne $tag" in text
    assert "persist-credentials: false" in text
    assert '$env:GITHUB_REF -ne "refs/tags/$tag"' in text
    assert "group: release-${{ github.event_name == 'workflow_dispatch' && inputs.version || github.ref_name }}" in text
    assert '"/repos/$env:GITHUB_REPOSITORY/git/ref/tags/$tag"' in text
    assert "$tagRefRecord.object.sha -ne $tagObject" in text
    assert "production-release" in text
    assert "INNO_SETUP_LICENSE_CONFIRMED" in text
    assert "SIGNING_CERTIFICATE_PFX_BASE64" in text
    assert "SIGNING_CERTIFICATE_PASSWORD" in text
    assert "SIGNING_TIMESTAMP_URL" in text
    assert "-replace '#define MyAppVersion" not in text
    assert text.index("Resolve and verify the immutable release version") < text.index(
        "Require protected release controls"
    )


def test_release_workflow_signs_payload_then_installer_and_verifies_both():
    text = _workflow()

    application_sign = text.index("dist\\lac\\lac.exe")
    installer_build = text.index("ISCC.exe")
    installer_sign = text.index("Installer signing failed")

    assert application_sign < installer_build < installer_sign
    assert text.count("Get-AuthenticodeSignature") == 2
    assert 'if ($signature.Status -ne "Valid")' in text
    assert "release-provenance.json" in text
    assert "SHA256SUMS.txt" in text
    assert "requirements-release.lock" in text
    assert "--require-hashes" in text
    assert "dependency_lock_sha256" in text
    assert "schema_version = 2" in text
    assert '$pythonSbomItem = Get-Item "dist\\python-sbom.json"' in text
    assert '$webSbomItem = Get-Item "web\\dist\\web-sbom.json"' in text
    assert "python_sbom = [ordered]@{" in text
    assert "web_sbom = [ordered]@{" in text
    assert 'Copy-Item -LiteralPath "web\\dist\\web-sbom.json" -Destination "dist\\web-sbom.json"' in text
    assert "            dist/web-sbom.json" in text
    assert "            web/dist/web-sbom.json" not in text
    assert "actions/attest-build-provenance@" in text
    assert "Get-Rfc3161SignatureEvidence" in text
    assert 'protocol = "RFC3161"' in text
    assert "TimeStamperCertificate" in text
    assert "timestamped_at_utc" in text
    assert 'python -m pytest -m "not live"' in text
    assert "detect-secrets-hook --baseline .secrets.baseline" in text
    assert "npx wrangler deploy --dry-run --env production" in text


def test_release_workflow_attests_every_exact_release_subject():
    text = _workflow()
    attest_start = text.index("      - name: Attest the signed release provenance")
    attest_end = text.index("      - name: Upload signed release candidate", attest_start)
    attestation_step = text[attest_start:attest_end]

    assert "          subject-path: |" in attestation_step
    assert [
        line.strip()
        for line in attestation_step.splitlines()
        if line.startswith("            dist/")
    ] == [
        "dist/LAC-Setup-${{ steps.release_version.outputs.version }}.exe",
        "dist/lac/lac.exe",
        "dist/SHA256SUMS.txt",
        "dist/release-provenance.json",
        "dist/python-sbom.json",
        "dist/web-sbom.json",
    ]


def test_signed_candidate_retains_packaged_application_without_publishing_it_directly():
    text = _workflow()
    upload_start = text.index("      - name: Upload signed release candidate")
    candidate_step = text[upload_start:]

    assert "            dist/lac/lac.exe" in candidate_step
    assert "            release/lac/lac.exe" not in text


def test_release_workflow_pins_every_third_party_action_to_a_commit():
    text = _workflow()
    uses_lines = [
        line.strip().removeprefix("- ")
        for line in text.splitlines()
        if line.strip().removeprefix("- ").startswith("uses:")
    ]

    assert uses_lines
    for line in uses_lines:
        reference = line.split("@", 1)[1].split()[0]
        assert len(reference) == 40
        assert all(character in "0123456789abcdef" for character in reference)


def test_release_workflow_is_candidate_only_until_the_enterprise_gate_can_run():
    text = _workflow()

    assert text.startswith("name: Signed Windows Release Candidate\n")
    assert "draft-release" not in text
    assert "softprops/action-gh-release" not in text
    assert "contents: write" not in text
    assert "draft: true" not in text
    assert "draft: false" not in text


def test_python_sbom_step_creates_root_dist_before_writing_into_it():
    text = _workflow()
    step_start = text.index("      - name: Audit and install Python dependencies")
    step_end = text.index("      - name: Run the complete source and secret gates", step_start)
    step = text[step_start:step_end]

    create_dist = "New-Item -ItemType Directory -Force -Path dist | Out-Null"
    write_sbom = "python -m cyclonedx_py environment --output-file dist/python-sbom.json"
    assert create_dist in step
    assert write_sbom in step
    assert step.index(create_dist) < step.index(write_sbom)


def test_release_secrets_are_scoped_to_the_steps_that_need_them():
    text = _workflow()
    job_prefix = text.split("    steps:", 1)[0]

    assert "SIGNING_CERTIFICATE_PFX_BASE64" not in job_prefix
    assert "SIGNING_CERTIFICATE_PASSWORD" not in job_prefix
