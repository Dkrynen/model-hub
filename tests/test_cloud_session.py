from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.cloud_session import (
    CloudHttpResponse,
    CloudSession,
    CloudSessionError,
    parse_oauth_callback_uri,
    resolve_cloud_api_origin,
    urllib_transport,
)
from backend.cloud_tokens import SecureTokenStoreError


ACCESS = "a" * 43
REFRESH = "r" * 43
ROTATED_ACCESS = "b" * 43
ROTATED_REFRESH = "s" * 43
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


class MemoryTokenStore:
    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def load(self):
        return self.value

    def save(self, value):
        self.value = value
        self.saved.append(value)

    def clear(self):
        self.value = None


class StubTransport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        if not self.responses:
            raise AssertionError(f"unexpected cloud request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status, payload):
    return CloudHttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def token_payload(access=ACCESS, refresh=REFRESH):
    return {
        "token_type": "Bearer",
        "access_token": access,
        "expires_in": 900,
        "refresh_token": refresh,
    }


def account_payload():
    return {
        "account": {
            "id": "11111111-1111-4111-8111-111111111111",
            "primary_email": "duan@example.test",
            "display_name": "Duan",
            "avatar_url": None,
            "status": "active",
            "created_at": 1_700_000_000,
        }
    }


def entitlements_payload():
    return {
        "entitlements": [
            {
                "plan": "pro_cloud",
                "state": "active",
                "effective_at": 1_700_000_000,
                "access_until": 1_800_000_000,
                "export_until": None,
                "updated_at": 1_700_000_001,
            }
        ]
    }


def usage_payload():
    return {
        "monthlyCredits": 12,
        "weeklyCredits": 7,
        "shortWindowCredits": 2,
        "activeJobs": 0,
        "queuedJobs": 0,
        "resetAt": {"monthly": 10, "weekly": 20, "five_hour": 30},
    }


def test_unconfigured_cloud_is_inert_even_with_a_persisted_token():
    transport = StubTransport()
    session = CloudSession(
        api_origin="https://replace-with-approved-cloud-api.example.invalid",
        token_store=MemoryTokenStore(REFRESH),
        transport=transport,
    )

    assert session.product_state(refresh=True) == {
        "state": "not_configured",
        "execution_available": False,
    }
    assert transport.calls == []


def test_cloud_origin_override_is_source_only_and_https_bounded(monkeypatch):
    import backend.cloud_session as cloud_mod

    monkeypatch.setattr(cloud_mod.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LAC_CLOUD_API_ORIGIN", "https://staging.example.test")
    assert resolve_cloud_api_origin() == "https://staging.example.test"

    monkeypatch.setenv("LAC_CLOUD_API_ORIGIN", "https://staging.example.test/path")
    assert resolve_cloud_api_origin() == cloud_mod.DEFAULT_CLOUD_API_ORIGIN

    monkeypatch.setattr(cloud_mod.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LAC_CLOUD_API_ORIGIN", "https://attacker.example")
    assert resolve_cloud_api_origin() == cloud_mod.DEFAULT_CLOUD_API_ORIGIN


def test_desktop_transport_never_follows_http_redirects_with_credentials():
    requests = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/capture")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        result = urllib_transport(
            "GET",
            f"{origin}/start",
            {"Authorization": f"Bearer {ACCESS}"},
            None,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == 302
    assert requests == [("/start", f"Bearer {ACCESS}")]

def test_authorization_start_uses_desktop_pkce_and_opens_the_system_browser():
    opened = []
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=MemoryTokenStore(),
        transport=StubTransport(),
        browser_open=lambda url: opened.append(url) or True,
        token_factory=lambda _bytes: VERIFIER,
        monotonic=lambda: 100.0,
    )

    result = session.start_authorization("google")

    assert result["state"] == "authorizing"
    assert result["provider"] == "google"
    assert opened == [result["authorization_url"]]
    parsed = urlsplit(opened[0])
    assert parsed.path == "/v1/auth/oauth/google/start"
    assert parse_qs(parsed.query) == {
        "client": ["desktop"],
        "code_challenge": ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"],
        "code_challenge_method": ["S256"],
    }


def test_authorizing_state_survives_status_poll_without_network_access():
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=MemoryTokenStore(),
        transport=StubTransport(),
        browser_open=lambda _url: True,
        token_factory=lambda _bytes: VERIFIER,
        monotonic=lambda: 100.0,
    )
    session.start_authorization("google")

    assert session.product_state(refresh=True) == {
        "state": "authorizing",
        "execution_available": False,
    }


def test_unhashable_provider_input_is_rejected_as_a_stable_client_error():
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=MemoryTokenStore(),
        transport=StubTransport(),
    )

    with pytest.raises(CloudSessionError, match="invalid_provider"):
        session.start_authorization([])


