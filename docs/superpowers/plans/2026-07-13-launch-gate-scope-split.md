# Launch-Gate Scope Split (Local vs Cloud) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `scripts/enterprise_launch_gate.py` into a LOCAL release scope (5 evidence gates + repo/installer lanes, passes with zero cloud evidence) and a CLOUD scope (today's full 19-gate behavior, unchanged), selected by `--release-scope` with default `cloud`.

**Architecture:** One script, scope-parameterized. Evidence manifests move to schema v3 with a mandatory `release_scope` field; Ed25519 signature payloads gain `release_scope` so records are scope-bound cryptographically and structurally. Local evidence records bind `model_hub_commit`, `lac_pro_commit`, `installer_sha256`, `release_provenance_sha256` — no `lac_cloud_commit`.

**Tech Stack:** Python 3 (repo venv at `.venv/Scripts/python`), pytest, `cryptography` (Ed25519). Windows host.

**Spec:** `docs/superpowers/specs/2026-07-13-launch-gate-scope-split-design.md` (approved).

## Global Constraints

- **Never weaken an evidence requirement.** Every gate keeps its exact validation logic and `EVIDENCE_MAX_AGE_DAYS` value. Only scope membership changes.
- Default scope is `cloud` = today's full behavior. `--release-scope` uses argparse `choices=("local", "cloud")`.
- LOCAL evidence gates are exactly: `patent_clearance`, `github_enterprise_controls`, `cryptographic_review`, `artifact_roundtrip`, `clean_machine_signed_install`.
- CLOUD evidence gates are exactly the existing 19-tuple `REQUIRED_EVIDENCE_GATES` (do not reorder or rename it).
- Evidence manifest schema v3: top-level fields exactly `{"schema_version", "release_scope", "release_version", "gates"}`; `schema_version == 3`; schema-v2 manifests fail closed in both scopes.
- The local gate must pass on a machine with **no lac-cloud checkout** and zero cloud evidence.
- Baseline: 34 tests green in `tests/test_enterprise_launch_gate.py` + `tests/test_release_workflow_contract.py`. Every task ends green.
- `tests/test_release_workflow_contract.py` must need **zero changes** (build.yml never invokes the gate). If a change there seems needed, stop and escalate.
- Repo: `C:\Users\User\repos\model-hub`, branch `master`, local-only — **never push**, never touch git config. Run tests with `.venv/Scripts/python -m pytest`.
- Master must be green after every commit (no cross-commit breakage; Task 2 patches the `build_report` call site in the same commit).

---

### Task 1: Scope constants and membership tests

**Files:**
- Modify: `scripts/enterprise_launch_gate.py` (constants block, after `REQUIRED_EVIDENCE_GATES` and after `_EVIDENCE_RECORD_FIELDS`)
- Test: `tests/test_enterprise_launch_gate.py`

