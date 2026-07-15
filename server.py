#!/usr/bin/env python3
import sys
import os
import webbrowser
import threading
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.cookbook import proc

HOST = "127.0.0.1"
PORT = 5050

_here = Path(__file__).parent


def get_version():
    try:
        from backend.version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def check_for_update(current_version: str) -> dict | None:
    try:
        url = "https://api.github.com/repos/Dkrynen/lac/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current_version:
            return {
                "latest_version": latest,
                "download_url": data.get("html_url", ""),
                "release_notes": data.get("body", "")[:500],
            }
    except Exception:
        pass
    return None


def ollama_is_installed() -> bool:
    import shutil
    return shutil.which("ollama") is not None


def find_port_pids(port: int) -> list[str]:
    pids = set()
    try:
        out = proc.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    for line in out.splitlines():
        s = line.strip()
        if "LISTENING" not in s.upper():
            continue
        parts = s.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        pid = parts[-1]
        if pid and pid != "0":
            pids.add(pid)
    return sorted(pids)


def _process_is_ours(pid: str) -> bool:
    """True only if we can prove this PID belongs to LAC.

    Either we spawned it (in-memory registry) or its image name is our shipped
    exe (a stale LAC from a previous launch). Anything else is treated as a
    foreign process we must never kill.
    """
    try:
        if proc.is_ours(pid):
            return True
    except (ValueError, TypeError):
        pass
    if os.name != "nt":
        return False
    try:
        out = proc.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return False
    import csv as _csv
    for row in _csv.reader(out.splitlines()):
        if row and row[0].strip().lower() == "lac.exe":
            return True
    return False


def kill_pids(pids: list[str]) -> list[str]:
    killed = []
    for pid in pids:
        if not _process_is_ours(pid):
            print(f"  ! Refusing to kill PID {pid}: not a LAC process.")
            continue
        try:
            if os.name == "nt":
                proc.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=10)
            else:
                os.kill(int(pid), 9)
            killed.append(pid)
        except Exception:
            pass
    return killed


def clear_port(port: int, force: bool) -> bool:
    pids = find_port_pids(port)
    if not pids:
        return True
    ours = [p for p in pids if _process_is_ours(p)]
    foreign = [p for p in pids if p not in ours]
    if foreign:
        print(f"  ! Port {port} is held by another application (PID {', '.join(foreign)}).")
        print(f"  ! LAC will not terminate a process it does not own. Free the port and retry.")
        return False
    print(f"  ! Port {port} is held by a stale LAC process (PID {', '.join(ours)}).")
    if not force:
        print(f"  ! Re-run with --force to reclaim it.")
        return False
    kill_pids(ours)
    time.sleep(0.5)
    return not find_port_pids(port)


def _should_use_window(args) -> bool:
    if getattr(args, "no_window", False):
        return False
    if getattr(args, "window", False):
        return True
    return getattr(sys, "frozen", False)


def _is_cli_invocation(argv: list[str]) -> bool:
    """The exe is being used as a CLI when the first token is a subcommand
    (a bare word), not a server flag (--host/--window/...) and not empty."""
    return bool(argv) and not argv[0].startswith("-") and not argv[0].lower().startswith("lac://")


def main():
    import argparse

    raw_args = sys.argv[1:]
    if len(raw_args) == 1 and raw_args[0].lower().startswith("lac://"):
        from backend.cloud_session import is_oauth_callback_uri
        from backend import desktop

        accepted = is_oauth_callback_uri(raw_args[0]) and desktop.forward_oauth_callback(raw_args[0])
        sys.exit(0 if accepted else 1)

    if _is_cli_invocation(raw_args):
        import cli  # bundled into the exe so `lac.exe pro activate` / `lac.exe scan` work
        sys.exit(cli.main())

    parser = argparse.ArgumentParser(description="LAC web UI server")
    parser.add_argument(
        "--host",
        default=HOST,
        help="Bind host; non-loopback addresses expose the unauthenticated API",
    )
    parser.add_argument("--port", type=int, default=PORT, help="Bind port")
    parser.add_argument("--force", action="store_true", help="Kill any process already using the port, then start")
    parser.add_argument("--kill-port", action="store_true", help="Kill whatever holds the port and exit")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--window", action="store_true", help="Open the native desktop window")
    parser.add_argument("--no-window", action="store_true", help="Force headless server (no window)")
    args = parser.parse_args()

    host = args.host
    port = args.port

    if args.kill_port:
        pids = find_port_pids(port)
        if not pids:
            print(f"  Port {port} is free.")
        else:
            killed = kill_pids(pids)
            print(f"  Killed: {', '.join(killed)}")
        return

    if _should_use_window(args):
        from backend import desktop
        sys.exit(desktop.launch_desktop(host=host, port=port))

    version = get_version()
    from backend.api import run_server

    print()
    print("  +------------------------------------------+")
    print(f"  |              LAC v{version:<22} |")
    print("  |  Find your perfect local LLM              |")
    print("  +------------------------------------------+")
    print()

    if not ollama_is_installed():
        print("  ! Ollama is not installed.")
        print("  ! Download it from: https://ollama.com/download")
        print("  ! The app will still run but cannot install models.")
        print()

    update = check_for_update(version)
    if update:
        print(f"  ! Update available: v{update['latest_version']}")
        print(f"  ! Download: {update['download_url']}")
        print()

    if not clear_port(port, args.force):
        sys.exit(1)

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=open_browser, daemon=True).start()
    run_server(host=host, port=port)


if __name__ == "__main__":
    main()