@pytest.mark.parametrize(
    "uri",
    [
        "https://oauth/callback?code=" + "c" * 43,
        "lac://wrong/callback?code=" + "c" * 43,
        "lac://oauth/not-callback?code=" + "c" * 43,
        "lac://oauth/callback",
        "lac://oauth/callback?code=a&code=b",
        "lac://oauth/callback?code=" + "c" * 43 + "&extra=1",
        "lac://oauth/callback?code=" + "c" * 43 + "#fragment",
    ],
)
def test_oauth_callback_uri_rejects_ambiguous_or_untrusted_shapes(uri):
    with pytest.raises(CloudSessionError, match="invalid_callback"):
        parse_oauth_callback_uri(uri)


def test_callback_exchanges_once_and_persists_only_the_refresh_token():
    store = MemoryTokenStore()
    transport = StubTransport([response(200, token_payload())])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
        browser_open=lambda _url: True,
        token_factory=lambda _bytes: VERIFIER,
        monotonic=lambda: 100.0,
        clock=lambda: 1_700_000_000.0,
        device_name="Test LAC",
        platform_name="windows",
    )
    session.start_authorization("github")

    result = session.complete_authorization("lac://oauth/callback?code=" + "c" * 43)

    assert result == {"state": "connected"}
    assert store.saved == [REFRESH]
    method, url, headers, body = transport.calls[0]
    assert (method, url) == ("POST", "https://api.example.test/v1/auth/token")
    assert "Authorization" not in headers
    assert json.loads(body) == {
        "grant_type": "authorization_code",
        "code": "c" * 43,
        "code_verifier": VERIFIER,
        "device_name": "Test LAC",
        "platform": "windows",
    }


def test_callback_revokes_issued_access_when_secure_storage_rejects_refresh_token():
    class FailingSaveTokenStore(MemoryTokenStore):
        def save(self, value):
            raise SecureTokenStoreError("secure_storage_unavailable")

    store = FailingSaveTokenStore()
    transport = StubTransport([
        response(200, token_payload()),
        response(204, {}),
    ])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
        browser_open=lambda _url: True,
        token_factory=lambda _bytes: VERIFIER,
        monotonic=lambda: 100.0,
        clock=lambda: 1_700_000_000.0,
    )
    session.start_authorization("github")

    with pytest.raises(CloudSessionError, match="secure_storage_unavailable"):
        session.complete_authorization("lac://oauth/callback?code=" + "c" * 43)

    assert store.value is None
    assert len(transport.calls) == 2
    method, url, headers, body = transport.calls[1]
    assert (method, url, body) == ("POST", "https://api.example.test/v1/auth/logout", None)
    assert headers["Authorization"] == f"Bearer {ACCESS}"


def test_token_response_is_exact_and_extra_fields_fail_closed():
    store = MemoryTokenStore()
    invalid = {**token_payload(), "scope": "unexpected"}
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=StubTransport([response(200, invalid)]),
        browser_open=lambda _url: True,
        token_factory=lambda _bytes: VERIFIER,
        monotonic=lambda: 100.0,
    )
    session.start_authorization("google")

    with pytest.raises(CloudSessionError, match="invalid_response"):
        session.complete_authorization("lac://oauth/callback?code=" + "c" * 43)

    assert store.saved == []


def test_persisted_session_rotates_refresh_and_returns_bounded_public_state():
    store = MemoryTokenStore(REFRESH)
    transport = StubTransport([
        response(200, token_payload(ROTATED_ACCESS, ROTATED_REFRESH)),
        response(200, account_payload()),
        response(200, entitlements_payload()),
        response(200, usage_payload()),
    ])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
        clock=lambda: 1_700_000_000.0,
    )

    state = session.product_state(refresh=True)

    assert state == {
        "state": "connected",
        "execution_available": False,
        "account": account_payload()["account"],
        "entitlements": entitlements_payload()["entitlements"],
        "usage": usage_payload(),
    }
    assert store.value == ROTATED_REFRESH
    assert all(REFRESH not in json.dumps(call, default=str) for call in transport.calls[1:])
    assert transport.calls[1][2]["Authorization"] == f"Bearer {ROTATED_ACCESS}"


