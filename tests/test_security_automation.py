from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dependabot_covers_supported_manifest_surfaces():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    surfaces = {
        (row["package-ecosystem"], row["directory"])
        for row in config["updates"]
    }

    assert config["version"] == 2
    assert surfaces == {
        ("github-actions", "/"),
        ("npm", "/web"),
        ("npm", "/worker"),
        ("pip", "/"),
    }


def test_release_lock_receives_a_routine_windows_vulnerability_audit():
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    assert "schedule:" in text
    assert "release-lock:" in text
    assert "runs-on: windows-latest" in text.split("  release-lock:\n", 1)[1]
    assert 'python -m pip install "pip-audit==2.10.1"' in text
    assert "python -m pip_audit -r requirements-release.lock" in text


def test_codeql_scans_python_and_javascript_with_bounded_permissions():
    path = ROOT / ".github" / "workflows" / "codeql.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert "security-events: write" in text
    assert "contents: read" in text
    assert "pull_request:" in text
    assert "schedule:" in text
    assert "python" in text
    assert "javascript-typescript" in text
    assert "github/codeql-action/init@99df26d4f13ea111d4ec1a7dddef6063f76b97e9" in text
    assert "github/codeql-action/analyze@99df26d4f13ea111d4ec1a7dddef6063f76b97e9" in text
    assert workflow["jobs"]["analyze"]["permissions"] == {
        "contents": "read",
        "security-events": "write",
    }
