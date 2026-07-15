## Summary

<!-- What changed, and why? -->

## Verification

- [ ] Tests covering the change were added or updated first where practical.
- [ ] Relevant Python tests passed.
- [ ] Relevant web tests, typecheck, production build, and bundle check passed.
- [ ] `git diff --check` passed.

## Public and product boundaries

- [ ] Public claims match verified behavior; limitations and unavailable features remain explicit.
- [ ] Privacy and security documentation still matches every changed network or data boundary.
- [ ] No secrets, credentials, tokens, personal data, private evidence, or generated release artifacts are included.
- [ ] LAC Core does not import or bundle `lac_pro`; free releases ship no proprietary code.
- [ ] Local desktop and Cloud readiness are reported separately.

## Release safety

- [ ] This change does not bypass or weaken a launch gate.
- [ ] Any merge-triggered deployment is named in this pull request; `site/**` changes on `master` deploy GitHub Pages.
- [ ] No release tag, package publication, application/Worker deployment, or public release is implied by merging this pull request.
- [ ] Signing, provenance, legal, security, clean-machine, and production-infrastructure evidence remains fail-closed until independently verified.
