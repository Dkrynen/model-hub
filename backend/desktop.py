"""Native desktop window for LAC.

Boots the existing Flask app on a fixed loopback port in a daemon thread,
waits until it is serving, then opens a pywebview (WebView2) window over it.
Closing the window exits the process and the daemon server dies with it, so
orphan servers cannot accumulate. Windows-only window; no-ops elsewhere.

Non-goals (locked): no tray, no autostart, no in-window auto-update, no
multi-window.
"""
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from backend import self_invoke
from backend.cookbook import proc
from backend.cookbook.config import resolve_under_data_root

HOST = "127.0.0.1"
PORT = 5050
WINDOW_TITLE = "LAC"
APP_USER_MODEL_ID = "Acend.LAC"
WEBVIEW2_RUNTIME_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _serving(host: str, port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
        return True
    except Exception:
        return False


def _wait_until_serving(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serving(host, port):
            return True
        time.sleep(0.15)
    return False


def _start_server_thread(host: str, port: int) -> None:
    from backend.api import run_server
    t = threading.Thread(target=lambda: run_server(host=host, port=port), daemon=True)
    t.start()


def _set_taskbar_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _show_startup_error(host: str, port: int) -> None:
    msg = f"LAC could not start its local server on {host}:{port}."
    print(f"  ! {msg}")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "LAC", 0x10)
        except Exception:
            pass


def _show_dialog(text: str) -> None:
    """Best-effort informational dialog on Windows. No-op elsewhere; never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "LAC", 0x40)
    except Exception:
        pass


_MUTEX_HANDLE = None  # kept alive for the process lifetime


def acquire_single_instance(name: str = "LAC_SINGLE_INSTANCE") -> bool:
    """True if we are the first instance; False if another already holds the mutex."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), name)
        if not handle:
            return True  # fail-open: a mutex error must not block launch
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True  # fail-open


def focus_existing_window(title: str = WINDOW_TITLE) -> None:
    """Best-effort raise of the already-running LAC window. Never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def forward_oauth_callback(callback_uri: str, host: str = HOST, port: int = PORT) -> bool:
    """Forward a protocol callback to the running desktop process."""
    from backend.cloud_session import CloudSessionError, parse_oauth_callback_uri

    try:
        parse_oauth_callback_uri(callback_uri)
    except CloudSessionError:
        return False
    body = json.dumps({"callback_uri": callback_uri}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"http://{host}:{port}/api/cloud/auth/callback",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            accepted = response.status == 200
    except Exception:
        return False
    if accepted:
        focus_existing_window()
    return accepted


def launch_desktop(host: str = HOST, port: int = PORT) -> int:
    # Single-instance FIRST: never even start a server if one is running.
    if not acquire_single_instance():
        focus_existing_window()
        return 0

    _set_taskbar_identity()
    _start_server_thread(host, port)

    if not _wait_until_serving(host, port):
        _show_startup_error(host, port)
        return 1

    return _open_window(host, port)


def load_window_state() -> dict:
    try:
        data = json.loads(WINDOW_STATE_PATH.read_text())
        return {"bounds": dict(data.get("bounds") or {}), "view": str(data.get("view") or "")}
    except Exception:
        return {"bounds": {}, "view": ""}


def _open_window(host: str, port: int) -> int:
    state = load_window_state()
    url = f"http://{host}:{port}"
    view = state.get("view")
    if view:
        url += f"/?view={view}"
    geom = {}
    b = state.get("bounds") or {}
    for k in ("x", "y", "width", "height"):
        if isinstance(b.get(k), int):
            geom[k] = b[k]
    try:
        import webview
        if webview is None:
            raise ImportError("webview unavailable")
        webview.create_window(WINDOW_TITLE, url, min_size=(1024, 700), **geom)
        webview.start()
        return 0
    except Exception as e:
        return _fallback_to_browser(host, port, str(e))


def _fallback_to_browser(host: str, port: int, reason: str) -> int:
    print(f"  ! Native window unavailable ({reason}).")
    print(f"  ! Opening LAC in your browser instead.")
    print(f"  ! For the desktop app, install the WebView2 runtime: {WEBVIEW2_RUNTIME_URL}")
    _show_dialog(
        "The desktop window needs the Microsoft WebView2 runtime.\n\n"
        "LAC will open in your browser now. Install WebView2 for the app window:\n"
        + WEBVIEW2_RUNTIME_URL
    )
    try:
        webbrowser.open(f"http://{host}:{port}")
    except Exception:
        pass
    return 0


WINDOW_STATE_PATH = resolve_under_data_root("window_state.json")


def save_window_state(bounds: dict | None, view: str | None) -> None:
    """Best-effort persist of window geometry + current view. Never raises."""
    try:
        WINDOW_STATE_PATH.write_text(json.dumps({"bounds": bounds or {}, "view": view or ""}))
    except Exception:
        pass


def relaunch(view: str | None = None, bounds: dict | None = None) -> bool:
    """Persist state, spawn a fresh window process, and exit this one so the
    new process cold-boots and mounts Pro via the normal startup seam. Returns
    False WITHOUT exiting if the spawn fails (grant is already on disk, so Pro
    comes up on the next manual launch)."""
    save_window_state(bounds, view)
    try:
        proc.popen(self_invoke.window_prefix())
    except Exception:
        return False
    os._exit(0)
    return True  # unreachable; keeps the declared return type honest
