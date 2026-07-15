# Supply-chain trust boundary

LAC's early public history predates enforced commit signing. Rewriting that published history would invalidate existing references without making the original commits retroactively signed, so release automation uses an explicit additive trust boundary.

The fixed trust-root commit is `f6ccf527b493e97ab5138afa4306241677037492`. <!-- pragma: allowlist secret -->

That commit was selected on 2026-07-15 as the first commit in the uninterrupted signed suffix of the repository history. The release workflow does not accept the value as operator input. Before a Pro gate deployment it requires:

1. the fixed trust root to exist and be an ancestor of the approved release commit;
2. GitHub to report the trust-root signature as valid;
3. the GitHub Compare API and the local clone to return the same exact descendant commit set; and
4. GitHub to report every descendant signature through the approved commit as valid.

The release tag is checked separately and must be an annotated, GitHub-verified signed tag that targets the exact release commit. The Windows candidate workflow performs that tag check before protected signing credentials are used.

This boundary establishes provenance, not approval. Public launch still requires protected branches and tags, required status checks, independent review, protected release environments, artifact signing and provenance, and the external evidence enforced by `scripts/enterprise_launch_gate.py`.

Changing this trust root requires a reviewed workflow and documentation change backed by a new contiguous-signature audit. It must never be supplied through workflow inputs or repository variables.
