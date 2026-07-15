import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"


def _job(text: str, name: str, next_name: str) -> str:
    return text.split(f"  {name}:\n", 1)[1].split(f"\n  {next_name}:\n", 1)[0]


def test_python_ci_installs_the_pinned_build_test_dependency():
    python_job = _job(WORKFLOW.read_text(encoding="utf-8"), "python", "web")

    assert "pyinstaller==6.21.0" in python_job


def test_worker_ci_uses_the_node_version_required_by_wrangler():
    worker_job = _job(WORKFLOW.read_text(encoding="utf-8"), "worker", "secrets")

    assert "node-version: 22.19.0" in worker_job


def test_ci_runs_for_stacked_pull_requests_not_only_default_branch_targets():
    text = WORKFLOW.read_text(encoding="utf-8")
    pull_request_trigger = text.split("  pull_request:\n", 1)[1].split("\npermissions:\n", 1)[0]

    assert "branches:" not in pull_request_trigger


def test_every_workflow_action_is_pinned_to_an_exact_commit():
    workflows = [
        *(ROOT / ".github" / "workflows").glob("*.yml"),
        *(ROOT / ".github" / "workflows").glob("*.yaml"),
    ]
    for workflow in sorted(workflows):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().removeprefix("- ")
            if not stripped.startswith("uses:"):
                continue
            reference = stripped.split("@", 1)[1].split()[0]
            assert re.fullmatch(r"[0-9a-f]{40}", reference), workflow.name
