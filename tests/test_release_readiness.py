from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_readiness.py"


def _load_release_readiness():
    spec = importlib.util.spec_from_file_location("release_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_workflow_verifies_source_version_and_uploads_checksum():
    text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "-replace '#define MyAppVersion" not in text
    assert "scripts/verify_release_version.py --expected $tag" in text
    assert "SHA256SUMS.txt" in text
    assert "dist/SHA256SUMS.txt" in text


def test_sha256_file_reports_uppercase_digest(tmp_path):
    rr = _load_release_readiness()
    artifact = tmp_path / "LAC-Setup-test.exe"
    artifact.write_bytes(b"lac")
    assert rr.sha256_file(artifact) == "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A"


def test_parse_sha256sums_accepts_common_formats():
    rr = _load_release_readiness()

    parsed = rr.parse_sha256sums(
        "# comment\n"
        "a40d4e73cd6a4ddc99f4c6a425196629c82ce3eac00e3740ee94811eb629a93a  LAC-Setup.exe\n"
        "DEE590AE62F700D9B84AD212DAD58E66BC078957EA03E73FDFF9B6A178C99F42 *LAC-Setup-2.6.4.exe\n"
    )

    assert parsed["LAC-Setup.exe"] == "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A"
    assert parsed["LAC-Setup-2.6.4.exe"] == "DEE590AE62F700D9B84AD212DAD58E66BC078957EA03E73FDFF9B6A178C99F42"


def test_check_running_app_reports_version_debug_and_pro_plugin(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=15):
        if url.endswith("/api/system/version"):
            return {"version": rr.APP_VERSION, "app_name": "LAC"}
        if url.endswith("/api/plugins"):
            return [{"name": "pro", "version": "0.1.0", "ok": True, "error": None}]
        raise AssertionError(url)

    def fake_read_bytes(url, timeout=15):
        assert url.endswith("/api/system/debug-bundle")
        return 200, {"content-disposition": 'attachment; filename="lac-debug.json"'}, b'{"app":{"version":"x"}}'

    monkeypatch.setattr(rr, "read_json", fake_read_json)
    monkeypatch.setattr(rr, "read_bytes", fake_read_bytes)

    result = rr.check_running_app("http://lac.local")
    assert result["ok"] is True
    assert result["debug_bundle"]["attachment"] is True
    assert result["pro_plugin"]["name"] == "pro"


def test_public_release_reports_local_size_mismatch(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=20):
        return {
            "tag_name": "v2.6.2",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "LAC-Setup-2.6.2-windows-x64.exe",
                    "size": 10,
                    "browser_download_url": "https://example.test/LAC-Setup.exe",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://example.test/SHA256SUMS.txt",
                },
            ],
        }

    def fake_read_bytes(url, timeout=20):
        assert url == "https://example.test/SHA256SUMS.txt"
        return 200, {}, (
            "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A  "
            "LAC-Setup-2.6.2-windows-x64.exe\n"
        ).encode()

    monkeypatch.setattr(rr, "read_json", fake_read_json)
    monkeypatch.setattr(rr, "read_bytes", fake_read_bytes)

    result = rr.check_public_release({"size_bytes": 11, "sha256": "NOT_THE_PUBLIC_HASH"})
    assert result["ok"] is True
    assert result["asset_name"] == "LAC-Setup-2.6.2-windows-x64.exe"
    assert result["local_matches_published_size"] is False
    assert result["published_sha256"] == "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A"
    assert result["local_matches_published_sha256"] is False


def test_public_release_reports_local_version_mismatch_when_size_matches(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=20):
        return {
            "tag_name": "v0.0.1",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "LAC-Setup-0.0.1.exe",
                    "size": 11,
                    "browser_download_url": "https://example.test/LAC-Setup.exe",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://example.test/SHA256SUMS.txt",
                },
            ],
        }

    def fake_read_bytes(url, timeout=20):
        assert url == "https://example.test/SHA256SUMS.txt"
        return 200, {}, (
            "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A  "
            "LAC-Setup-0.0.1.exe\n"
        ).encode()

    monkeypatch.setattr(rr, "read_json", fake_read_json)
    monkeypatch.setattr(rr, "read_bytes", fake_read_bytes)

    result = rr.check_public_release({
        "size_bytes": 11,
        "sha256": "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A",
    })

    assert result["local_matches_published_size"] is True
    assert result["local_matches_published_sha256"] is True
    assert result["expected_tag"] == f"v{rr.APP_VERSION}"
    assert result["published_matches_local_version"] is False
    assert rr.strict_public_match_ok(result) is False


def test_public_release_requires_checksum_asset_for_strict_match(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=20):
        return {
            "tag_name": f"v{rr.APP_VERSION}",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "LAC-Setup.exe",
                    "size": 11,
                    "browser_download_url": "https://example.test/LAC-Setup.exe",
                },
            ],
        }

    monkeypatch.setattr(rr, "read_json", fake_read_json)

    result = rr.check_public_release({
        "size_bytes": 11,
        "sha256": "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A",
    })

    assert result["published_matches_local_version"] is True
    assert result["local_matches_published_size"] is True
    assert result["sha256_asset_name"] is None
    assert result["local_matches_published_sha256"] is False
    assert rr.strict_public_match_ok(result) is False


def test_public_release_reports_checksum_fetch_failure(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=20):
        return {
            "tag_name": f"v{rr.APP_VERSION}",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "LAC-Setup.exe",
                    "size": 11,
                    "browser_download_url": "https://example.test/LAC-Setup.exe",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://example.test/SHA256SUMS.txt",
                },
            ],
        }

    def fake_read_bytes(url, timeout=20):
        raise OSError("checksum offline")

    monkeypatch.setattr(rr, "read_json", fake_read_json)
    monkeypatch.setattr(rr, "read_bytes", fake_read_bytes)

    result = rr.check_public_release({
        "size_bytes": 11,
        "sha256": "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A",
    })

    assert result["checksum_error"] == "checksum offline"
    assert result["local_matches_published_size"] is True
    assert result["local_matches_published_sha256"] is False
    assert rr.strict_public_match_ok(result) is False


def test_main_strict_public_match_exits_nonzero_on_checksum_mismatch(monkeypatch, tmp_path, capsys):
    rr = _load_release_readiness()
    installer = tmp_path / "LAC-Setup.exe"
    installer.write_bytes(b"local")

    monkeypatch.setattr(rr, "check_running_app", lambda *a, **kw: {"skipped": True})
    monkeypatch.setattr(rr, "check_public_release", lambda *a, **kw: {
        "ok": True,
        "published_matches_local_version": True,
        "local_matches_published_size": True,
        "local_matches_published_sha256": False,
    })

    rc = rr.main([
        "--installer",
        str(installer),
        "--skip-app",
        "--strict-public-match",
    ])

    assert rc == 1
    assert "local_matches_published_sha256" in capsys.readouterr().out


def test_strict_public_match_requires_size_and_version():
    rr = _load_release_readiness()

    assert rr.strict_public_match_ok({
        "local_matches_published_size": True,
        "published_matches_local_version": True,
        "local_matches_published_sha256": True,
    }) is True
    assert rr.strict_public_match_ok({
        "local_matches_published_size": False,
        "published_matches_local_version": True,
        "local_matches_published_sha256": True,
    }) is False
    assert rr.strict_public_match_ok({
        "local_matches_published_size": True,
        "published_matches_local_version": False,
        "local_matches_published_sha256": True,
    }) is False
    assert rr.strict_public_match_ok({
        "local_matches_published_size": True,
        "published_matches_local_version": True,
        "local_matches_published_sha256": False,
    }) is False
