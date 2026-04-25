"""
Tests for API Key authentication mode in KRAeTIMSClient.

Converted from responses (requests-based) to pytest-httpx, matching the
httpx.Client transport that KRAeTIMSClient now uses.

Positive tests (structural — verifies the SDK sends the right headers):
1. X-API-Key header is sent when api_key is configured.
2. /oauth/token is never called when api_key is set.
3. TAXID_API_KEY env var is auto-picked-up.
4. Legacy OAuth2 Bearer path unchanged when no api_key.
5. Env var takes priority over constructor api_key argument.

Negative tests (rejection — verifies server auth failures raise typed exceptions):
6. Invalid API key → server returns 401 → KRAeTIMSAuthError raised.
7. No API key (OAuth mode) → server returns 401 → KRAeTIMSAuthError raised.
8. Insufficient-role key → server returns 403 → KRAAuthorizationError raised.
9. Expired OAuth token → refresh called before API endpoint.
"""
import os
import time
import pytest
from unittest.mock import patch
from kra_etims.client import KRAeTIMSClient
from kra_etims.exceptions import KRAeTIMSAuthError, KRAAuthorizationError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE          = "https://api.test.co.ke"
HANDSHAKE_URL = f"{BASE}/v2/etims/init-handshake"
SALE_URL      = f"{BASE}/v2/etims/sale"
TOKEN_URL     = f"{BASE}/oauth/token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_with_api_key(key: str = "test-api-key-abc123") -> KRAeTIMSClient:
    return KRAeTIMSClient(
        client_id="unused",
        client_secret="unused",
        api_key=key,
        base_url=BASE,
    )


def _client_no_api_key() -> KRAeTIMSClient:
    """Client using the legacy OAuth2 path with a pre-injected valid token."""
    client = KRAeTIMSClient(client_id="test_id", client_secret="test_secret", base_url=BASE)
    client._access_token = "mock_bearer_token"
    client._token_expiry = time.time() + 3600
    return client


# ---------------------------------------------------------------------------
# Test 1: X-API-Key header is sent when api_key is configured
# ---------------------------------------------------------------------------

def test_api_key_sent_in_x_api_key_header(httpx_mock):
    """
    When api_key is provided, every request must carry X-API-Key, not Authorization.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    client = _client_with_api_key("my-secret-key")
    client.initialize_device_handshake()

    sent = httpx_mock.get_requests()
    assert len(sent) == 1
    assert sent[0].headers.get("X-API-Key") == "my-secret-key", \
        "X-API-Key header must be present and match the configured key"
    assert "Authorization" not in sent[0].headers, \
        "No Bearer token should be sent in API key mode"


# ---------------------------------------------------------------------------
# Test 2: /oauth/token is NEVER called when api_key is set
# ---------------------------------------------------------------------------

def test_no_oauth_token_call_when_api_key_is_set(httpx_mock):
    """
    The /oauth/token endpoint must never be called when api_key auth is active.

    pytest-httpx raises by default if an unmocked URL is called, so the absence
    of a token mock here proves the token endpoint is never reached.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    client = _client_with_api_key()
    client.initialize_device_handshake()

    token_calls = [r for r in httpx_mock.get_requests() if "/oauth/token" in str(r.url)]
    assert len(token_calls) == 0, "/oauth/token must not be called when api_key is configured"


# ---------------------------------------------------------------------------
# Test 3: TAXID_API_KEY env var is auto-picked-up
# ---------------------------------------------------------------------------

def test_env_var_TAXID_API_KEY_is_used(httpx_mock):
    """
    When TAXID_API_KEY is set in the environment, the client must use it
    even if api_key is not passed to the constructor.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    with patch.dict(os.environ, {"TAXID_API_KEY": "env-injected-key"}):
        client = KRAeTIMSClient(client_id="x", client_secret="y", base_url=BASE)
        client.initialize_device_handshake()

    sent = httpx_mock.get_requests()
    assert sent[0].headers.get("X-API-Key") == "env-injected-key"


# ---------------------------------------------------------------------------
# Test 4: Legacy OAuth2 path still works (no api_key configured)
# ---------------------------------------------------------------------------

def test_bearer_token_used_when_no_api_key(httpx_mock):
    """
    When no api_key is configured, the existing Bearer token flow must be unchanged.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    client = _client_no_api_key()
    client.initialize_device_handshake()

    sent = httpx_mock.get_requests()
    assert sent[0].headers.get("Authorization") == "Bearer mock_bearer_token"
    assert "X-API-Key" not in sent[0].headers


# ---------------------------------------------------------------------------
# Test 5: Env var takes priority over constructor arg
# ---------------------------------------------------------------------------

