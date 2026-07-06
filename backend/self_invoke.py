"""How LAC re-invokes itself in a fresh process — as the CLI (for a throwaway
`lac pro activate`) or as the desktop window (for a self-relaunch). Frozen exe
dispatches CLI subcommands (see server._is_cli_invocation); a dev checkout runs
cli.py / server.py under the interpreter."""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))  # backend/
_REPO = os.path.dirname(_ROOT)


def cli_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(_REPO, "cli.py")]


def window_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--window"]
    return [sys.executable, os.path.join(_REPO, "server.py"), "--window"]
