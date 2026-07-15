from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APP_URL = "http://127.0.0.1:5050"
DEFAULT_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def tail_text(value: str, *, limit: int = 3000) -> str:
    value = value or ""
    return value[-limit:] if len(value) > limit else value


def npm_cmd() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    lane: str,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "lane": lane,
            "name": name,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            "cwd": str(cwd),
            "command": command,
            "stdout_tail": tail_text(proc.stdout),
            "stderr_tail": tail_text(proc.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "lane": lane,
            "name": name,
            "ok": False,
            "returncode": None,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            "cwd": str(cwd),
            "command": command,
            "error": f"timed out after {timeout}s",
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr),
        }


def add_check(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    lane: str,
    name: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> None:
    rows.append(
        run_command(
            name,
            command,
            cwd=cwd or args.repo_root,
            timeout=timeout or args.timeout,
            lane=lane,
        )
    )


def check_lac_pro_remote(args: argparse.Namespace) -> dict[str, Any]:
    pro_root = Path(args.lac_pro_root)
    if not pro_root.exists():
        return {
            "lane": "guards",
            "name": "lac_pro_remote_guard",
            "ok": bool(args.allow_missing_lac_pro),
            "cwd": str(pro_root),
            "command": ["git", "remote", "-v"],
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "lac-pro repo not found",
        }
    row = run_command(
        "lac_pro_remote_guard",
        ["git", "remote", "-v"],
        cwd=pro_root,
        timeout=args.timeout,
        lane="guards",
    )
    row["ok"] = row["ok"] and not row.get("stdout_tail", "").strip()
    if not row["ok"] and not row.get("stderr_tail"):
        row["stderr_tail"] = "lac-pro must stay local-only; git remote output was not empty"
    return row


def import_preflight_smoke_timeout(args: argparse.Namespace) -> int:
    request_timeout = args.live_timeout
    route_timeout = max(request_timeout, 30)
    return (3 * request_timeout) + (2 * route_timeout) + 60


def live_import_stress_timeout(args: argparse.Namespace) -> int:
    request_timeout = args.live_timeout
    route_timeout = max(request_timeout, 30)
    warm_chat_timeout = max(request_timeout, 700)
    delete_timeout = (4 * request_timeout) + max(request_timeout, 60) + 5
    setup_timeout = (5 * request_timeout) + route_timeout
    import_wait_timeout = args.import_timeout + request_timeout + 5
    return setup_timeout + import_wait_timeout + (2 * warm_chat_timeout) + delete_timeout + 180



def build_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    python = sys.executable
    repo_root = Path(args.repo_root)
    web_root = repo_root / "web"

    if not args.skip_source:
        add_check(
            rows,
            args,
            "source",
            "python_tests_non_live",
            [python, "-m", "pytest", "-q", "-m", "not live"],
            timeout=args.source_timeout,
        )
        add_check(
            rows,
            args,
            "source",
            "web_tests",
            [npm_cmd(), "test"],
            cwd=web_root,
            timeout=args.web_timeout,
        )
        add_check(
            rows,
            args,
            "source",
            "web_typecheck",
            [npm_cmd(), "run", "typecheck"],
            cwd=web_root,
            timeout=args.web_timeout,
        )
        if not args.skip_web_build:
            add_check(
                rows,
                args,
                "source",
                "web_build",
                [npm_cmd(), "run", "build"],
                cwd=web_root,
                timeout=args.web_timeout,
            )
            add_check(
                rows,
                args,
                "source",
                "web_bundle",
                [npm_cmd(), "run", "check:bundle"],
                cwd=web_root,
                timeout=args.web_timeout,
            )
        add_check(
            rows,
            args,
            "source",
            "diff_check",
            ["git", "diff", "--check"],
            timeout=args.timeout,
        )

    if not args.skip_guards:
        add_check(
            rows,
            args,
            "guards",
            "open_core_boundary",
            [python, "-m", "pytest", "-q", "tests/test_boundary_no_lac_pro_import.py"],
            timeout=args.timeout,
        )
        rows.append(check_lac_pro_remote(args))

    if not args.skip_installed:
        release_cmd = [
            python,
            str(repo_root / "scripts" / "release_readiness.py"),
            "--app-url",
            args.app_url,
        ]
        if args.skip_public:
            release_cmd.append("--skip-public")
        if args.strict_public_match:
            release_cmd.append("--strict-public-match")
        add_check(
            rows,
            args,
            "installed",
            "release_readiness",
            release_cmd,
            timeout=args.timeout,
        )
        audit_cmd = [
            python,
            str(repo_root / "scripts" / "installed_app_audit.py"),
            "--app-url",
            args.app_url,
        ]
        if args.edge:
            audit_cmd.extend(["--edge", args.edge])
        add_check(
            rows,
            args,
            "installed",
            "installed_app_audit",
            audit_cmd,
            timeout=args.installed_timeout,
        )
        if args.include_launch_smoke:
            launch_cmd = [
                python,
                str(repo_root / "scripts" / "installed_launch_smoke.py"),
                "--repo-root",
                str(repo_root),
                "--app-url",
                args.app_url,
                "--exe",
                args.installed_exe,
            ]
            if args.edge:
                launch_cmd.extend(["--edge", args.edge])
            if args.allow_existing_launch:
                launch_cmd.append("--allow-existing")
            add_check(
                rows,
                args,
                "installed",
                "installed_launch_smoke",
                launch_cmd,
                timeout=args.installed_timeout + 90,
            )

    if not args.skip_live:
        runtime_cmd = [
            python,
            str(repo_root / "scripts" / "runtime_smoke.py"),
            "--app-url",
            args.app_url,
            "--model",
            args.model,
            "--delete-session",
        ]
        add_check(
            rows,
            args,
            "live",
            "runtime_smoke",
            runtime_cmd,
            timeout=args.live_timeout,
        )
        if not args.skip_import_preflight:
            import_preflight_cmd = [
                python,
                str(repo_root / "scripts" / "live_import_stress.py"),
                "--app-url",
                args.app_url,
                "--repo-id",
                args.import_repo_id,
                "--quant",
                args.import_quant,
                "--filename",
                args.import_filename,
                "--timeout",
                str(args.live_timeout),
                "--preflight-only",
            ]
            add_check(
                rows,
                args,
                "live",
                "import_preflight_smoke",
                import_preflight_cmd,
                timeout=import_preflight_smoke_timeout(args),
            )
        if args.include_live_import:
            import_cmd = [
                python,
                str(repo_root / "scripts" / "live_import_stress.py"),
                "--app-url",
                args.app_url,
                "--repo-id",
                args.import_repo_id,
                "--quant",
                args.import_quant,
                "--filename",
                args.import_filename,
                "--timeout",
                str(args.live_timeout),
                "--import-timeout",
                str(args.import_timeout),
            ]
            add_check(
                rows,
                args,
                "live",
                "live_import_stress",
                import_cmd,
                timeout=live_import_stress_timeout(args),
            )

    return rows


def summarize_lanes(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lanes: dict[str, dict[str, Any]] = {}
    for check in checks:
        lane = check["lane"]
        bucket = lanes.setdefault(lane, {"ok": True, "passed": 0, "failed": 0, "checks": []})
        bucket["checks"].append(check["name"])
        if check["ok"]:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
            bucket["ok"] = False
    return lanes


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    checks = build_checks(args)
    failed = [row for row in checks if not row["ok"]]
    return {
        "ok": not failed,
        "app_url": args.app_url,
        "repo_root": str(args.repo_root),
        "lanes": summarize_lanes(checks),
        "checks": checks,
        "failed": failed,
        "notes": [
            "This gate is local/read-only except for QA session creation and optional live model import/delete checks.",
            "It does not commit, push, tag, upload, publish, or modify lac-pro remotes.",
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the local LAC public-readiness gate by named QA lanes.")
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--lac-pro-root", type=Path, default=ROOT.parent / "lac-pro")
    p.add_argument("--app-url", default=DEFAULT_APP_URL)
    p.add_argument("--edge", default=DEFAULT_EDGE if Path(DEFAULT_EDGE).exists() else "")
    p.add_argument("--installed-exe", default=r"C:\Program Files (x86)\LAC\lac.exe")
    p.add_argument("--model", default="qwen2.5:0.5b")
    p.add_argument("--import-repo-id", default="bartowski/Qwen2.5-0.5B-Instruct-GGUF")
    p.add_argument("--import-quant", default="Q4_K_M")
    p.add_argument("--import-filename", default="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf")
    p.add_argument("--include-live-import", action="store_true", help="Run the slow HF import + disposable delete stress test.")
    p.add_argument("--include-launch-smoke", action="store_true", help="Start the installed lac.exe and audit it.")
    p.add_argument("--allow-existing-launch", action="store_true", help="Let launch smoke audit an already responding app.")
    p.add_argument("--skip-source", action="store_true")
    p.add_argument("--skip-web-build", action="store_true")
    p.add_argument("--skip-guards", action="store_true")
    p.add_argument("--skip-installed", action="store_true")
    p.add_argument("--skip-live", action="store_true")
    p.add_argument("--skip-import-preflight", action="store_true", help="Skip the cheap HF/Pro preflight smoke in the live lane.")
    p.add_argument("--skip-public", action="store_true", help="Do not query GitHub's latest public release.")
    p.add_argument("--strict-public-match", action="store_true", help="Fail if the local installer does not match the latest published release asset.")
    p.add_argument("--allow-missing-lac-pro", action="store_true")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--source-timeout", type=int, default=300)
    p.add_argument("--web-timeout", type=int, default=240)
    p.add_argument("--installed-timeout", type=int, default=180)
    p.add_argument("--live-timeout", type=int, default=180)
    p.add_argument("--import-timeout", type=int, default=900)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
