# LAC 2.7 Enterprise Launch Gate

`scripts/enterprise_launch_gate.py` is the final fail-closed, read-only release
gate for the coordinated Local Pro and Pro Cloud launch. It does not deploy,
publish, purchase, modify a repository, or read credentials.

Run it from `model-hub`:

```powershell
python scripts/enterprise_launch_gate.py `
  --evidence C:\private\LAC-Launch-Evidence\2.7.0.json
```

Exit code `0` means every local and externally evidenced gate passed. Exit code
`1` means checkout and publication must remain closed. The JSON output names
every failing gate without including remote URLs, credential values, evidence
references, or approver names.

## Evidence manifest

The evidence file is operator supplied and must stay outside the repository. It
contains references to authoritative records, not reports or secrets themselves.
Every record is signed independently with an allowlisted, gate-scoped Ed25519
review key:

```json
{
  "schema_version": 1,
  "release_version": "2.7.0",
  "gates": {
    "patent_clearance": {
      "status": "approved",
      "approver": "responsible-reviewer",
      "reference": "authoritative-record-reference",
      "recorded_at": "2026-07-13T00:00:00Z",
      "record_sha256": "64-hex-digest-of-the-authoritative-record",
      "signer_kid": "approved-review-key-id",
      "signature": "base64url-ed25519-signature"
    }
  }
}
```

Every required gate uses the same record shape. Accepted status values are
`approved`, `passed`, and `verified`. Placeholder, pending, unsigned, stale,
future-dated, untrusted, wrong-version, or malformed records fail closed. Trust
roots are empty by default and must be onboarded in a reviewed source commit;
an operator-supplied file cannot add its own signer.

The required gates are defined in `REQUIRED_EVIDENCE_GATES` inside the script
and cover patent clearance, GitHub governance, Polar readiness, Cloudflare
account ownership, Turnstile and WAF validation, staging and production smokes,
paid beta, penetration and cryptographic review, remediation, incident and
recovery drills, artifact roundtrip, and clean-machine signed installation.

## Repository and artifact checks

The gate also verifies:

- `model-hub`, `lac-pro`, and `lac-cloud` are Git repositories with clean trees;
- every unpublished `model-hub` commit after the immutable public-upstream
  ancestor, plus each private launch-range commit, has a good signature from an
  allowlisted signer;
- `lac-pro` has zero remotes;
- `lac-cloud` has the approved `Acend-co/lac-cloud` remote;
- the exact 2.7.0 installer exists and matches `SHA256SUMS.txt`;
- both the installer and packaged `lac.exe` have an allowlisted Authenticode
  subject and thumbprint, plus a verified RFC3161 timestamp whose timestamping
  certificate has the correct EKU and was valid at the recorded signing time;
- `release-provenance.json` binds the version, source commit, dependency lock,
  installer and application file sizes, checksums, signature states, and exact
  RFC3161 timestamp-certificate evidence; and
- `gh attestation verify` confirms GitHub's signed SLSA provenance for the
  exact installer, source commit, release tag, hosted runner, and pinned build
  workflow.

The gate records only counts and pass/fail state for Git policy checks. It does
not expose remote addresses from the machine or the contents of the evidence
manifest.