**Interfaces:**
- Consumes: existing `REQUIRED_EVIDENCE_GATES` (19-tuple), `_EVIDENCE_BASE_FIELDS`, `EVIDENCE_MAX_AGE_DAYS`.
- Produces (later tasks rely on these exact names): `RELEASE_SCOPES: tuple[str, str]`, `EVIDENCE_SCHEMA_VERSION: int = 3`, `LOCAL_EVIDENCE_GATES: tuple[str, ...]` (5 names), `EVIDENCE_GATES_BY_SCOPE: dict[str, tuple[str, ...]]`, `_LOCAL_RELEASE_BINDING_FIELDS: set[str]`, `_LOCAL_EVIDENCE_RECORD_FIELDS: set[str]`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_enterprise_launch_gate.py`:

```python
def test_local_scope_membership_is_an_exact_subset_with_max_ages():
    gate = _load_gate()

    assert gate.RELEASE_SCOPES == ("local", "cloud")
    assert gate.EVIDENCE_SCHEMA_VERSION == 3
    assert gate.LOCAL_EVIDENCE_GATES == (
        "patent_clearance",
        "github_enterprise_controls",
        "cryptographic_review",
        "artifact_roundtrip",
        "clean_machine_signed_install",
    )
    assert gate.EVIDENCE_GATES_BY_SCOPE == {
        "local": gate.LOCAL_EVIDENCE_GATES,
        "cloud": gate.REQUIRED_EVIDENCE_GATES,
    }
    assert set(gate.LOCAL_EVIDENCE_GATES) < set(gate.REQUIRED_EVIDENCE_GATES)
    assert len(gate.REQUIRED_EVIDENCE_GATES) == 19
    for name in gate.REQUIRED_EVIDENCE_GATES:
        assert gate.EVIDENCE_MAX_AGE_DAYS[name] >= 1
    assert not (set(gate.LOCAL_EVIDENCE_GATES) & gate._WORKER_BOUND_EVIDENCE_GATES)
    assert gate._LOCAL_EVIDENCE_RECORD_FIELDS == (
        gate._EVIDENCE_BASE_FIELDS | {
            "model_hub_commit", "lac_pro_commit",
            "installer_sha256", "release_provenance_sha256",
        }
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py::test_local_scope_membership_is_an_exact_subset_with_max_ages -q`
Expected: FAIL with `AttributeError: ... has no attribute 'RELEASE_SCOPES'`

- [ ] **Step 3: Write minimal implementation** — in `scripts/enterprise_launch_gate.py`, directly after the `REQUIRED_EVIDENCE_GATES = (...)` tuple closes, insert:

```python
RELEASE_SCOPES = ("local", "cloud")
EVIDENCE_SCHEMA_VERSION = 3
LOCAL_EVIDENCE_GATES = (
    "patent_clearance",
    "github_enterprise_controls",
    "cryptographic_review",
    "artifact_roundtrip",
    "clean_machine_signed_install",
)
EVIDENCE_GATES_BY_SCOPE = {
    "local": LOCAL_EVIDENCE_GATES,
    "cloud": REQUIRED_EVIDENCE_GATES,
}
```

Then directly after the `_EVIDENCE_RECORD_FIELDS = _EVIDENCE_BASE_FIELDS | _EVIDENCE_RELEASE_BINDING_FIELDS` line, insert:

```python
_LOCAL_RELEASE_BINDING_FIELDS = {
    "model_hub_commit", "lac_pro_commit",
    "installer_sha256", "release_provenance_sha256",
}
_LOCAL_EVIDENCE_RECORD_FIELDS = _EVIDENCE_BASE_FIELDS | _LOCAL_RELEASE_BINDING_FIELDS
```

- [ ] **Step 4: Run the two gate test files, all green**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py tests/test_release_workflow_contract.py -q`
Expected: 35 passed

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\repos\model-hub
git add scripts/enterprise_launch_gate.py tests/test_enterprise_launch_gate.py
git commit -m "feat(release): define local/cloud launch-gate scope membership"
```

---

### Task 2: Scope-bound schema-v3 evidence verification

**Files:**
- Modify: `scripts/enterprise_launch_gate.py` — `evidence_signature_payload`, `_verify_evidence_record`, `check_evidence`, and the single `check_evidence(...)` call inside `build_report` (hardcode scope `"cloud"` there for now; Task 3 wires the CLI).
- Test: `tests/test_enterprise_launch_gate.py` — update helpers `_valid_evidence` and `_check_evidence` plus every existing call that breaks; add three new tests.

**Interfaces:**
- Consumes (from Task 1): `EVIDENCE_GATES_BY_SCOPE`, `EVIDENCE_SCHEMA_VERSION`, `_LOCAL_EVIDENCE_RECORD_FIELDS`.
- Produces (Task 3 relies on these exact signatures):
  - `evidence_signature_payload(gate: str, release_scope: str, release_version: str, record: dict[str, Any]) -> bytes`
  - `check_evidence(path: Path, release_scope: str, expected_version: str, *, expected_model_hub_commit: str = "", expected_lac_pro_commit: str = "", expected_lac_cloud_commit: str = "", expected_installer_sha256: str = "", expected_provenance_sha256: str = "", now: float | None = None) -> list[dict[str, Any]]`

- [ ] **Step 1: Update the two test helpers** in `tests/test_enterprise_launch_gate.py`.

Replace `_check_evidence` with:

```python
def _check_evidence(
    gate, path: Path, *, release_scope: str = "cloud",
    expected_version: str = "2.7.0", **overrides,
):
    expected = {
        "expected_model_hub_commit": MODEL_HUB_COMMIT,
        "expected_lac_pro_commit": LAC_PRO_COMMIT,
        "expected_lac_cloud_commit": CLOUD_COMMIT,
        "expected_installer_sha256": INSTALLER_SHA256,
        "expected_provenance_sha256": PROVENANCE_SHA256,
        "now": NOW,
    }
    expected.update(overrides)
    return gate.check_evidence(path, release_scope, expected_version, **expected)
```

In `_valid_evidence`: change the signature line `hosted_digests: dict[str, str],` to `hosted_digests: dict[str, str] | None = None,` and add a new keyword parameter `release_scope: str = "cloud",` after it. Replace the document literal and loop header:

```python
    document = {
        "schema_version": 3,
        "release_scope": release_scope,
        "release_version": "2.7.0",
        "gates": {},
    }
    for index, name in enumerate(
        gate.EVIDENCE_GATES_BY_SCOPE[release_scope], start=1,
    ):
```

Inside the loop, immediately after the `record = {...}` literal, add:

```python
        if release_scope == "local":
            del record["lac_cloud_commit"]
```

And change the two remaining lines that finish each record to bind the scope:

```python
        if name == "hosted_agent_end_to_end":
            record.update(hosted_digests or {})
        signature = private_key.sign(
            gate.evidence_signature_payload(name, release_scope, "2.7.0", record)
        )
```

(The `cloud_staging_smoke` / production / `measured_at` branches stay exactly as they are — those gate names never occur in local scope.)

- [ ] **Step 2: Update existing tests that call the changed APIs** in `tests/test_enterprise_launch_gate.py`:
  - `test_missing_evidence_fails_every_external_gate`: change `gate.check_evidence(tmp_path / "missing.json", "2.7.0")` to `gate.check_evidence(tmp_path / "missing.json", "cloud", "2.7.0")`.
  - `test_valid_evidence_requires_scoped_signature_exact_release_and_fresh_records`: change the restore line `evidence["schema_version"] = 2` to `evidence["schema_version"] = 3`. (The `= 1` rejection line stays.)
  - `test_build_report_derives_and_passes_exact_evidence_subject_bindings`: change `def fake_evidence(path, version, **expected):` to `def fake_evidence(path, release_scope, version, **expected):`.
  - No other existing test constructs manifests directly; all go through the helpers.

- [ ] **Step 3: Add four new failing tests** at the end of `tests/test_enterprise_launch_gate.py`:

```python
def test_signature_payload_binds_release_scope():
    gate = _load_gate()
    record = {"status": "verified"}

    local = gate.evidence_signature_payload(
        "patent_clearance", "local", "2.7.0", record,
    )
    cloud = gate.evidence_signature_payload(
        "patent_clearance", "cloud", "2.7.0", record,
    )

    assert local != cloud
    assert b'"release_scope":"local"' in local
    assert b'"release_scope":"cloud"' in cloud


def test_local_scope_evidence_passes_with_zero_cloud_evidence(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    evidence = _valid_evidence(gate, private_key, release_scope="local")
    path.write_text(json.dumps(evidence), encoding="utf-8")

    rows = _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    )

    assert {row["name"] for row in rows} == {
        f"evidence_{name}" for name in gate.LOCAL_EVIDENCE_GATES
    }
    assert all(row["ok"] for row in rows)
    assert all(
        "lac_cloud_commit" not in record
        for record in evidence["gates"].values()
    )


def test_local_and_cloud_manifests_cannot_cross_authorize(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)

    local_manifest = _valid_evidence(gate, private_key, release_scope="local")
    path.write_text(json.dumps(local_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))

    digests = _write_hosted_evidence_objects(path)
    cloud_manifest = _valid_evidence(gate, private_key, hosted_digests=digests)
    path.write_text(json.dumps(cloud_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    ))

    forged = _valid_evidence(gate, private_key, release_scope="local")
    forged["release_scope"] = "cloud"
    forged["gates"] = {
        name: forged["gates"].get(name)
        for name in gate.REQUIRED_EVIDENCE_GATES
        if forged["gates"].get(name) is not None
    }
    path.write_text(json.dumps(forged), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))


