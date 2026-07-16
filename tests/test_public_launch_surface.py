from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_security_policy_has_a_private_reporting_path_and_support_scope():
    policy = _read("SECURITY.md")

    assert "https://github.com/Dkrynen/lac/security/advisories/new" in policy
    assert "Do not open a public issue" in policy
    assert "Supported versions" in policy
    assert "latest published release" in policy


def test_privacy_disclosure_names_every_optional_network_boundary():
    privacy = _read("PRIVACY.md").lower()
    server = _read("server.py").lower()

    assert "no telemetry" in privacy
    assert "configured ollama endpoint" in privacy
    assert "may be remote" in privacy
    assert "ollama.com/library" in privacy
    assert "automatically" in privacy
    assert "github" in privacy
    assert "source/headless" in privacy
    assert "frozen windows desktop" in privacy
    assert "hugging face" in privacy
    assert "hf_token.json" in privacy
    assert "plaintext" in privacy
    assert "fine-grained read-only" in privacy
    assert "openai" in privacy
    assert "anthropic" in privacy
    assert "mcp" in privacy
    assert "duckduckgo" in privacy
    assert "pro activation" in privacy
    assert "hostname" in privacy
    assert "periodic entitlement" in privacy
    assert "model execution and tuning stay local" not in privacy
    assert "cloud execution remains disabled" in privacy
    assert "cookbook.db" in privacy
    assert "staged file" in privacy
    assert "uninstalling lac does not delete" in privacy
    assert "unauthenticated state-changing api" in privacy
    assert "unauthenticated api" in server


def test_public_entrypoints_link_policies_and_avoid_absolute_locality_claims():
    readme = _read("README.md")
    landing = _read("site/index.html")

    assert "[Security](SECURITY.md)" in readme
    assert "[Privacy](PRIVACY.md)" in readme
    assert "https://github.com/Dkrynen/lac/blob/master/SECURITY.md" in landing
    assert "https://github.com/Dkrynen/lac/blob/master/PRIVACY.md" in landing
    assert "Your chats and model execution stay local." not in landing
    assert "configured Ollama endpoint" in landing
    assert "configured providers" in landing
    assert "periodic entitlement validation" in landing
    assert "Model execution and tuning stay local" not in landing
    assert "The activated runtime stays local" not in landing
    assert "Studio" in landing
    assert "Lab" in landing


def test_installed_entrypoints_keep_public_policies_and_repository_links_valid():
    installer = _read("installer.iss")
    docs_page = _read("web/src/pages/docs.tsx")

    assert 'Source: "SECURITY.md"; DestDir: "{app}"' in installer
    assert 'Source: "PRIVACY.md"; DestDir: "{app}"' in installer
    assert "https://github.com/Dkrynen/lac" in docs_page
    assert "https://github.com/Dkrynen/model-hub" not in docs_page


def test_pull_request_template_preserves_release_and_open_core_boundaries():
    template = _read(".github/pull_request_template.md").lower()

    assert "tests" in template
    assert "public claims" in template
    assert "no secrets" in template
    assert "lac_pro" in template
    assert "launch gate" in template
    assert "`site/**` changes on `master` deploy github pages" in template
    assert "application/worker deployment" in template