def test_concurrent_state_refresh_rotates_a_refresh_token_only_once():
    class ConcurrentTransport:
        def __init__(self):
            self.lock = threading.Lock()
            self.refresh_calls = 0

        def __call__(self, method, url, headers, body):
            if url.endswith("/v1/auth/token"):
                with self.lock:
                    self.refresh_calls += 1
                time.sleep(0.05)
                return response(200, token_payload(ROTATED_ACCESS, ROTATED_REFRESH))
            if url.endswith("/v1/account"):
                return response(200, account_payload())
            if url.endswith("/v1/entitlements"):
                return response(200, entitlements_payload())
            if url.endswith("/v1/usage"):
                return response(200, usage_payload())
            raise AssertionError(f"unexpected cloud request: {method} {url}")

    store = MemoryTokenStore(REFRESH)
    transport = ConcurrentTransport()
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
        clock=lambda: 1_700_000_000.0,
    )
    barrier = threading.Barrier(3)
    states = []

    def refresh_state():
        barrier.wait()
        states.append(session.product_state(refresh=True))

    threads = [threading.Thread(target=refresh_state) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert transport.refresh_calls == 1
    assert len(states) == 2
    assert all(state["state"] == "connected" for state in states)
    assert store.value == ROTATED_REFRESH


def test_network_failure_is_stable_and_never_exposes_credentials():
    store = MemoryTokenStore(REFRESH)
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=StubTransport([OSError(f"network failed near {REFRESH}")]),
    )

    state = session.product_state(refresh=True)

    assert state == {
        "state": "unreachable",
        "execution_available": False,
        "error": {"code": "provider_unavailable"},
    }
    assert REFRESH not in json.dumps(state)
    assert store.value == REFRESH


def test_rejected_refresh_clears_local_credentials_and_signs_out():
    store = MemoryTokenStore(REFRESH)
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=StubTransport([response(401, {"error": {"code": "auth_required"}})]),
    )

    assert session.product_state(refresh=True) == {
        "state": "signed_out",
        "execution_available": False,
    }
    assert store.value is None


def test_logout_refreshes_then_revokes_the_desktop_session_before_local_clear():
    store = MemoryTokenStore(REFRESH)
    transport = StubTransport([
        response(200, token_payload(ROTATED_ACCESS, ROTATED_REFRESH)),
        response(204, {}),
    ])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
    )

    assert session.logout() == {"state": "signed_out"}

    assert [call[1] for call in transport.calls] == [
        "https://api.example.test/v1/auth/token",
        "https://api.example.test/v1/auth/logout",
    ]
    assert transport.calls[1][2]["Authorization"] == f"Bearer {ROTATED_ACCESS}"
    assert store.value is None


def test_logout_rotates_an_expired_in_memory_access_before_remote_revoke():
    store = MemoryTokenStore(REFRESH)
    transport = StubTransport([
        response(200, token_payload(ROTATED_ACCESS, ROTATED_REFRESH)),
        response(204, {}),
    ])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
        clock=lambda: 1_700_000_000.0,
    )
    session._access_token = ACCESS
    session._access_expires_at = 1_699_999_999.0

    assert session.logout() == {"state": "signed_out"}

    assert [call[1] for call in transport.calls] == [
        "https://api.example.test/v1/auth/token",
        "https://api.example.test/v1/auth/logout",
    ]
    assert transport.calls[1][2]["Authorization"] == f"Bearer {ROTATED_ACCESS}"
    assert store.value is None


def test_logout_surfaces_secure_storage_clear_failure_instead_of_false_sign_out():
    class FailingClearTokenStore(MemoryTokenStore):
        def clear(self):
            raise SecureTokenStoreError("secure_storage_unavailable")

    store = FailingClearTokenStore(REFRESH)
    transport = StubTransport([
        response(200, token_payload(ROTATED_ACCESS, ROTATED_REFRESH)),
        response(204, {}),
    ])
    session = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=transport,
    )

    with pytest.raises(SecureTokenStoreError, match="secure_storage_unavailable"):
        session.logout()

    assert store.value == ROTATED_REFRESH
    restarted = CloudSession(
        api_origin="https://api.example.test",
        token_store=store,
        transport=StubTransport(),
    )
    assert restarted.product_state() == {
        "state": "connected",
        "execution_available": False,
    }
