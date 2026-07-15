"""Fail-closed LAC Cloud desktop OAuth and public account session."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, Mapping

from .cloud_tokens import DpapiTokenStore, SecureTokenStoreError

DEFAULT_CLOUD_API_ORIGIN = "https://replace-with-approved-cloud-api.example.invalid"
_UNCONFIGURED_HOSTS = {"replace-with-approved-cloud-api.example.invalid"}
_OPAQUE_CREDENTIAL_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_OPAQUE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_CAPTURE_LIMIT = 128 * 1024
_AUTHORIZATION_TTL_SECONDS = 10 * 60
_ENTITLEMENT_STATES = {
    "active", "trialing", "cancel_at_period_end", "past_due", "unpaid", "revoked",
}
_ACCOUNT_STATES = {"active", "deleting", "deleted"}
_STABLE_FAILURE_CODES = {
    "auth_required",
    "quota_exhausted",
    "entitlement_required",
    "conflict_or_concurrency",
    "abuse_rate_limited",
    "provider_unavailable",
}
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class CloudSessionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


@dataclass(frozen=True)
class CloudHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


def _bounded_read(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > _CAPTURE_LIMIT:
                raise CloudSessionError("invalid_response")
        except ValueError as exc:
            raise CloudSessionError("invalid_response") from exc
    body = response.read(_CAPTURE_LIMIT + 1)
    if len(body) > _CAPTURE_LIMIT:
        raise CloudSessionError("invalid_response")
    return body


def urllib_transport(method, url, headers, body) -> CloudHttpResponse:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=15) as response:
            return CloudHttpResponse(
                status=int(response.status),
                headers={key.lower(): value for key, value in response.headers.items()},
                body=_bounded_read(response),
            )
    except urllib.error.HTTPError as exc:
        return CloudHttpResponse(
            status=int(exc.code),
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=_bounded_read(exc),
        )
    except CloudSessionError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise CloudSessionError("provider_unavailable") from exc


def _normalized_origin(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        loopback_dev = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback_dev and not getattr(sys, "frozen", False)):
            return None
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except ValueError:
        return None


def resolve_cloud_api_origin() -> str:
    candidate = DEFAULT_CLOUD_API_ORIGIN
    if not getattr(sys, "frozen", False):
        candidate = os.environ.get("LAC_CLOUD_API_ORIGIN", candidate)
    return _normalized_origin(candidate) or DEFAULT_CLOUD_API_ORIGIN


def parse_oauth_callback_uri(uri: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(uri)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise CloudSessionError("invalid_callback") from exc
    if (
        parsed.scheme != "lac"
        or parsed.netloc != "oauth"
        or parsed.path != "/callback"
        or parsed.fragment
        or len(pairs) != 1
        or pairs[0][0] != "code"
        or _OPAQUE_CODE_PATTERN.fullmatch(pairs[0][1]) is None
    ):
        raise CloudSessionError("invalid_callback")
    return pairs[0][1]


def is_oauth_callback_uri(value: str) -> bool:
    try:
        parse_oauth_callback_uri(value)
        return True
    except CloudSessionError:
        return False


def _exact_record(value, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise CloudSessionError("invalid_response")
    return value


def _bounded_string(value, *, nullable=False, limit=320, allow_empty=False):
    if nullable and value is None:
        return None
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or sum(2 if ord(char) > 0xFFFF else 1 for char in value) > limit
        or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
    ):
        raise CloudSessionError("invalid_response")
    return value


def _timestamp(value, *, nullable=False):
    if nullable and value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SAFE_INTEGER
    ):
        raise CloudSessionError("invalid_response")
    return value


def _decode_response(response: CloudHttpResponse):
    content_type = next(
        (value for key, value in response.headers.items() if key.lower() == "content-type"),
        "",
    )
    if not content_type.lower().split(";", 1)[0].strip() == "application/json":
        raise CloudSessionError("invalid_response")
    if len(response.body) > _CAPTURE_LIMIT:
        raise CloudSessionError("invalid_response")
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudSessionError("invalid_response") from exc


def _failure_code(response: CloudHttpResponse) -> str:
    try:
        payload = _decode_response(response)
        outer = _exact_record(payload, {"error"})
        error = outer["error"]
        if not isinstance(error, dict) or not set(error).issubset({"code", "detail"}) or "code" not in error:
            return "provider_unavailable"
        code = error["code"]
        if not isinstance(code, str) or code not in _STABLE_FAILURE_CODES:
            return "provider_unavailable"
        detail = error.get("detail")
        if detail is not None and (
            not isinstance(detail, str)
            or not detail
            or len(detail) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in detail)
        ):
            return "provider_unavailable"
        return code
    except CloudSessionError:
        return "provider_unavailable"


def _parse_token(value) -> dict:
    data = _exact_record(value, {"token_type", "access_token", "expires_in", "refresh_token"})
    if (
        data["token_type"] != "Bearer"
        or not isinstance(data["access_token"], str)
        or _OPAQUE_CREDENTIAL_PATTERN.fullmatch(data["access_token"]) is None
        or not isinstance(data["refresh_token"], str)
        or _OPAQUE_CREDENTIAL_PATTERN.fullmatch(data["refresh_token"]) is None
        or isinstance(data["expires_in"], bool)
        or not isinstance(data["expires_in"], int)
        or not 1 <= data["expires_in"] <= 3600
    ):
        raise CloudSessionError("invalid_response")
    return dict(data)


def _parse_account(value) -> dict:
    outer = _exact_record(value, {"account"})
    account = _exact_record(
        outer["account"],
        {"id", "primary_email", "display_name", "avatar_url", "status", "created_at"},
    )
    parsed = {
        "id": _bounded_string(account["id"], limit=100),
        "primary_email": _bounded_string(account["primary_email"], nullable=True),
        "display_name": _bounded_string(
            account["display_name"], nullable=True, limit=120, allow_empty=True
        ),
        "avatar_url": _bounded_string(account["avatar_url"], nullable=True, limit=2048),
        "status": account["status"],
        "created_at": _timestamp(account["created_at"]),
    }
    if _OPAQUE_ID_PATTERN.fullmatch(parsed["id"]) is None:
        raise CloudSessionError("invalid_response")
    if parsed["avatar_url"] is not None:
        try:
            avatar = urllib.parse.urlsplit(parsed["avatar_url"])
        except ValueError as exc:
            raise CloudSessionError("invalid_response") from exc
        if avatar.scheme != "https" or not avatar.hostname or avatar.username is not None or avatar.password is not None:
            raise CloudSessionError("invalid_response")
    if parsed["status"] not in _ACCOUNT_STATES:
        raise CloudSessionError("invalid_response")
    return parsed


def _parse_entitlements(value) -> list[dict]:
    outer = _exact_record(value, {"entitlements"})
    rows = outer["entitlements"]
    if not isinstance(rows, list) or len(rows) > 2:
        raise CloudSessionError("invalid_response")
    parsed = []
    for value in rows:
        row = _exact_record(
            value,
            {"plan", "state", "effective_at", "access_until", "export_until", "updated_at"},
        )
        if row["plan"] not in {"pro_local", "pro_cloud"} or row["state"] not in _ENTITLEMENT_STATES:
            raise CloudSessionError("invalid_response")
        parsed.append({
            "plan": row["plan"],
            "state": row["state"],
            "effective_at": _timestamp(row["effective_at"]),
            "access_until": _timestamp(row["access_until"], nullable=True),
            "export_until": _timestamp(row["export_until"], nullable=True),
            "updated_at": _timestamp(row["updated_at"]),
        })
    if len({row["plan"] for row in parsed}) != len(parsed):
        raise CloudSessionError("invalid_response")
    return parsed


def _parse_usage(value) -> dict:
    data = _exact_record(
        value,
        {"monthlyCredits", "weeklyCredits", "shortWindowCredits", "activeJobs", "queuedJobs", "resetAt"},
    )
    reset = _exact_record(data["resetAt"], {"monthly", "weekly", "five_hour"})
    limits = {
        "monthlyCredits": 5_000,
        "weeklyCredits": 2_500,
        "shortWindowCredits": 1_000,
        "activeJobs": 3,
        "queuedJobs": 5,
    }
    for key, maximum in limits.items():
        if (
            isinstance(data[key], bool)
            or not isinstance(data[key], int)
            or not 0 <= data[key] <= maximum
        ):
            raise CloudSessionError("invalid_response")
    for key in ("monthly", "weekly", "five_hour"):
        _timestamp(reset[key])
    return {
        "monthlyCredits": data["monthlyCredits"],
        "weeklyCredits": data["weeklyCredits"],
        "shortWindowCredits": data["shortWindowCredits"],
        "activeJobs": data["activeJobs"],
        "queuedJobs": data["queuedJobs"],
        "resetAt": dict(reset),
    }


def parse_public_oauth_token_response_v1(value) -> dict:
    return _parse_token(value)


def parse_public_account_response_v1(value) -> dict:
    return {"account": _parse_account(value)}


def parse_public_entitlements_response_v1(value) -> dict:
    return {"entitlements": _parse_entitlements(value)}


def parse_public_usage_response_v1(value) -> dict:
    return _parse_usage(value)


def parse_public_stable_failure_response_v1(value) -> dict:
    outer = _exact_record(value, {"error"})
    error = outer["error"]
    if not isinstance(error, dict):
        raise CloudSessionError("invalid_response")
    expected = {"code", "detail"} if "detail" in error else {"code"}
    exact = _exact_record(error, expected)
    code = exact["code"]
    if not isinstance(code, str) or code not in _STABLE_FAILURE_CODES:
        raise CloudSessionError("invalid_response")
    result = {"code": code}
    if "detail" in exact:
        result["detail"] = _bounded_string(exact["detail"], limit=512)
    return {"error": result}


class CloudSession:
    def __init__(
        self,
        *,
        api_origin: str | None = None,
        token_store=None,
        transport: Callable = urllib_transport,
        browser_open: Callable[[str], bool] = webbrowser.open,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
        device_name: str | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.api_origin = _normalized_origin(api_origin or resolve_cloud_api_origin()) or DEFAULT_CLOUD_API_ORIGIN
        self.token_store = token_store or DpapiTokenStore()
        self.transport = transport
        self.browser_open = browser_open
        self.token_factory = token_factory
        self.monotonic = monotonic
        self.clock = clock
        self.device_name = (device_name or platform.node() or "LAC Desktop")[:120]
        self.platform_name = (platform_name or sys.platform)[:40]
        self._pending: tuple[str, float] | None = None
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._cache: dict | None = None
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return urllib.parse.urlsplit(self.api_origin).hostname not in _UNCONFIGURED_HOSTS

    def start_authorization(self, provider: str) -> dict:
        if not isinstance(provider, str) or provider not in {"google", "github"}:
            raise CloudSessionError("invalid_provider")
        if not self.configured:
            raise CloudSessionError("cloud_not_configured")
        verifier = self.token_factory(48)
        if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128 or re.fullmatch(r"[A-Za-z0-9._~-]+", verifier) is None:
            raise CloudSessionError("secure_random_unavailable")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        query = urllib.parse.urlencode({
            "client": "desktop",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        url = f"{self.api_origin}/v1/auth/oauth/{provider}/start?{query}"
        with self._lock:
            self._pending = (verifier, self.monotonic() + _AUTHORIZATION_TTL_SECONDS)
        try:
            opened = self.browser_open(url)
        except Exception as exc:
            with self._lock:
                self._pending = None
            raise CloudSessionError("browser_unavailable") from exc
        if opened is False:
            with self._lock:
                self._pending = None
            raise CloudSessionError("browser_unavailable")
        return {"state": "authorizing", "provider": provider, "authorization_url": url}

    def complete_authorization(self, callback_uri: str) -> dict:
        code = parse_oauth_callback_uri(callback_uri)
        with self._lock:
            pending = self._pending
            self._pending = None
            if pending is None or pending[1] < self.monotonic():
                raise CloudSessionError("authorization_expired")
            with self._token_rotation_lock():
                token = self._request(
                    "POST",
                    "/v1/auth/token",
                    body={
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": pending[0],
                        "device_name": self.device_name,
                        "platform": self.platform_name,
                    },
                    parser=_parse_token,
                )
                try:
                    self._accept_tokens(token)
                except SecureTokenStoreError as exc:
                    try:
                        self._request(
                            "POST",
                            "/v1/auth/logout",
                            access=token["access_token"],
                            expect_empty=True,
                        )
                    except Exception:
                        pass
                    self._clear_local()
                    raise CloudSessionError("secure_storage_unavailable") from exc
        return {"state": "connected"}

    def product_state(self, *, refresh: bool = False) -> dict:
        if not self.configured:
            return {"state": "not_configured", "execution_available": False}
        with self._lock:
            pending = self._pending
            if pending is not None and pending[1] >= self.monotonic():
                return {"state": "authorizing", "execution_available": False}
            if not refresh and self._cache is not None:
                return dict(self._cache)
        if not refresh:
            try:
                signed_in = self.token_store.load() is not None
            except SecureTokenStoreError as exc:
                return self._storage_error(exc.code)
            return {"state": "connected" if signed_in else "signed_out", "execution_available": False}
        try:
            access = self._ensure_access()
            if access is None:
                return {"state": "signed_out", "execution_available": False}
            account = self._request("GET", "/v1/account", access=access, parser=_parse_account)
            entitlements = self._request("GET", "/v1/entitlements", access=access, parser=_parse_entitlements)
            usage = self._request("GET", "/v1/usage", access=access, parser=_parse_usage)
            state = {
                "state": "connected",
                "execution_available": False,
                "account": account,
                "entitlements": entitlements,
                "usage": usage,
            }
            with self._lock:
                self._cache = state
            return dict(state)
        except SecureTokenStoreError as exc:
            return self._storage_error(exc.code)
        except CloudSessionError as exc:
            if exc.code == "auth_required":
                self._clear_local()
                return {"state": "signed_out", "execution_available": False}
            return {
                "state": "unreachable",
                "execution_available": False,
                "error": {"code": exc.code if exc.code == "invalid_response" else "provider_unavailable"},
            }
        except Exception:
            return {
                "state": "unreachable",
                "execution_available": False,
                "error": {"code": "provider_unavailable"},
            }

    def logout(self) -> dict:
        with self._lock:
            with self._token_rotation_lock():
                access = None
                if self.configured:
                    try:
                        access = self._ensure_access()
                    except Exception:
                        access = None
                if access:
                    try:
                        self._request("POST", "/v1/auth/logout", access=access, expect_empty=True)
                    except Exception:
                        pass
                self._clear_local(suppress_storage_error=False)
        return {"state": "signed_out"}

    def _ensure_access(self) -> str | None:
        with self._lock:
            with self._token_rotation_lock():
                if self._access_token and self._access_expires_at > self.clock() + 30:
                    return self._access_token
                refresh = self.token_store.load()
                if refresh is None:
                    return None
                token = self._request(
                    "POST",
                    "/v1/auth/token",
                    body={"grant_type": "refresh_token", "refresh_token": refresh},
                    parser=_parse_token,
                )
                self._accept_tokens(token)
                return token["access_token"]

    def _token_rotation_lock(self):
        factory = getattr(self.token_store, "rotation_lock", None)
        return factory() if callable(factory) else nullcontext()

    def _accept_tokens(self, token: dict) -> None:
        self.token_store.save(token["refresh_token"])
        with self._lock:
            self._access_token = token["access_token"]
            self._access_expires_at = self.clock() + token["expires_in"]
            self._cache = None

    def _request(self, method, path, *, access=None, body=None, parser=None, expect_empty=False):
        headers = {"Accept": "application/json"}
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if access is not None:
            headers["Authorization"] = f"Bearer {access}"
        try:
            response = self.transport(method, f"{self.api_origin}{path}", headers, encoded)
        except CloudSessionError:
            raise
        except Exception as exc:
            raise CloudSessionError("provider_unavailable") from exc
        if not isinstance(response, CloudHttpResponse):
            raise CloudSessionError("invalid_response")
        if not 200 <= response.status < 300:
            raise CloudSessionError(_failure_code(response))
        if expect_empty:
            return None
        payload = _decode_response(response)
        return parser(payload) if parser is not None else payload

    def _clear_local(self, *, suppress_storage_error: bool = True) -> None:
        storage_error = None
        try:
            self.token_store.clear()
        except SecureTokenStoreError as exc:
            storage_error = exc
        with self._lock:
            self._access_token = None
            self._access_expires_at = 0
            self._cache = None
            self._pending = None
        if storage_error is not None and not suppress_storage_error:
            raise storage_error

    @staticmethod
    def _storage_error(code: str) -> dict:
        return {
            "state": "unreachable",
            "execution_available": False,
            "error": {"code": code if code in {"corrupt_store", "secure_storage_unavailable"} else "secure_storage_unavailable"},
        }
