"""
Unit tests for GavaConnectClient and AsyncGavaConnectClient.

Verified against official API behaviour documented at developer.go.ke:
  - Token endpoint is GET with HTTP Basic Auth, grant_type as query param
  - PIN check: POST /checker/v1/pinbypin, body {"KRAPIN": pin}
  - TCC check: POST /v1/kra-tcc/validate, body {"kraPIN": pin, "tccNumber": tcc}
  - Error response contains top-level "ErrorMessage" key (no PINDATA)
  - Token expiry signalled by 401 with "expired" / "invalid_token" in body
    or 500 with "Access Token expired" in body

All tests use pytest-httpx's httpx_mock — no network traffic.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from kra_etims.gavaconnect import (
    AsyncGavaConnectClient,
    GavaConnectAuthError,
    GavaConnectClient,
    GavaConnectPINNotFoundError,
    GavaConnectTCCError,
    _PRODUCTION_BASE,
    _SANDBOX_BASE,
)

_KEY    = "test-consumer-key"
_SECRET = "test-consumer-secret"
_TOKEN  = "eyJhbGciOiJSUzI1NiJ9.test-token"
_PIN    = "A000123456B"
_TCC    = "TCC2024001234"

_TOKEN_RESPONSE = {"access_token": _TOKEN, "expires_in": 3600}

_PIN_SUCCESS = {
    "ResponseCode": "23000",
    "Message": "Valid PIN",
    "Status": "OK",
    "PINDATA": {
        "KRAPIN": "A***6B",
        "TypeOfTaxpayer": "Individual",
        "Name": "J**n D**",
        "StatusOfPIN": "Active",
    },
}

_PIN_NOT_FOUND = {"ErrorMessage": "PIN not found in KRA registry"}

_TCC_SUCCESS = {
    "Status": "OK",
    "TCCData": {"KRAPIN": _PIN},
}

_TCC_INVALID = {"Status": "FAILED", "TCCData": {}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client() -> GavaConnectClient:
    return GavaConnectClient(_KEY, _SECRET)


def _sandbox_client() -> GavaConnectClient:
    return GavaConnectClient(_KEY, _SECRET, sandbox=True)


def _token_url(base: str = _PRODUCTION_BASE) -> str:
    # grant_type is a query param on the GET request — must match exactly.
    return f"{base}/v1/token/generate?grant_type=client_credentials"


def _pin_url(base: str = _PRODUCTION_BASE) -> str:
    return f"{base}/checker/v1/pinbypin"


def _tcc_url(base: str = _PRODUCTION_BASE) -> str:
    return f"{base}/v1/kra-tcc/validate"


# ===========================================================================
# Sync — constructor
# ===========================================================================

class TestGavaConnectClientInit:

    def test_requires_key_and_secret(self) -> None:
        with pytest.raises(GavaConnectAuthError, match="consumer key"):
            GavaConnectClient()

    def test_accepts_positional_args(self) -> None:
        c = GavaConnectClient(_KEY, _SECRET)
        assert c._key == _KEY
        assert c._secret == _SECRET

    def test_production_base_is_default(self) -> None:
        c = GavaConnectClient(_KEY, _SECRET)
        assert c._base == _PRODUCTION_BASE

    def test_sandbox_flag_sets_sandbox_base(self) -> None:
        c = GavaConnectClient(_KEY, _SECRET, sandbox=True)
        assert c._base == _SANDBOX_BASE

    def test_sandbox_env_var_sets_sandbox_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAVACONNECT_SANDBOX", "true")
        c = GavaConnectClient(_KEY, _SECRET)
        assert c._base == _SANDBOX_BASE

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAVACONNECT_CONSUMER_KEY", _KEY)
        monkeypatch.setenv("GAVACONNECT_CONSUMER_SECRET", _SECRET)
        c = GavaConnectClient.from_env()
        assert c._key == _KEY
        assert c._secret == _SECRET


# ===========================================================================
# Sync — token management
# ===========================================================================

class TestTokenFetch:

    def test_token_fetched_on_first_request(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        _client().lookup_pin(_PIN)

        token_req = httpx_mock.get_requests()[0]
        assert token_req.method == "GET"
        assert "grant_type=client_credentials" in str(token_req.url)

    def test_token_uses_basic_auth(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        _client().lookup_pin(_PIN)

        token_req = httpx_mock.get_requests()[0]
        assert "Authorization" in token_req.headers
        assert token_req.headers["Authorization"].startswith("Basic ")

    def test_token_cached_across_calls(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        c = _client()
        c.lookup_pin(_PIN)
        c.lookup_pin(_PIN)

        token_reqs = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert len(token_reqs) == 1  # fetched once, cached for second call

    def test_expired_token_is_refreshed(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        c = _client()
        c.lookup_pin(_PIN)
        c._token_expiry = 0.0  # simulate expiry
        c.lookup_pin(_PIN)

        token_reqs = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert len(token_reqs) == 2  # fetched twice

    def test_401_on_token_fetch_raises_auth_error(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), status_code=401)

        with pytest.raises(GavaConnectAuthError, match="401"):
            _client().lookup_pin(_PIN)

    def test_token_expiry_mid_request_retries_once(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        # First API call returns 401 expired
        httpx_mock.add_response(
            method="POST", url=_pin_url(), status_code=401,
            json={"fault": {"faultstring": "Access Token expired"}},
        )
        # Second token fetch after expiry
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        # Retry succeeds
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        result = _client().lookup_pin(_PIN)
        assert "PINDATA" in result

    def test_500_with_expired_body_triggers_token_refresh(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(
            method="POST", url=_pin_url(), status_code=500,
            text='{"fault":{"faultstring":"Access Token expired"}}',
        )
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        result = _client().lookup_pin(_PIN)
        assert result["Status"] == "OK"


# ===========================================================================
# Sync — lookup_pin
# ===========================================================================

class TestLookupPin:

    def test_success_returns_full_response(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        result = _client().lookup_pin(_PIN)
        assert result["Status"] == "OK"
        assert "PINDATA" in result
        assert result["PINDATA"]["StatusOfPIN"] == "Active"

    def test_request_body_uses_uppercase_krapin(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        _client().lookup_pin(_PIN)

        import json
        pin_req = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
        body = json.loads(pin_req.content)
        # Field name is uppercase KRAPIN — this is GavaConnect's convention.
        assert "KRAPIN" in body
        assert body["KRAPIN"] == _PIN

    def test_request_uses_bearer_token(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        _client().lookup_pin(_PIN)

        pin_req = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
        assert pin_req.headers["Authorization"] == f"Bearer {_TOKEN}"

    def test_pin_not_found_raises_error(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_NOT_FOUND)

        with pytest.raises(GavaConnectPINNotFoundError, match="not found"):
            _client().lookup_pin(_PIN)

    def test_sandbox_uses_sandbox_url(self, httpx_mock) -> None:
        httpx_mock.add_response(
            method="GET", url=_token_url(_SANDBOX_BASE), json=_TOKEN_RESPONSE
        )
        httpx_mock.add_response(
            method="POST", url=_pin_url(_SANDBOX_BASE), json=_PIN_SUCCESS
        )

        _sandbox_client().lookup_pin(_PIN)

        reqs = httpx_mock.get_requests()
        assert all(_SANDBOX_BASE in str(r.url) for r in reqs)


# ===========================================================================
# Sync — check_tcc
# ===========================================================================

class TestCheckTCC:

    def test_success_returns_full_response(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_tcc_url(), json=_TCC_SUCCESS)

        result = _client().check_tcc(_PIN, _TCC)
        assert result["Status"] == "OK"
        assert "TCCData" in result

    def test_request_body_uses_lowercase_krapin(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_tcc_url(), json=_TCC_SUCCESS)

        _client().check_tcc(_PIN, _TCC)

        import json
        tcc_req = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
        body = json.loads(tcc_req.content)
        # TCC endpoint uses lowercase-camel kraPIN — GavaConnect inconsistency.
        assert "kraPIN" in body
        assert body["kraPIN"] == _PIN
        assert body["tccNumber"] == _TCC

    def test_invalid_tcc_raises_error(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_tcc_url(), json=_TCC_INVALID)

        with pytest.raises(GavaConnectTCCError):
            _client().check_tcc(_PIN, _TCC)


# ===========================================================================
# Async — mirrors of the sync tests
# ===========================================================================

class TestAsyncGavaConnectClient:

    async def test_lookup_pin_success(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        async with AsyncGavaConnectClient(_KEY, _SECRET) as client:
            result = await client.lookup_pin(_PIN)

        assert result["Status"] == "OK"
        assert result["PINDATA"]["StatusOfPIN"] == "Active"

    async def test_lookup_pin_not_found(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_NOT_FOUND)

        async with AsyncGavaConnectClient(_KEY, _SECRET) as client:
            with pytest.raises(GavaConnectPINNotFoundError):
                await client.lookup_pin(_PIN)

    async def test_check_tcc_success(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_tcc_url(), json=_TCC_SUCCESS)

        async with AsyncGavaConnectClient(_KEY, _SECRET) as client:
            result = await client.check_tcc(_PIN, _TCC)

        assert result["Status"] == "OK"

    async def test_token_cached_across_async_calls(self, httpx_mock) -> None:
        httpx_mock.add_response(method="GET", url=_token_url(), json=_TOKEN_RESPONSE)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)
        httpx_mock.add_response(method="POST", url=_pin_url(), json=_PIN_SUCCESS)

        async with AsyncGavaConnectClient(_KEY, _SECRET) as client:
            await client.lookup_pin(_PIN)
            await client.lookup_pin(_PIN)

        token_reqs = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert len(token_reqs) == 1

    async def test_requires_credentials(self) -> None:
        with pytest.raises(GavaConnectAuthError):
            AsyncGavaConnectClient()

    async def test_sandbox_flag(self, httpx_mock) -> None:
        httpx_mock.add_response(
            method="GET", url=_token_url(_SANDBOX_BASE), json=_TOKEN_RESPONSE
        )
        httpx_mock.add_response(
            method="POST", url=_pin_url(_SANDBOX_BASE), json=_PIN_SUCCESS
        )

        async with AsyncGavaConnectClient(_KEY, _SECRET, sandbox=True) as client:
            await client.lookup_pin(_PIN)

        reqs = httpx_mock.get_requests()
        assert all(_SANDBOX_BASE in str(r.url) for r in reqs)
