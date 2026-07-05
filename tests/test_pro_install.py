"""`lac unlock <key>` — generic licensed-plugin bootstrap (backend/pro_install.py).

Covers the four honest failure states (invalid_key / network / download /
install), the success path, no-partial-install, zip-slip containment,
re-run overwrite, gate-URL resolution, the CLI command, and that a
bootstrap-installed dist-info becomes visible to plugin discovery.

The real ``~/.model-hub/plugins`` is NEVER touched: every test patches the
module constant ``pro_install.PLUGIN_DIR`` to a tmp_path (repo convention —
patch module attributes, not env vars).
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import urllib.error

import backend.pro_install as pro_install
import backend.plugins as plugins_mod


# ---------------------------------------------------------------- helpers

def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """Build an in-memory zip with the given name -> content entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _artifact(pyd_content: bytes = b"MZ\x90\x00fake-native-module") -> bytes:
    """A generic licensed-plugin artifact: compiled module + dist-info at the
    zip ROOT (the layout the lac-pro build produces). Deliberately NOT named
    lac_pro — core-side install is plugin-agnostic."""
    return _zip_bytes({
        "dummy_plugin.cp311-win_amd64.pyd": pyd_content,
        "dummy_plugin-0.1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: dummy-plugin\nVersion: 0.1.0\n"
        ),
        "dummy_plugin-0.1.0.dist-info/entry_points.txt": (
            b"[lac.plugins]\ndummy = types:SimpleNamespace\n"
        ),
    })


def _fake_post(status: int, body: bytes, seen: list | None = None):
    """A fake gate: records the URL it was called with, returns (status, body)."""
    def post(url, payload):
        if seen is not None:
            seen.append((url, payload))
        return status, body
    return post


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch) -> Path:
    """Isolate the plugin dir: patch the module constant to a tmp path that
    does NOT yet exist (install must create it)."""
    pdir = tmp_path / "plugins"
    monkeypatch.setattr(pro_install, "PLUGIN_DIR", pdir)
    return pdir


@pytest.fixture
def guarded_sys_path(monkeypatch):
    """Replace sys.path with a copy so path mutations by the code under test
    are rolled back after the test."""
    monkeypatch.setattr(sys, "path", list(sys.path))


# ------------------------------------------------- install_pro_plugin: success

def test_success_installs_artifact_and_reports_installed(plugin_dir):
    result = pro_install.install_pro_plugin(
        "LAC-GOOD-KEY", gate_url="https://gate.test/pro/download",
        http_post=_fake_post(200, _artifact()),
    )
    assert result["state"] == "installed"
    assert result["path"] == str(plugin_dir)
    # Contents extracted at the plugin-dir ROOT (dir itself goes on sys.path).
    assert (plugin_dir / "dummy_plugin.cp311-win_amd64.pyd").read_bytes().startswith(b"MZ")
    ep_txt = (plugin_dir / "dummy_plugin-0.1.0.dist-info" / "entry_points.txt").read_text()
    assert "[lac.plugins]" in ep_txt


def test_success_sends_license_key_as_json_payload(plugin_dir):
    seen: list = []
    pro_install.install_pro_plugin(
        "LAC-GOOD-KEY", gate_url="https://gate.test/pro/download",
        http_post=_fake_post(200, _artifact(), seen),
    )
    assert seen == [("https://gate.test/pro/download", {"license_key": "LAC-GOOD-KEY"})]


def test_rerun_overwrites_installed_files(plugin_dir):
    r1 = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, _artifact(b"v1-bytes")))
    r2 = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, _artifact(b"v2-bytes")))
    assert r1["state"] == r2["state"] == "installed"
    assert (plugin_dir / "dummy_plugin.cp311-win_amd64.pyd").read_bytes() == b"v2-bytes"


# ------------------------------------------- install_pro_plugin: honest failures

def test_gate_403_maps_to_invalid_key(plugin_dir):
    result = pro_install.install_pro_plugin(
        "LAC-BAD-KEY", gate_url="https://g.test/d",
        http_post=_fake_post(403, b'{"error":"invalid_or_expired"}'),
    )
    assert result == {
        "state": "failed",
        "error_type": "invalid_key",
        "message": result["message"],
    }
    assert "license key" in result["message"].lower()
    assert not plugin_dir.exists()  # nothing written


def test_network_failure_maps_to_network(plugin_dir):
    def post(url, payload):
        raise urllib.error.URLError("getaddrinfo failed")
    result = pro_install.install_pro_plugin("K", gate_url="https://g.test/d", http_post=post)
    assert result["state"] == "failed"
    assert result["error_type"] == "network"
    assert "https://g.test/d" in result["message"]
    assert not plugin_dir.exists()


def test_unexpected_transport_error_maps_to_network_never_raises(plugin_dir):
    def post(url, payload):
        raise RuntimeError("totally unexpected")
    result = pro_install.install_pro_plugin("K", gate_url="https://g.test/d", http_post=post)
    assert result["state"] == "failed"
    assert result["error_type"] == "network"


def test_non_200_status_maps_to_download(plugin_dir):
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d",
        http_post=_fake_post(503, b'{"error":"artifact_unavailable"}'),
    )
    assert result["state"] == "failed"
    assert result["error_type"] == "download"
    assert "503" in result["message"]
    assert not plugin_dir.exists()


def test_body_not_a_zip_maps_to_download_installs_nothing(plugin_dir):
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d",
        http_post=_fake_post(200, b"<html>this is not a zip</html>"),
    )
    assert result["state"] == "failed"
    assert result["error_type"] == "download"
    assert not plugin_dir.exists()  # no partial install, not even an empty dir


