import os, sys
from backend import self_invoke


def test_cli_prefix_dev(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    p = self_invoke.cli_prefix()
    assert p[0] == sys.executable and p[-1].endswith("cli.py")


def test_cli_prefix_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert self_invoke.cli_prefix() == [sys.executable]


def test_window_prefix_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert self_invoke.window_prefix() == [sys.executable, "--window"]
