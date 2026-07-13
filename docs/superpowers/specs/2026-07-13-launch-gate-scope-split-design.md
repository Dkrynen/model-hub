# Launch-Gate Scope Split (Local vs Cloud) — Design

**Date:** 2026-07-13
**Status:** Approved by Duan (design gate passed)
**Owner file:** `scripts/enterprise_launch_gate.py`
**Tests:** `tests/test_enterprise_launch_gate.py`, `tests/test_release_workflow_contract.py`

## Problem

The v2.7.0 RC is blocked by `scripts/enterprise_launch_gate.py`: a single
fail-closed gate binds 19 evidence gates — including cloud staging/production
smokes, the regional latency SLO, and the hosted-agent end-to-end journey — but
the hosted backend is months out (lac-cloud, Morne's workstream). The local
installer release and the Pro Cloud launch need separate gates.

**Hard constraint:** never weaken an evidence requirement. Only re-scope which
release each gate binds. Every gate keeps its exact validation logic and
max-age.

## Decision summary

One script, two scopes, selected by `--release-scope {local,cloud}`.

- `cloud` (**default**) — today's full gate, unchanged in strictness.
  Authorizes the coordinated Pro Cloud launch.
- `local` — authorizes only publishing the signed v2.7.0 installer. Passes
  with zero cloud evidence and no lac-cloud checkout on the machine.

Defaulting to `cloud` keeps every existing invocation at maximum strictness.
An unknown scope is unrepresentable (argparse `choices`).

## Gate membership

### Evidence gates by scope

| Scope | Evidence gates |
|---|---|
| LOCAL (5) | `patent_clearance`, `github_enterprise_controls`, `cryptographic_review`, `artifact_roundtrip`, `clean_machine_signed_install` |
| CLOUD (19) | all LOCAL gates + `polar_products_ready`, `cloudflare_account_boundary`, `turnstile_validation`, `waf_abuse_protection`, `cloud_staging_smoke`, `cloud_production_dark_smoke`, `regional_latency_slo`, `hosted_agent_end_to_end`, `private_paid_beta`, `external_pentest`, `remediation_verified`, `incident_response_tabletop`, `credential_rotation_drill`, `restore_rollback_deletion_drills` |

LOCAL is a strict subset of CLOUD. Max-ages (`EVIDENCE_MAX_AGE_DAYS`) are
unchanged for every gate.

Binding decisions (Duan, 2026-07-13):

- `polar_products_ready` → CLOUD only. v2.7.0 public release ships without
  live checkout; Polar readiness gates the commerce launch.
- `external_pentest` + `remediation_verified` → CLOUD only. Pentest is scoped
  to the hosted attack surface.
- `cryptographic_review` → BOTH. Entitlement-receipt and evidence-signing
  crypto ships in the local release.
- `private_paid_beta` → CLOUD only. The paid beta evidences the hosted tier.
- `incident_response_tabletop`, `credential_rotation_drill`,
  `restore_rollback_deletion_drills` → CLOUD only (hosted-ops drills).
- `github_enterprise_controls` → BOTH (repo governance protects the public
  release pipeline itself).

### Non-evidence lanes by scope

| Lane | LOCAL | CLOUD |
|---|---|---|
| model-hub repo (clean, signed range, approved remote, signed `v2.7.0` tag) | ✔ | ✔ |
| lac-pro repo (clean, signed range, zero remotes) | ✔ | ✔ |
| Installer lane (exists, checksum, both Authenticode signatures, provenance v2, SBOM bindings, `gh attestation verify` on all six subjects) | ✔ | ✔ |
| lac-cloud repo (clean, signed, approved remote) | — | ✔ |
| `cloud_product_local_complete` (strict product-readiness probe) | — | ✔ |

The handoff's "secrets sweep + local smokes" requirements are enforced
transitively in LOCAL scope: the SLSA attestation binds the exact pinned
workflow, and `tests/test_release_workflow_contract.py` pins
`detect-secrets-hook --baseline .secrets.baseline` and
`python -m pytest -m "not live"` inside that workflow.

## Evidence manifest schema v3

Top-level document becomes:

```json
{
  "schema_version": 3,
  "release_scope": "local",
  "release_version": "2.7.0",
  "gates": { "...": "exactly the invoked scope's required gate set" }
}
```

- `release_scope` must equal the invoked scope; the `gates` key set must
  exactly equal that scope's required set. Anything else fails every gate.
- **Local records** bind `model_hub_commit`, `lac_pro_commit`,
  `installer_sha256`, `release_provenance_sha256` — no `lac_cloud_commit`
  field. Exact-field-set checks mean a cloud record pasted into a local
  manifest fails structurally, and vice versa.
- **Cloud records** are unchanged from schema v2 semantics (all five bindings,
  worker version fields on the worker-bound gates, `measured_at`, hosted
  journey digests). No worker-bound gate exists in LOCAL scope.
- The Ed25519 signature payload gains `release_scope`:
  `{"gate", "release_scope", "release_version", "record"}` (ASCII, sorted
  keys, compact separators, as today). Records are scope-bound
  cryptographically as well as structurally.
- Schema-v2 manifests are rejected in **both** scopes. This break is free:
  trust roots are empty; no production record has ever been signed.

## Report and CLI

- Report JSON gains `"release_scope"` and bumps report `schema_version`
  1 → 2. Exit semantics unchanged (0 = ready, 1 = closed).
- `parse_args` gains `--release-scope` with `choices=("local", "cloud")`,
  default `cloud`. `--lac-cloud-root` remains accepted but is not consulted
  in local scope.
- In local scope `build_report` does not resolve the lac-cloud HEAD and does
  not emit lac-cloud lanes; `check_evidence` receives no lac-cloud
  expectation.

## Testing

Baseline: 34 tests green across the two files (verified 2026-07-13).

- Existing evidence fixtures move to schema v3 + `release_scope` + new
  signature payload. `test_release_workflow_contract.py` is expected to need
  zero changes (`build.yml` never invokes the gate).
- New tests:
  - Local scope passes end-to-end with zero cloud evidence and no lac-cloud
    directory present.
  - A local manifest cannot authorize cloud scope and vice versa — both the
    structural path (field sets, gate sets, `release_scope` mismatch) and the
    signature path (scope-bound payload).
  - Local report contains no lac-cloud or cloud-product lanes.
  - Default scope is `cloud` and equals today's full 19-gate behavior.
  - Membership assertions: LOCAL ⊂ CLOUD, exact 5/19 sets, every gate in
    both sets has a max-age.
  - Schema-v2 manifests fail closed in both scopes.

## Docs

- `docs/release/enterprise-launch-gate.md`: add a Release Scopes section with
  the membership table and local/cloud invocation examples.
- `CHANGELOG.md`: entry for the scope split.

## Out of scope

- The protected publication workflow that will invoke
  `--release-scope local` (future work).
- Any change to lac-cloud, its product-readiness CLI, or Morne's backlog.
- Onboarding trust roots (signers stay empty until a reviewed commit adds
  them).