def test_truncated_body_read_maps_to_download(plugin_dir):
    def post(url, payload):
        raise pro_install._GateReadError("connection dropped after 1024 bytes")
    result = pro_install.install_pro_plugin("K", gate_url="https://g.test/d", http_post=post)
    assert result["state"] == "failed"
    assert result["error_type"] == "download"
    assert not plugin_dir.exists()


def test_empty_archive_maps_to_download(plugin_dir):
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, _zip_bytes({})))
    assert result["state"] == "failed"
    assert result["error_type"] == "download"
    assert not plugin_dir.exists()


def test_zip_slip_entry_rejected_installs_nothing(plugin_dir, tmp_path):
    evil = _zip_bytes({
        "good.txt": b"ok",
        "../evil.txt": b"escaped!",
    })
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, evil))
    assert result["state"] == "failed"
    assert result["error_type"] == "download"
    assert not plugin_dir.exists()
    assert not (tmp_path / "evil.txt").exists()          # nothing escaped
    assert not list(tmp_path.rglob("evil.txt"))          # ... anywhere under tmp


def test_filesystem_failure_maps_to_install_and_cleans_staging(plugin_dir, monkeypatch):
    def locked(staging, dest):
        raise PermissionError("file is locked by a running LAC instance")
    monkeypatch.setattr(pro_install, "_move_contents", locked)
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, _artifact()))
    assert result["state"] == "failed"
    assert result["error_type"] == "install"
    assert str(plugin_dir) in result["message"]
    # staging dirs are cleaned up, no droppings next to the plugin dir
    leftovers = [p for p in plugin_dir.parent.iterdir() if p.name.startswith(".lac-unlock")]
    assert leftovers == []


# ------------------------------------------------------- gate URL resolution

def test_gate_url_env_override_read_at_call_time(plugin_dir, monkeypatch):
    seen: list = []
    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://env-gate.test/pro/download")
    pro_install.install_pro_plugin("K", http_post=_fake_post(200, _artifact(), seen))
    assert seen[0][0] == "https://env-gate.test/pro/download"


def test_explicit_gate_url_beats_env(plugin_dir, monkeypatch):
    seen: list = []
    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://env-gate.test/pro/download")
    pro_install.install_pro_plugin(
        "K", gate_url="https://param.test/d", http_post=_fake_post(200, _artifact(), seen))
    assert seen[0][0] == "https://param.test/d"


def test_default_gate_url_is_the_module_constant(plugin_dir, monkeypatch):
    monkeypatch.delenv("LAC_PRO_GATE_URL", raising=False)
    seen: list = []
    pro_install.install_pro_plugin("K", http_post=_fake_post(200, _artifact(), seen))
    assert seen[0][0] == pro_install.PRO_GATE_URL
    assert seen[0][0].startswith("https://")


# ------------------------------------------------------------- core seam purity

def test_pro_install_never_mentions_lac_pro():
    """Core stays Pro-logic-unaware: generic licensed-plugin delivery only."""
    source = Path(pro_install.__file__).read_text(encoding="utf-8")
    assert "lac_pro" not in source


# ------------------------------------------------------------------ cmd_unlock

def test_cmd_unlock_success_exit0_prints_path_and_restart(plugin_dir, monkeypatch, capsys):
    import cli
    monkeypatch.setattr(pro_install, "_http_post", _fake_post(200, _artifact()))
    cli.cmd_unlock(SimpleNamespace(key="LAC-GOOD-KEY"))  # returns (exit 0), no SystemExit
    out = capsys.readouterr().out
    assert str(plugin_dir) in out
    assert "restart lac" in out.lower()


def test_cmd_unlock_invalid_key_exit1_message_on_stderr(plugin_dir, monkeypatch, capsys):
    import cli
    monkeypatch.setattr(pro_install, "_http_post", _fake_post(403, b"{}"))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_unlock(SimpleNamespace(key="LAC-BAD-KEY"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "license key" in err.lower()


def test_unlock_subparser_registered(plugin_dir, guarded_sys_path):
    import cli
    parser = cli.build_parser()
    args = parser.parse_args(["unlock", "LAC-SOME-KEY"])
    assert args.key == "LAC-SOME-KEY"
    assert args.func is cli.cmd_unlock
    assert "Activate LAC Pro with your license key" in parser.format_help()


# ------------------------------------- discovery sees a bootstrap-installed dist

def test_discover_finds_plugin_installed_by_unlock(plugin_dir, guarded_sys_path):
    """The crux: unlock-install a dist-info, then plugins.discover() must see it
    (plugin dir prepended to sys.path BEFORE the entry-point read)."""
    result = pro_install.install_pro_plugin(
        "K", gate_url="https://g.test/d", http_post=_fake_post(200, _artifact()))
    assert result["state"] == "installed"

    found = plugins_mod.discover()
    names = [p.name for p in found]
    assert "dummy" in names                      # dist-info discovered via entry_points
    dummy = next(p for p in found if p.name == "dummy")
    assert dummy.ok                              # types:SimpleNamespace loads fine
    assert sys.path[0] == str(plugin_dir)        # prepended, not appended


def test_discover_skips_cleanly_when_plugin_dir_absent(plugin_dir, guarded_sys_path, monkeypatch):
    """Guard: dir doesn't exist -> zero behavior change, sys.path untouched."""
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [])
    assert not plugin_dir.exists()
    assert plugins_mod.discover() == []
    assert str(plugin_dir) not in sys.path
