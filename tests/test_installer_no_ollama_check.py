# tests/test_installer_no_ollama_check.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_has_no_ollama_registry_check():
    text = (ROOT / "installer.iss").read_text(encoding="utf-8", errors="ignore")
    assert "Services\\Ollama" not in text
    assert "Ollama was not detected" not in text


def test_installer_registers_exact_lac_oauth_callback_protocol():
    text = (ROOT / "installer.iss").read_text(encoding="utf-8", errors="ignore")
    assert "[Registry]" in text
    assert 'Root: HKLM; Subkey: "Software\\Classes\\lac"' in text
    assert 'Root: HKCU; Subkey: "Software\\Classes\\lac"' not in text
    assert 'Subkey: "Software\\Classes\\lac"' in text
    assert 'ValueData: "URL:LAC OAuth Callback"' in text
    assert 'ValueName: "URL Protocol"' in text
    assert 'Subkey: "Software\\Classes\\lac\\shell\\open\\command"' in text
    assert 'ValueData: """{app}\\{#MyAppExeName}"" ""%1"""' in text
    assert "Value type:" not in text
    assert text.count("ValueType: string") >= 4


def test_packaged_app_collects_windows_dpapi_module():
    text = (ROOT / "build.spec").read_text(encoding="utf-8", errors="ignore")
    assert '"win32crypt"' in text