def test_schema_v2_manifests_fail_closed_in_both_scopes(tmp_path, monkeypatch):
    gate = _load_gate()
    path = tmp_path / "evidence.json"
    private_key = Ed25519PrivateKey.generate()
    _trust_evidence_signer(gate, private_key, monkeypatch)
    digests = _write_hosted_evidence_objects(path)

    cloud_manifest = _valid_evidence(gate, private_key, hosted_digests=digests)
    cloud_manifest["schema_version"] = 2
    path.write_text(json.dumps(cloud_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(gate, path))

    local_manifest = _valid_evidence(gate, private_key, release_scope="local")
    local_manifest["schema_version"] = 2
    path.write_text(json.dumps(local_manifest), encoding="utf-8")
    assert all(not row["ok"] for row in _check_evidence(
        gate, path, release_scope="local", expected_lac_cloud_commit="",
    ))
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py -q -k "cross_authorize or zero_cloud or v2_manifests or payload_binds"`
Expected: FAIL (TypeError on the old `check_evidence`/payload signatures or assertion failures)

- [ ] **Step 5: Implement in `scripts/enterprise_launch_gate.py`.**

Replace `evidence_signature_payload` entirely with:

```python
def evidence_signature_payload(
    gate: str, release_scope: str, release_version: str, record: dict[str, Any],
) -> bytes:
    signed = {key: value for key, value in record.items() if key != "signature"}
    return json.dumps(
        {
            "gate": gate,
            "record": signed,
            "release_scope": release_scope,
            "release_version": release_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
```

In `_verify_evidence_record`: add `release_scope: str,` as the second positional parameter (after `name`, before `release_version`). Replace the `expected_fields = (...)` expression at the top with:

```python
    if release_scope == "local":
        expected_fields = _LOCAL_EVIDENCE_RECORD_FIELDS
    else:
        expected_fields = (
            _HOSTED_JOURNEY_EVIDENCE_FIELDS if name == "hosted_agent_end_to_end"
            else _MEASURED_WORKER_EVIDENCE_FIELDS if name == "regional_latency_slo"
            else _WORKER_EVIDENCE_FIELDS if name in _WORKER_BOUND_EVIDENCE_GATES
            else _EVIDENCE_RECORD_FIELDS
        )
```

Replace the `expected_commits = {...}` literal with:

```python
    expected_commits = {
        "model_hub_commit": expected_model_hub_commit,
        "lac_pro_commit": expected_lac_pro_commit,
    }
    if release_scope != "local":
        expected_commits["lac_cloud_commit"] = expected_lac_cloud_commit
```

Change the final signature verification call to:

```python
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            evidence_signature_payload(name, release_scope, release_version, record),
        )
```

Everything else in `_verify_evidence_record` stays byte-identical (status/approver/freshness/worker/measured/hosted/signer checks).

In `check_evidence`: change the signature to add `release_scope: str` as the second positional parameter (see Interfaces block for the full signature). Then:
  - First line of the body: `required = EVIDENCE_GATES_BY_SCOPE[release_scope]`.
  - Replace every `REQUIRED_EVIDENCE_GATES` reference inside the function with `required` (the `missing` list comprehension, the `set(gates) != set(...)` check, both `for name in ...` loops).
  - Replace the top-level document field-set check with:

```python
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "release_scope", "release_version", "gates",
    }:
        return [{**row, "detail": "evidence manifest is invalid"} for row in missing]
    version_ok = (
        document.get("schema_version") == EVIDENCE_SCHEMA_VERSION
        and document.get("release_scope") == release_scope
        and document.get("release_version") == expected_version
    )
```

  - Pass the scope through to the record verifier: `verified[name] = version_ok and _verify_evidence_record(name, release_scope, expected_version, record, ...)` (keyword arguments unchanged).
  - Wrap the hosted-journey object check **and** the production-deployment binding cross-match block in `if release_scope == "cloud":` (indent both blocks one level; they reference gates that only exist in cloud scope).

In `build_report`: change the `check_evidence(` call to `check_evidence(args.evidence, "cloud", APP_VERSION, ...)` — insert the literal `"cloud"` as the second argument, everything else unchanged. (Task 3 replaces the literal with the parsed scope.)

- [ ] **Step 6: Run the full gate test files**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py tests/test_release_workflow_contract.py -q`
Expected: 39 passed

- [ ] **Step 7: Commit**

```powershell
cd C:\Users\User\repos\model-hub
git add scripts/enterprise_launch_gate.py tests/test_enterprise_launch_gate.py
git commit -m "feat(release): scope-bound schema-v3 evidence verification"
```

---

### Task 3: `--release-scope` CLI wiring and local report

**Files:**
- Modify: `scripts/enterprise_launch_gate.py` — `parse_args`, `build_report`, module docstring line in `argparse.ArgumentParser(description=...)` may stay.
- Test: `tests/test_enterprise_launch_gate.py`

**Interfaces:**
- Consumes (from Tasks 1-2): `RELEASE_SCOPES`, `EVIDENCE_GATES_BY_SCOPE`, new `check_evidence(path, release_scope, version, ...)`.
- Produces: `args.release_scope: str`; report JSON with top-level `"schema_version": 2` and `"release_scope": <scope>`; in local scope the report contains no `lac_cloud_*` checks and no `cloud_product` lane.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_enterprise_launch_gate.py`:

```python
def test_release_scope_defaults_to_cloud():
    gate = _load_gate()

    args = gate.parse_args([])
    assert args.release_scope == "cloud"
    assert gate.parse_args(["--release-scope", "local"]).release_scope == "local"


def test_local_scope_report_omits_cloud_lanes_and_binds_scope(tmp_path):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")

    rc = gate.main([
        "--release-scope", "local",
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(tmp_path / "does-not-exist"),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])
    assert rc == 1


def test_local_scope_report_content(tmp_path, capsys):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")

    gate.main([
        "--release-scope", "local",
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(tmp_path / "does-not-exist"),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])

    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 2
    assert report["release_scope"] == "local"
    names = [row["name"] for row in report["checks"]]
    lanes = {row["lane"] for row in report["checks"]}
    assert not any(name.startswith("lac_cloud_") for name in names)
    assert "cloud_product" not in lanes
    assert {
        f"evidence_{name}" for name in gate.LOCAL_EVIDENCE_GATES
    } == {name for name in names if name.startswith("evidence_")}
    assert any(name.startswith("model_hub_") for name in names)
    assert any(name.startswith("lac_pro_") for name in names)
    assert any(name == "installer_exists" for name in names)


def test_cloud_scope_report_keeps_full_lane_set(tmp_path, capsys):
    gate = _load_gate()
    model = _repo(tmp_path, "model-hub")
    pro = _repo(tmp_path, "lac-pro")
    cloud = _repo(tmp_path, "lac-cloud")

    gate.main([
        "--repo-root", str(model),
        "--lac-pro-root", str(pro),
        "--lac-cloud-root", str(cloud),
        "--evidence", str(tmp_path / "missing.json"),
        "--installer", str(tmp_path / "missing-installer.exe"),
    ])

    report = json.loads(capsys.readouterr().out)
    assert report["release_scope"] == "cloud"
    names = [row["name"] for row in report["checks"]]
    assert any(name.startswith("lac_cloud_") for name in names)
    assert "cloud_product_local_complete" in names
    assert {
        f"evidence_{name}" for name in gate.REQUIRED_EVIDENCE_GATES
    } == {name for name in names if name.startswith("evidence_")}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py -q -k "release_scope or report_content or full_lane_set"`
Expected: FAIL (`unrecognized arguments: --release-scope` and missing report keys)

- [ ] **Step 3: Implement.**

In `parse_args`, after the `--repo-root` argument, add:

```python
    parser.add_argument(
        "--release-scope",
        choices=RELEASE_SCOPES,
        default="cloud",
        help="Which release this run authorizes: the local installer release "
        "or the full cloud launch.",
    )
```

Replace `build_report` entirely with:

```python
def build_report(args: argparse.Namespace) -> dict[str, Any]:
    release_scope = args.release_scope
    source = _git(args.repo_root, "rev-parse", "HEAD")
    source_commit = source.stdout.strip() if source.returncode == 0 else ""
    pro_source = _git(args.lac_pro_root, "rev-parse", "HEAD")
    pro_source_commit = pro_source.stdout.strip() if pro_source.returncode == 0 else ""
    cloud_source_commit = ""
    installer_sha256 = _evidence_subject_sha256(args.installer)
    provenance_sha256 = _evidence_subject_sha256(
        args.provenance, max_bytes=256 * 1024,
    )
    checks = [
        *check_repository(
            "model_hub",
            args.repo_root,
            required_remote="https://github.com/Dkrynen/lac.git",
            base_commit=MODEL_HUB_RELEASE_BASE,
            release_tag=f"v{APP_VERSION}",
            expected_tag_target=source_commit,
        ),
        *check_repository(
            "lac_pro",
            args.lac_pro_root,
            require_zero_remotes=True,
            base_commit=LAC_PRO_RELEASE_BASE,
        ),
    ]
    if release_scope == "cloud":
        cloud_source = _git(args.lac_cloud_root, "rev-parse", "HEAD")
        cloud_source_commit = (
            cloud_source.stdout.strip() if cloud_source.returncode == 0 else ""
        )
        checks += [
            *check_repository(
                "lac_cloud",
                args.lac_cloud_root,
                required_remote="https://github.com/Acend-co/lac-cloud.git",
            ),
            check_cloud_product_readiness(args.lac_cloud_root),
        ]
    checks += [
        *check_installer(
            args.installer,
            args.checksums,
            args.application,
            args.provenance,
            source_commit,
            args.repo_root / "requirements-release.lock",
            args.python_sbom,
            args.web_sbom,
        ),
        *check_evidence(
            args.evidence,
            release_scope,
            APP_VERSION,
            expected_model_hub_commit=source_commit,
            expected_lac_pro_commit=pro_source_commit,
            expected_lac_cloud_commit=cloud_source_commit,
            expected_installer_sha256=installer_sha256,
            expected_provenance_sha256=provenance_sha256,
        ),
    ]
    failed = [row for row in checks if not row["ok"]]
    return {
        "schema_version": 2,
        "release_scope": release_scope,
        "release_version": APP_VERSION,
        "ready": not failed,
        "failed_count": len(failed),
        "checks": checks,
    }
```

Also update the existing test `test_build_report_derives_and_passes_exact_evidence_subject_bindings`: its `fake_git` asserts `rev-parse HEAD` per repo via the `commits` dict — in default cloud scope all three repos are still resolved, so it needs no change beyond Task 2's `fake_evidence` signature fix. Verify the existing `test_main_returns_nonzero_and_emits_json_for_currently_blocked_fixture` still passes (it uses default cloud scope).

- [ ] **Step 4: Run the full gate test files**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py tests/test_release_workflow_contract.py -q`
Expected: 43 passed

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\repos\model-hub
git add scripts/enterprise_launch_gate.py tests/test_enterprise_launch_gate.py
git commit -m "feat(release): --release-scope wiring - local gate passes with zero cloud evidence"
```

---

### Task 4: Documentation

**Files:**
- Modify: `docs/release/enterprise-launch-gate.md`
- Modify: `CHANGELOG.md` (top `## Unreleased`-style section; follow the file's existing heading pattern)

**Interfaces:**
- Consumes: final CLI/schema from Tasks 1-3. No code changes.

- [ ] **Step 1: Add a "Release scopes" section** to `docs/release/enterprise-launch-gate.md`, inserted directly after the opening paragraph/run example, containing:

```markdown
## Release scopes

The gate authorizes two different releases, selected with `--release-scope`
(default `cloud`, the strictest scope):

```powershell
# Full cloud launch gate (default) - all 19 evidence gates
python scripts/enterprise_launch_gate.py `
  --evidence C:\private\LAC-Launch-Evidence\2.7.0.json

# Local installer release gate - passes with zero cloud evidence
python scripts/enterprise_launch_gate.py --release-scope local `
  --evidence C:\private\LAC-Launch-Evidence\2.7.0-local.json
```

| Scope | Evidence gates | Extra lanes |
|---|---|---|
| `local` | `patent_clearance`, `github_enterprise_controls`, `cryptographic_review`, `artifact_roundtrip`, `clean_machine_signed_install` | model-hub + lac-pro repository checks, full installer/provenance/SBOM/attestation lane |
| `cloud` | all 19 required gates | everything in `local` plus lac-cloud repository checks and the strict hosted product-readiness probe |

The evidence manifest is schema v3 and scope-bound: it must carry
`"release_scope"` matching the invoked scope, its gate set must exactly equal
that scope's required set, and every Ed25519 record signature covers the
scope. Local records bind `model_hub_commit`, `lac_pro_commit`,
`installer_sha256`, and `release_provenance_sha256` and must not contain
`lac_cloud_commit`. A local manifest cannot authorize the cloud launch, and a
cloud manifest cannot authorize the local release. Schema-v2 manifests fail
closed in both scopes. No evidence requirement was weakened by the split:
every gate keeps its exact validation logic and maximum age, and the cloud
launch still requires all nineteen.
```

Also update the existing "Evidence manifest" section: change `"schema_version": 2` to `"schema_version": 3` in the JSON example and add `"release_scope": "cloud",` directly beneath it, and update the sentence "Schema v2 requires an exact top-level field set" to say "Schema v3 requires an exact top-level field set (`schema_version`, `release_scope`, `release_version`, `gates`)".

- [ ] **Step 2: Add a CHANGELOG entry** following the file's existing style, at the top of the current unreleased section:

```markdown
- **Launch-gate release scopes** - `enterprise_launch_gate.py` now takes `--release-scope {local,cloud}` (default `cloud`). The `local` scope gates the signed installer release on 5 evidence gates plus the repository and installer lanes and passes with zero cloud evidence; the `cloud` scope keeps all 19 gates fail-closed. Evidence manifests are schema v3 and scope-bound (signatures cover the scope).
```

- [ ] **Step 3: Run the full gate test files one final time**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py tests/test_release_workflow_contract.py -q`
Expected: 43 passed

- [ ] **Step 4: Commit**

```powershell
cd C:\Users\User\repos\model-hub
git add docs/release/enterprise-launch-gate.md CHANGELOG.md
git commit -m "docs(release): document launch-gate release scopes"
```
