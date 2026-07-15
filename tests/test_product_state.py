from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def valid_product_state(active=True):
    return {
        "schema_version": 1,
        "product": "local_pro",
        "entitlement": {
            "state": "active" if active else "inactive",
            "plan": "pro_local" if active else None,
            "expires_human": "2027-01-01" if active else None,
            "checked": "2026-07-15" if active else None,
        },
        "capabilities": ["agent_cockpit", "model_benchmarking"],
    }


def plugin(**overrides):
    values = {
        "name": "pro",
        "version": "1.0.0",
        "product_id": "local_pro",
        "host_api_version": 1,
        "product_state": lambda: valid_product_state(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_discovery_accepts_exact_product_contract(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name="pro", load=lambda: plugin())
    ])

    loaded = plugins_mod.discover()[0]

    assert loaded.state == "ready"
    assert loaded.host_api_version == 1
    assert loaded.product_state == valid_product_state()
    assert loaded.ok is True


def test_discovery_quarantines_missing_or_mismatched_host_api(monkeypatch):
    plugins = [plugin(host_api_version=None), plugin(host_api_version=2)]
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name=f"pro-{index}", load=lambda value=value: value)
        for index, value in enumerate(plugins)
    ])

    loaded = plugins_mod.discover()

    assert [item.state for item in loaded] == ["incompatible", "incompatible"]
    assert all(item.ok is False for item in loaded)
    assert all(item.product_state is None for item in loaded)


def test_discovery_quarantines_raising_or_non_exact_product_state(monkeypatch):
    def raises():
        raise RuntimeError("receipt path and secret should not escape")

    extra = valid_product_state()
    extra["machine"] = "secret-machine-id"
    plugins = [plugin(product_state=raises), plugin(product_state=lambda: extra)]
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name=f"pro-{index}", load=lambda value=value: value)
        for index, value in enumerate(plugins)
    ])

    loaded = plugins_mod.discover()

    assert [item.state for item in loaded] == ["load_error", "incompatible"]
    assert all(item.product_state is None for item in loaded)
    assert "secret-machine-id" not in repr(loaded)


def test_capabilities_must_be_sorted_unique_stable_ids(monkeypatch):
    invalid = valid_product_state()
    invalid["capabilities"] = ["model_benchmarking", "agent_cockpit", "agent_cockpit"]
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name="pro", load=lambda: plugin(product_state=lambda: invalid))
    ])

    assert plugins_mod.discover()[0].state == "incompatible"


def test_boolean_schema_version_is_not_contract_version_one(monkeypatch):
    invalid = valid_product_state()
    invalid["schema_version"] = True
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name="pro", load=lambda: plugin(product_state=lambda: invalid))
    ])

    assert plugins_mod.discover()[0].state == "incompatible"


@pytest.mark.parametrize("invalid_version", [7, object()])
def test_product_endpoint_quarantines_non_string_plugin_version(
    monkeypatch, flask_app, invalid_version
):
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(name="pro", load=lambda: plugin(version=invalid_version))
    ])
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_cloud_session",
        SimpleNamespace(
            product_state=lambda **_kwargs: {
                "state": "not_configured",
                "execution_available": False,
            }
        ),
    )

    response = flask_app.test_client().get("/api/product/state")

    assert response.status_code == 200
    assert response.is_json
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["local_pro"] == {
        "state": "incompatible",
        "plugin_version": "?",
        "host_api_version": 1,
    }


def test_entitlement_claims_require_a_known_active_plan_and_canonical_expiry(monkeypatch):
    invalid_states = []
    for entitlement in [
        {
            "state": "active",
            "plan": None,
            "expires_human": "2027-01-01",
            "checked": "2026-07-15",
        },
        {
            "state": "active",
            "plan": "pro_local",
            "expires_human": "some day",
            "checked": "2026-07-15",
        },
        {
            "state": "inactive",
            "plan": None,
            "expires_human": "2027-01-01",
            "checked": None,
        },
    ]:
        state = valid_product_state()
        state["entitlement"] = entitlement
        invalid_states.append(state)
    monkeypatch.setattr(plugins_mod, "_entry_points", lambda: [
        SimpleNamespace(
            name=f"pro-{index}",
            load=lambda state=state: plugin(product_state=lambda: state),
        )
        for index, state in enumerate(invalid_states)
    ])

    assert [item.state for item in plugins_mod.discover()] == [
        "incompatible",
        "incompatible",
        "incompatible",
    ]


def test_product_state_endpoint_keeps_local_pro_and_cloud_authorities_separate(
    monkeypatch, flask_app
):
    pro = LoadedPlugin(
        "pro",
        "1.0.0",
        plugin(),
        product_id="local_pro",
        host_api_version=1,
        product_state=valid_product_state(),
    )
    monkeypatch.setattr(plugins_mod, "discover", lambda: [pro])

    from backend import api as api_mod

    class SignedOutCloud:
        def product_state(self, *, refresh=False):
            assert refresh is True
            return {"state": "signed_out", "execution_available": False}

    monkeypatch.setattr(api_mod, "_cloud_session", SignedOutCloud())
    response = flask_app.test_client().get("/api/product/state")

    assert response.status_code == 200
    assert response.get_json() == {
        "schema_version": 1,
        "execution_default": "local",
        "local": {"state": "ready"},
        "local_pro": {
            "state": "ready",
            "plugin_version": "1.0.0",
            "host_api_version": 1,
            **valid_product_state(),
        },
        "cloud": {"state": "signed_out", "execution_available": False},
    }


def test_product_state_endpoint_reports_absent_pro_without_calling_pro_routes(
    monkeypatch, flask_app
):
    monkeypatch.setattr(plugins_mod, "discover", lambda: [])
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_cloud_session",
        SimpleNamespace(product_state=lambda **_kwargs: {"state": "not_configured", "execution_available": False}),
    )

    data = flask_app.test_client().get("/api/product/state").get_json()

    assert data["local_pro"] == {"state": "absent"}
    assert data["execution_default"] == "local"
