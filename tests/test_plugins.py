"""Plugin seam: entry-point discovery with per-plugin error isolation."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import discover, LoadedPlugin


def _contract_plugin(**values):
    state = {
        "schema_version": 1,
        "product": "local_pro",
        "entitlement": {
            "state": "inactive",
            "plan": None,
            "expires_human": None,
            "checked": None,
        },
        "capabilities": [],
    }
    metadata = {
        "product_id": "local_pro",
        "host_api_version": 1,
        "product_state": lambda: state,
    }
    metadata.update(values)
    return SimpleNamespace(**metadata)


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
    plug = _contract_plugin(name="pro", version="0.1.0")
    _patch_eps(monkeypatch, [FakeEntryPoint("pro", obj=plug)])
    out = discover()
    assert len(out) == 1
    assert out[0].ok
    assert out[0].name == "pro"
    assert out[0].version == "0.1.0"
    assert out[0].obj is plug


def test_discover_isolates_broken_plugin(monkeypatch):
    good = _contract_plugin(name="good", version="1.0")
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
    plug = _contract_plugin()  # no name/version attrs
    _patch_eps(monkeypatch, [FakeEntryPoint("bare", obj=plug)])
    out = discover()
    assert out[0].name == "bare"       # falls back to entry-point name
    assert out[0].version == "?"
    assert out[0].ok


def test_discover_empty(monkeypatch):
    _patch_eps(monkeypatch, [])
    assert discover() == []


def test_generic_community_plugin_does_not_need_a_product_contract(monkeypatch):
    calls = []
    plug = SimpleNamespace(
        name="community-tools",
        version="2.0.0",
        product_state=lambda: calls.append("called"),
    )
    _patch_eps(monkeypatch, [FakeEntryPoint("community-tools", obj=plug)])

    loaded = discover()[0]

    assert loaded.ok is True
    assert loaded.state == "ready"
    assert loaded.product_id is None
    assert loaded.product_state is None
    assert calls == []


def test_discover_isolates_raising_metadata_property(monkeypatch):
    """A plugin whose name/version PROPERTY raises must become an error record,
    not an exception escaping discover()."""

    class ExplodingMeta:
        @property
        def name(self):
            raise RuntimeError("metadata bomb")

        version = "1.0"

    _patch_eps(monkeypatch, [FakeEntryPoint("volatile", obj=ExplodingMeta())])
    out = discover()
    assert len(out) == 1
    assert not out[0].ok
    assert "metadata bomb" in out[0].error
    assert out[0].name == "volatile"  # falls back to the entry-point name


def test_discover_isolates_raising_product_state_descriptor(monkeypatch):
    class ExplodingProductState:
        name = "pro"
        version = "1.0.0"
        product_id = "local_pro"
        host_api_version = 1

        @property
        def product_state(self):
            raise RuntimeError("descriptor must stay isolated")

    good = _contract_plugin(name="pro", version="1.0.1")
    _patch_eps(monkeypatch, [
        FakeEntryPoint("broken-pro", obj=ExplodingProductState()),
        FakeEntryPoint("good-pro", obj=good),
    ])

    loaded = discover()

    assert [item.state for item in loaded] == ["load_error", "ready"]
    assert loaded[0].error == "product_state_failed"


def test_boolean_host_api_version_is_not_integer_version_one(monkeypatch):
    plug = _contract_plugin(name="pro", version="1.0.0", host_api_version=True)
    _patch_eps(monkeypatch, [FakeEntryPoint("pro", obj=plug)])

    loaded = discover()[0]

    assert loaded.state == "incompatible"
    assert loaded.host_api_version is None