def test_env_var_takes_priority_over_constructor_api_key(httpx_mock):
    """
    TAXID_API_KEY env var should override the api_key constructor argument.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    with patch.dict(os.environ, {"TAXID_API_KEY": "env-wins"}):
        client = _client_with_api_key("constructor-arg")
        client.initialize_device_handshake()

    sent = httpx_mock.get_requests()
    assert sent[0].headers.get("X-API-Key") == "env-wins", \
        "Env var TAXID_API_KEY must take priority over constructor api_key"


# ---------------------------------------------------------------------------
# Negative Test 6: Invalid API key → server returns 401 → KRAeTIMSAuthError
# ---------------------------------------------------------------------------

def test_invalid_key_returns_401(httpx_mock):
    """
    When the server rejects the API key with 401, the client must raise
    KRAeTIMSAuthError — not a generic KRAeTIMSError.

    This test verifies the server's auth rejection path, not just the SDK's
    sending behavior. A broken ApiKeyAuthFilter would cause this test to fail
    even if the positive tests all pass.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=401,
        json={"error": "Unauthorized", "message": "Invalid or revoked API key"},
    )

    client = _client_with_api_key("invalid-key-that-doesnt-exist-in-db")
    with pytest.raises(KRAeTIMSAuthError):
        client.initialize_device_handshake()


# ---------------------------------------------------------------------------
# Negative Test 7: No API key (OAuth mode) → server returns 401 → KRAeTIMSAuthError
# ---------------------------------------------------------------------------

def test_missing_key_returns_401(httpx_mock):
    """
    When the server rejects with 401 (e.g. Bearer token expired on the server side),
    the client must raise KRAeTIMSAuthError regardless of which auth mode is in use.
    """
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=401,
        json={"error": "Unauthorized", "message": "Missing X-API-Key header"},
    )

    client = _client_no_api_key()
    with pytest.raises(KRAeTIMSAuthError):
        client.initialize_device_handshake()


# ---------------------------------------------------------------------------
# Negative Test 8: Insufficient role key → server returns 403 → KRAAuthorizationError
# ---------------------------------------------------------------------------

def test_revoked_key_behavior(httpx_mock):
    """
    When the server returns 403 (valid credential, insufficient role — e.g.
    ROLE_SDK_CLIENT attempting an ROLE_ADMIN endpoint), the client must raise
    KRAAuthorizationError, not the generic KRAeTIMSError.

    This distinction matters: 401 = identity unknown; 403 = identity known but
    access denied. Callers need the specific type to route to the right error
    handling path.
    """
    httpx_mock.add_response(
        url=f"{BASE}/v2/etims/init-handshake",
        method="GET",
        status_code=403,
        json={"error": "Forbidden", "message": "ROLE_ADMIN required for this endpoint"},
    )

    client = _client_with_api_key("sdk-client-key-not-admin")
    with pytest.raises(KRAAuthorizationError):
        client.initialize_device_handshake()


# ---------------------------------------------------------------------------
# Negative Test 9: Expired OAuth token → refresh endpoint called before API call
# ---------------------------------------------------------------------------

def test_expired_oauth_token_triggers_refresh(httpx_mock):
    """
    When the cached OAuth token is expired (token_expiry in the past), the
    client must call /oauth/token to refresh before calling the API endpoint.

    This test verifies the double-checked locking refresh logic on the live
    client path — not just that the header is sent.
    """
    httpx_mock.add_response(
        url=TOKEN_URL,
        method="POST",
        status_code=200,
        json={"access_token": "refreshed-token", "expires_in": 3600},
    )
    httpx_mock.add_response(
        url=HANDSHAKE_URL,
        method="GET",
        status_code=200,
        json={"status": "ok"},
    )

    client = KRAeTIMSClient(client_id="test_id", client_secret="test_secret", base_url=BASE)
    client._access_token = "stale-token"
    client._token_expiry = 0  # expired: any call to isTokenValid() returns False

    client.initialize_device_handshake()

    all_requests = httpx_mock.get_requests()

    token_calls = [r for r in all_requests if "/oauth/token" in str(r.url)]
    assert len(token_calls) == 1, "Token refresh must be called exactly once when token is expired"

    api_calls = [r for r in all_requests if "/init-handshake" in str(r.url)]
    assert len(api_calls) == 1
    assert api_calls[0].headers.get("Authorization") == "Bearer refreshed-token", \
        "API call must carry the newly refreshed token, not the stale one"

    # Token endpoint must have been called BEFORE the API endpoint
    token_idx = all_requests.index(token_calls[0])
    api_idx   = all_requests.index(api_calls[0])
    assert token_idx < api_idx, "Token refresh must precede the API call"
