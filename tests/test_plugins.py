"""Plugin seam: entry-point discovery with per-plugin error isolation."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import discover, LoadedPlugin


class FakeEntryPoint:
    def __init__(self, name, obj=None, exc=None):
        self.name = name
        self._obj = obj
        self._exc = exc

    def load(self):
        if self._exc:
            raise self._exc
        return self._obj


def _patch_eps(monkeypatch, eps):
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: eps)


def test_discover_loads_wellformed_plugin(monkeypatch):
    plug = SimpleNamespace(name="pro", version="0.1.0")
    _patch_eps(monkeypatch, [FakeEntryPoint("pro", obj=plug)])
    out = discover()
    assert len(out) == 1
    assert out[0].ok
    assert out[0].name == "pro"
    assert out[0].version == "0.1.0"
    assert out[0].obj is plug


def test_discover_isolates_broken_plugin(monkeypatch):
    good = SimpleNamespace(name="good", version="1.0")
    _patch_eps(monkeypatch, [
        FakeEntryPoint("broken", exc=ImportError("boom")),
        FakeEntryPoint("good", obj=good),
    ])
    out = discover()
    assert len(out) == 2
    broken = next(p for p in out if p.name == "broken")
    assert not broken.ok
    assert "boom" in broken.error
    assert next(p for p in out if p.name == "good").ok


def test_discover_defaults_missing_metadata(monkeypatch):
    plug = SimpleNamespace()  # no name/version attrs
    _patch_eps(monkeypatch, [FakeEntryPoint("bare", obj=plug)])
    out = discover()
    assert out[0].name == "bare"       # falls back to entry-point name
    assert out[0].version == "?"
    assert out[0].ok


def test_discover_empty(monkeypatch):
    _patch_eps(monkeypatch, [])
    assert discover() == []
