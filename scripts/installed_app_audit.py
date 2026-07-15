from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EXPECTED_APP_DIR = r"C:\Program Files (x86)\LAC"

PAGE_ROUTES = [
    ("/", "Dashboard"),
    ("/browse", "Browse models"),
    ("/scan", "Scan & recommend"),
    ("/installed", "Installed"),
    ("/studio", "Studio"),
    ("/chat", "Studio"),
    ("/lab", "Lab"),
    ("/performance", "Lab"),
    ("/downloads", "Downloads"),
    ("/pro", "LAC Pro"),
    ("/account", "Account"),
    ("/cloud", "Cloud Activity"),
    ("/docs", "Docs"),
    ("/settings", "Settings"),
]


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method or ("POST" if payload is not None else "GET"),
        headers={"Accept": "application/json", "User-Agent": "LAC-installed-app-audit/1"},
    )
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def request_head(base_url: str, path: str, timeout: int = 15) -> dict[str, Any]:
    req = urllib.request.Request(base_url.rstrip("/") + path, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {
            "status": resp.status,
            "content_type": resp.headers.get("content-type", ""),
            "content_length": resp.headers.get("content-length"),
        }


def render_dom(edge: str, base_url: str, path: str, timeout: int) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    proc = subprocess.run(
        [
            edge,
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--virtual-time-budget=5000",
            "--dump-dom",
            url,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
    stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr_tail": "\n".join((stderr or "").splitlines()[-6:]),
    }


def text_content(dom: str) -> str:
    without_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", dom, flags=re.I | re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def check_pages(args: argparse.Namespace) -> list[dict[str, Any]]:
    edge = args.edge or shutil.which("msedge") or (DEFAULT_EDGE if Path(DEFAULT_EDGE).exists() else "")
    out = []
    for path, expected in PAGE_ROUTES:
        head: dict[str, Any] = {}
        render: dict[str, Any] = {}
        ok = False
        error = None
        try:
            head = request_head(args.app_url, path, timeout=args.timeout)
            if edge:
                rendered = render_dom(edge, args.app_url, path, max(args.timeout, 30))
                body_text = text_content(rendered["stdout"])
                render = {
                    "returncode": rendered["returncode"],
                    "contains_expected": expected in body_text,
                    "has_root": 'id="root"' in rendered["stdout"],
                    "body_chars": len(body_text),
                    "stderr_tail": rendered["stderr_tail"],
                }
                ok = head["status"] == 200 and rendered["returncode"] == 0 and expected in body_text
            else:
                render = {"skipped": True, "reason": "Edge not found"}
                ok = head["status"] == 200
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        out.append({
            "path": path,
            "expected": expected,
            "ok": ok,
            "head": head,
            "render": render,
            "error": error,
        })
    return out


def q(path: str, **params: Any) -> str:
    return path + ("?" + urllib.parse.urlencode(params) if params else "")


def check_api(args: argparse.Namespace) -> list[dict[str, Any]]:
    checks: list[tuple[str, str, str, Any | None, int | None, str | None]] = [
        ("version", "GET", "/api/system/version", None, 200, "version"),
        ("plugins", "GET", "/api/plugins", None, 200, None),
        ("product_state", "GET", "/api/product/state", None, 200, "schema_version"),
        ("ollama_status", "GET", "/api/ollama/status", None, 200, "running"),
        ("installed_models", "GET", "/api/ollama/models", None, 200, None),
        ("running_models", "GET", "/api/ollama/ps", None, 200, "running"),
        ("scan", "GET", "/api/scan", None, 200, "ram_gb"),
        ("recommend", "GET", q("/api/recommend", use_case="coding", top_k=3), None, 200, "recommendations"),
        ("library_browse", "GET", q("/api/library/browse", q="qwen", sort="pulls"), None, 200, "models"),
        ("library_tags", "GET", q("/api/library/tags", name="qwen2.5"), None, 200, "tags"),
        ("hf_search", "GET", q("/api/hf/gguf-search", q="qwen2.5 0.5b", limit=3), None, 200, "models"),
        ("install_preflight_hf", "GET", q("/api/model/install-preflight", target="hf.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M"), None, 200, "preflight"),
        ("install_preflight_ollama", "GET", q("/api/model/install-preflight", target="llama3.2:3b"), None, 200, "model_store"),
        ("downloads", "GET", "/api/config/downloads", None, 200, None),
        ("pull_status", "GET", "/api/ollama/pull-status", None, 200, "pulls"),
        ("config", "GET", "/api/config", None, 200, "ollama_host"),
        ("storage", "GET", "/api/system/storage", None, 200, "app_dir"),
        ("model_location", "GET", "/api/system/model-location", None, 200, "effective_after_restart"),
        ("model_store_doctor", "GET", "/api/system/model-store-doctor", None, 200, "model_store"),
        ("debug_bundle", "GET", "/api/system/debug-bundle", None, 200, "app"),
        ("update_check", "GET", q("/api/system/check-update", current="2.7.0"), None, 200, "update_available"),
        ("performance", "GET", q("/api/diagnostics/performance", model="qwen2.5:0.5b"), None, 200, "diagnosis"),
        ("pro_status", "GET", "/api/pro/status", None, 200, "licensed"),
        ("pro_hf_token", "GET", "/api/pro/hf-token", None, 200, "configured"),
        ("pro_insights", "GET", "/api/pro/insights", None, 200, "rows"),
        ("pro_autopilot_log", "GET", "/api/pro/autopilot-log", None, 200, "entries"),
        ("pro_import_history", "GET", "/api/pro/import-history", None, 200, "entries"),
        ("pro_agent_cockpit", "GET", "/api/pro/agent-cockpit", None, 200, "state"),
        ("pro_benchmark_history", "GET", q("/api/pro/benchmark-history", model="qwen2.5:0.5b"), None, 200, "runs"),
        ("pro_optimize_status", "GET", q("/api/pro/optimize-status", model="qwen2.5:0.5b"), None, None, "state"),
        ("pro_tune_status", "GET", q("/api/pro/tune-status", model="qwen2.5:0.5b"), None, 200, "state"),
        ("pro_import_resolve", "GET", q("/api/pro/import-resolve", repo_id="bartowski/Qwen2.5-0.5B-Instruct-GGUF", quant="Q4_K_M", filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"), None, 200, "state"),
    ]
    out = []
    for name, method, path, payload, expected_status, key in checks:
        ok = False
        status = None
        body: Any = None
        error = None
        try:
            status, body = request_json(
                args.app_url,
                path,
                payload,
                method=method if method != "GET" else None,
                timeout=args.timeout,
            )
            status_ok = expected_status is None or status == expected_status
            key_ok = key is None or (isinstance(body, dict) and key in body)
            ok = bool(status_ok and key_ok)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        out.append({
            "name": name,
            "method": method,
            "path": path,
            "ok": ok,
            "status": status,
            "key": key,
            "body_summary": summarize_body(body),
            "error": error,
        })
    return out


def summarize_body(body: Any) -> Any:
    if isinstance(body, dict):
        out = {}
        for key, value in body.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, list):
                out[key] = f"list[{len(value)}]"
            elif isinstance(value, dict):
                out[key] = f"dict[{len(value)}]"
            if len(out) >= 8:
                break
        return out
    if isinstance(body, list):
        return f"list[{len(body)}]"
    return body


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    pages = check_pages(args)
    api = check_api(args)
    _, storage = request_json(args.app_url, "/api/system/storage", timeout=args.timeout)
    installed_app_ok = str(storage.get("app_dir", "")).lower() == EXPECTED_APP_DIR.lower()
    no_bundled_weights = not storage.get("models_are_bundled") and not storage.get("model_weight_files_in_app")
    ok = (
        all(row["ok"] for row in pages)
        and all(row["ok"] for row in api)
        and installed_app_ok
        and no_bundled_weights
    )
    return {
        "ok": ok,
        "installed_app_ok": installed_app_ok,
        "no_bundled_weights": bool(no_bundled_weights),
        "storage": summarize_body(storage),
        "pages": pages,
        "api": api,
        "failed_pages": [row for row in pages if not row["ok"]],
        "failed_api": [row for row in api if not row["ok"]],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit a real installed LAC app across pages and APIs.")
    p.add_argument("--app-url", default="http://127.0.0.1:5050")
    p.add_argument("--edge", default="", help="Path to Edge/Chromium for rendered route checks.")
    p.add_argument("--timeout", type=int, default=30)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
