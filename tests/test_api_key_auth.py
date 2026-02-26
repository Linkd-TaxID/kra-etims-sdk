"""
Tests for API Key authentication mode in KRAeTIMSClient.

Verifies that:
1. When api_key is provided, X-API-Key header is sent (no Bearer token).
2. When api_key is absent, the existing OAuth2 flow is unchanged.
3. TAXID_API_KEY env var is picked up automatically.
4. No call to /oauth/token is ever made when api_key auth is active.
"""
import os
import time
import pytest
import responses
import requests
from unittest.mock import patch
from kra_etims.client import KRAeTIMSClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "https://api.test.co.ke"
HANDSHAKE_URL = f"{BASE}/v2/etims/init-handshake"
SALE_URL = f"{BASE}/v2/etims/sale"
TOKEN_URL = f"{BASE}/oauth/token"


def _client_with_api_key(key: str = "test-api-key-abc123") -> KRAeTIMSClient:
    return KRAeTIMSClient(
        client_id="unused",
        client_secret="unused",
        api_key=key,
        base_url=BASE,
    )


def _client_no_api_key() -> KRAeTIMSClient:
    """Client using the legacy OAuth2 path (dummy token injected)."""
    client = KRAeTIMSClient(client_id="test_id", client_secret="test_secret", base_url=BASE)
    client._access_token = "mock_bearer_token"
    client._token_expiry = time.time() + 3600
    return client


# ---------------------------------------------------------------------------
# Test 1: X-API-Key header is sent when api_key is configured
# ---------------------------------------------------------------------------

@responses.activate
def test_api_key_sent_in_x_api_key_header():
    """
    When api_key is provided, every request must carry X-API-Key, not Authorization.
    """
    received_headers = {}

    def capture(request):
        received_headers.update(dict(request.headers))
        return (200, {}, '{"status": "ok"}')

    responses.add_callback(
        responses.GET,
        HANDSHAKE_URL,
        callback=capture,
        content_type="application/json",
    )

    client = _client_with_api_key("my-secret-key")
    client.initialize_device_handshake()

    assert "X-API-Key" in received_headers, "X-API-Key header must be present"
    assert received_headers["X-API-Key"] == "my-secret-key"
    assert "Authorization" not in received_headers, "No Bearer token should be sent in API key mode"


# ---------------------------------------------------------------------------
# Test 2: /oauth/token is NEVER called when api_key is set
# ---------------------------------------------------------------------------

@responses.activate
def test_no_oauth_token_call_when_api_key_is_set():
    """
    The /oauth/token endpoint must never be called when api_key auth is active.
    """
    responses.add(responses.POST, TOKEN_URL, status=200, json={"access_token": "tok", "expires_in": 3600})
    responses.add(responses.GET, HANDSHAKE_URL, status=200, json={"status": "ok"})

    client = _client_with_api_key()
    client.initialize_device_handshake()

    token_calls = [c for c in responses.calls if TOKEN_URL in c.request.url]
    assert len(token_calls) == 0, "/oauth/token must not be called when api_key is configured"


# ---------------------------------------------------------------------------
# Test 3: TAXID_API_KEY env var is auto-picked-up
# ---------------------------------------------------------------------------

@responses.activate
def test_env_var_TAXID_API_KEY_is_used():
    """
    When TAXID_API_KEY is set in the environment, the client must use it
    even if api_key is not passed to the constructor.
    """
    received_headers = {}

    def capture(request):
        received_headers.update(dict(request.headers))
        return (200, {}, '{"status": "ok"}')

    responses.add_callback(
        responses.GET,
        HANDSHAKE_URL,
        callback=capture,
        content_type="application/json",
    )

    with patch.dict(os.environ, {"TAXID_API_KEY": "env-injected-key"}):
        # Construct WITHOUT passing api_key; env var should be picked up
        client = KRAeTIMSClient(client_id="x", client_secret="y", base_url=BASE)
        client.initialize_device_handshake()

    assert received_headers.get("X-API-Key") == "env-injected-key"


# ---------------------------------------------------------------------------
# Test 4: Legacy OAuth2 path still works (no api_key configured)
# ---------------------------------------------------------------------------

@responses.activate
def test_bearer_token_used_when_no_api_key():
    """
    When no api_key is configured, the existing Bearer token flow must be unchanged.
    """
    received_headers = {}

    def capture(request):
        received_headers.update(dict(request.headers))
        return (200, {}, '{"status": "ok"}')

    responses.add_callback(
        responses.GET,
        HANDSHAKE_URL,
        callback=capture,
        content_type="application/json",
    )

    client = _client_no_api_key()
    client.initialize_device_handshake()

    assert "Authorization" in received_headers
    assert received_headers["Authorization"] == "Bearer mock_bearer_token"
    assert "X-API-Key" not in received_headers


# ---------------------------------------------------------------------------
# Test 5: env var takes priority over constructor arg
# ---------------------------------------------------------------------------

@responses.activate
def test_env_var_takes_priority_over_constructor_api_key():
    """
    TAXID_API_KEY env var should override the api_key constructor argument.
    """
    received_headers = {}

    def capture(request):
        received_headers.update(dict(request.headers))
        return (200, {}, '{"status": "ok"}')

    responses.add_callback(
        responses.GET,
        HANDSHAKE_URL,
        callback=capture,
        content_type="application/json",
    )

    with patch.dict(os.environ, {"TAXID_API_KEY": "env-wins"}):
        client = _client_with_api_key("constructor-arg")
        client.initialize_device_handshake()

    assert received_headers.get("X-API-Key") == "env-wins", \
        "Env var TAXID_API_KEY must take priority over constructor api_key"
