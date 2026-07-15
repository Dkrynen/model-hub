from __future__ import annotations

from types import SimpleNamespace

from backend.cloud_session import CloudSessionError
from backend.cloud_tokens import SecureTokenStoreError


class FakeCloudSession:
    def __init__(self):
        self.calls = []

    def start_authorization(self, provider):
        self.calls.append(("start", provider))
        return {
            "state": "authorizing",
            "provider": provider,
            "authorization_url": "https://api.example.test/secret-browser-route",
        }

    def complete_authorization(self, uri):
        self.calls.append(("callback", uri))
        return {"state": "connected"}

    def logout(self):
        self.calls.append(("logout",))
        return {"state": "signed_out"}


def test_cloud_auth_start_opens_via_backend_without_returning_remote_url(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = FakeCloudSession()
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)

    response = flask_app.test_client().post("/api/cloud/auth/start", json={"provider": "google"})

    assert response.status_code == 200
    assert response.get_json() == {"state": "authorizing", "provider": "google"}
    assert cloud.calls == [("start", "google")]


def test_cloud_callback_requires_an_exact_bounded_body(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = FakeCloudSession()
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)
    client = flask_app.test_client()

    assert client.post("/api/cloud/auth/callback", json={"callback_uri": "lac://oauth/callback?code=x", "extra": 1}).status_code == 400
    response = client.post(
        "/api/cloud/auth/callback",
        json={"callback_uri": "lac://oauth/callback?code=" + "c" * 43},
    )

    assert response.status_code == 200
    assert response.get_json() == {"state": "connected"}
    assert cloud.calls == [("callback", "lac://oauth/callback?code=" + "c" * 43)]


def test_cloud_api_returns_only_stable_error_codes(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = SimpleNamespace(
        start_authorization=lambda _provider: (_ for _ in ()).throw(CloudSessionError("cloud_not_configured"))
    )
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)

    response = flask_app.test_client().post("/api/cloud/auth/start", json={"provider": "github"})

    assert response.status_code == 400
    assert response.get_json() == {"error": {"code": "cloud_not_configured"}}


def test_cloud_callback_maps_secure_storage_failure_to_bounded_json(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = SimpleNamespace(
        complete_authorization=lambda _uri: (_ for _ in ()).throw(
            SecureTokenStoreError("secure_storage_unavailable")
        )
    )
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)

    response = flask_app.test_client().post(
        "/api/cloud/auth/callback",
        json={"callback_uri": "lac://oauth/callback?code=" + "c" * 43},
    )

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": {"code": "secure_storage_unavailable"}}


def test_cloud_logout_maps_secure_storage_clear_failure_to_bounded_json(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = SimpleNamespace(
        logout=lambda: (_ for _ in ()).throw(
            SecureTokenStoreError("secure_storage_unavailable")
        )
    )
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)

    response = flask_app.test_client().post("/api/cloud/logout")

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": {"code": "secure_storage_unavailable"}}
