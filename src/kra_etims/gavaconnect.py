"""
GavaConnect — KRA's public-sector API gateway (api.kra.go.ke).

Direct access to KRA's taxpayer registry and compliance APIs — no TIaaS
subscription required. Uses OAuth2 client_credentials with consumer key +
consumer secret issued by KRA's developer portal.

Authentication flow (confirmed against fourpixels-studio/KRA-APIs and
ImSidow/GavaBridge reference implementations):
  GET /v1/token/generate?grant_type=client_credentials
  Authorization: Basic base64(consumer_key:consumer_secret)
  → {"access_token": "...", "expires_in": 3600}

Token is cached and refreshed automatically. Tokens last ~1 hour; the
client refreshes 30 seconds before expiry and retries once on any
401/500 that signals expiry mid-request.

Supported operations today:
  lookup_pin(pin)              POST /checker/v1/pinbypin
  check_tcc(pin, tcc_number)   POST /v1/kra-tcc/validate

Credentials — constructor or environment variables:
  GAVACONNECT_CONSUMER_KEY
  GAVACONNECT_CONSUMER_SECRET
  GAVACONNECT_SANDBOX=true    (use sbx.kra.go.ke instead of api.kra.go.ke)

Source: KRA GavaConnect developer portal — developer.go.ke
        KRA API support — apisupport@kra.go.ke
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Endpoints (verified against open-source reference implementations)
# ---------------------------------------------------------------------------

_PRODUCTION_BASE = "https://api.kra.go.ke"
_SANDBOX_BASE    = "https://sbx.kra.go.ke"

_TOKEN_PATH    = "/v1/token/generate"
_PIN_PATH      = "/checker/v1/pinbypin"
_TCC_PATH      = "/v1/kra-tcc/validate"

# Token refresh buffer — refresh 30 s before actual expiry.
_TOKEN_BUFFER_SECS = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GavaConnectError(Exception):
    """Base for all GavaConnect errors."""


class GavaConnectAuthError(GavaConnectError):
    """Consumer key / secret rejected by KRA, or token fetch failed."""


class GavaConnectPINNotFoundError(GavaConnectError):
    """The supplied KRA PIN is not in KRA's taxpayer registry."""


class GavaConnectTCCError(GavaConnectError):
    """TCC validation failed — certificate not found or expired."""


# ---------------------------------------------------------------------------
# Shared token logic (used by both sync and async clients)
# ---------------------------------------------------------------------------

def _is_token_expiry_response(status_code: int, body: str) -> bool:
    """
    KRA signals token expiry in two ways (both observed in the wild):
      - HTTP 401 with "expired" or "invalid_token" in the response body
      - HTTP 500 with 'fault.faultstring = "Access Token expired"'
    """
    if status_code not in (401, 500):
        return False
    lowered = body.lower()
    return "expired" in lowered or "invalid_token" in lowered


def _parse_token(data: dict[str, Any]) -> tuple[str, float]:
    """Return (access_token, expiry_monotonic)."""
    token = data.get("access_token", "")
    if not token:
        raise GavaConnectAuthError(
            "GavaConnect token endpoint returned no access_token. "
            "Verify your consumer key and secret at developer.go.ke."
        )
    expires_in = int(data.get("expires_in", 3600))
    expiry = time.monotonic() + expires_in - _TOKEN_BUFFER_SECS
    return token, expiry


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class GavaConnectClient:
    """
    Synchronous GavaConnect client. Thread-safe — one instance can serve
    multiple threads (token refresh is Lock-protected).

    Usage::

        client = GavaConnectClient(consumer_key="...", consumer_secret="...")
        result = client.lookup_pin("A000123456B")
        print(result["PINDATA"]["Name"])

    Or via environment variables::

        GAVACONNECT_CONSUMER_KEY=...
        GAVACONNECT_CONSUMER_SECRET=...
        client = GavaConnectClient.from_env()
    """

    def __init__(
        self,
        consumer_key: Optional[str] = None,
        consumer_secret: Optional[str] = None,
        *,
        sandbox: bool = False,
    ) -> None:
        key    = consumer_key    or os.getenv("GAVACONNECT_CONSUMER_KEY",    "")
        secret = consumer_secret or os.getenv("GAVACONNECT_CONSUMER_SECRET", "")
        if not key or not secret:
            raise GavaConnectAuthError(
                "GavaConnect requires a consumer key and consumer secret. "
                "Pass them as arguments or set GAVACONNECT_CONSUMER_KEY / "
                "GAVACONNECT_CONSUMER_SECRET environment variables. "
                "Register at: https://developer.go.ke"
            )
        self._key    = key
        self._secret = secret
        self._base   = _SANDBOX_BASE if (
            sandbox or os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
        ) else _PRODUCTION_BASE

        self._http             = httpx.Client(timeout=30.0)
        self._lock             = threading.Lock()
        self._token: str       = ""
        self._token_expiry: float = 0.0

    @classmethod
    def from_env(cls) -> "GavaConnectClient":
        sandbox = os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
        return cls(sandbox=sandbox)

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _fetch_token(self) -> None:
        # Token fetch is GET with HTTP Basic Auth — NOT a POST form body.
        # grant_type is a query parameter per the GavaConnect spec.
        resp = self._http.get(
            f"{self._base}{_TOKEN_PATH}",
            params={"grant_type": "client_credentials"},
            auth=(self._key, self._secret),
        )
        if resp.status_code == 401:
            raise GavaConnectAuthError(
                "GavaConnect rejected your consumer key / secret (HTTP 401). "
                "Verify your credentials at developer.go.ke."
            )
        resp.raise_for_status()
        self._token, self._token_expiry = _parse_token(resp.json())

    def _get_token(self) -> str:
        with self._lock:
            if not self._token or time.monotonic() >= self._token_expiry:
                self._fetch_token()
            return self._token

    def _invalidate_token(self) -> None:
        with self._lock:
            self._token_expiry = 0.0

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            token = self._get_token()
            resp = self._http.post(
                f"{self._base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if _is_token_expiry_response(resp.status_code, resp.text):
                self._invalidate_token()
                if attempt == 0:
                    continue
                raise GavaConnectAuthError("GavaConnect token expired and refresh failed.")
            resp.raise_for_status()
            return resp.json()
        raise GavaConnectAuthError("GavaConnect request failed after token refresh.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup_pin(self, pin: str) -> dict[str, Any]:
        """
        Validate a KRA PIN against the taxpayer registry.

        Returns the raw GavaConnect response on success::

            {
                "ResponseCode": "23000",
                "Status": "OK",
                "PINDATA": {
                    "KRAPIN": "A***7Q",          # KRA masks the PIN
                    "TypeOfTaxpayer": "Individual",
                    "Name": "J**h M**ua M**ga",  # KRA masks the name
                    "StatusOfPIN": "Active"
                }
            }

        Raises GavaConnectPINNotFoundError if the PIN is not in KRA's registry.
        """
        # Request field is uppercase KRAPIN — this is GavaConnect's field name.
        data = self._post(_PIN_PATH, {"KRAPIN": pin})
        if "ErrorMessage" in data:
            raise GavaConnectPINNotFoundError(
                f"PIN {pin!r} not found in KRA's taxpayer registry: {data['ErrorMessage']}"
            )
        return data

    def check_tcc(self, pin: str, tcc_number: str) -> dict[str, Any]:
        """
        Validate a Tax Compliance Certificate (TCC) against KRA.

        Returns the raw GavaConnect response on success::

            {
                "Status": "OK",
                "TCCData": {"KRAPIN": "P051411025H"}
            }

        Raises GavaConnectTCCError if the TCC is invalid or expired.
        Note: request field is lowercase-camel kraPIN — GavaConnect's naming.
        """
        data = self._post(_TCC_PATH, {"kraPIN": pin, "tccNumber": tcc_number})
        if data.get("Status", "").upper() != "OK":
            raise GavaConnectTCCError(
                f"TCC {tcc_number!r} for PIN {pin!r} is invalid or expired. "
                f"Response: {data}"
            )
        return data

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GavaConnectClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class AsyncGavaConnectClient:
    """
    Async GavaConnect client. Use with FastAPI, async workers, or asyncio scripts.

    Usage::

        async with AsyncGavaConnectClient(consumer_key="...", consumer_secret="...") as client:
            result = await client.lookup_pin("A000123456B")
    """

    def __init__(
        self,
        consumer_key: Optional[str] = None,
        consumer_secret: Optional[str] = None,
        *,
        sandbox: bool = False,
    ) -> None:
        key    = consumer_key    or os.getenv("GAVACONNECT_CONSUMER_KEY",    "")
        secret = consumer_secret or os.getenv("GAVACONNECT_CONSUMER_SECRET", "")
        if not key or not secret:
            raise GavaConnectAuthError(
                "GavaConnect requires a consumer key and consumer secret. "
                "Pass them as arguments or set GAVACONNECT_CONSUMER_KEY / "
                "GAVACONNECT_CONSUMER_SECRET environment variables."
            )
        self._key    = key
        self._secret = secret
        self._base   = _SANDBOX_BASE if (
            sandbox or os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
        ) else _PRODUCTION_BASE

        self._http             = httpx.AsyncClient(timeout=30.0)
        self._lock             = asyncio.Lock()
        self._token: str       = ""
        self._token_expiry: float = 0.0

    @classmethod
    def from_env(cls) -> "AsyncGavaConnectClient":
        sandbox = os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
        return cls(sandbox=sandbox)

    async def _fetch_token(self) -> None:
        resp = await self._http.get(
            f"{self._base}{_TOKEN_PATH}",
            params={"grant_type": "client_credentials"},
            auth=(self._key, self._secret),
        )
        if resp.status_code == 401:
            raise GavaConnectAuthError(
                "GavaConnect rejected your consumer key / secret (HTTP 401)."
            )
        resp.raise_for_status()
        self._token, self._token_expiry = _parse_token(resp.json())

    async def _get_token(self) -> str:
        async with self._lock:
            if not self._token or time.monotonic() >= self._token_expiry:
                await self._fetch_token()
            return self._token

    async def _invalidate_token(self) -> None:
        async with self._lock:
            self._token_expiry = 0.0

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._get_token()
            resp = await self._http.post(
                f"{self._base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if _is_token_expiry_response(resp.status_code, resp.text):
                await self._invalidate_token()
                if attempt == 0:
                    continue
                raise GavaConnectAuthError("GavaConnect token expired and refresh failed.")
            resp.raise_for_status()
            return resp.json()
        raise GavaConnectAuthError("GavaConnect request failed after token refresh.")

    async def lookup_pin(self, pin: str) -> dict[str, Any]:
        """Async equivalent of GavaConnectClient.lookup_pin."""
        data = await self._post(_PIN_PATH, {"KRAPIN": pin})
        if "ErrorMessage" in data:
            raise GavaConnectPINNotFoundError(
                f"PIN {pin!r} not found in KRA's taxpayer registry: {data['ErrorMessage']}"
            )
        return data

    async def check_tcc(self, pin: str, tcc_number: str) -> dict[str, Any]:
        """Async equivalent of GavaConnectClient.check_tcc."""
        data = await self._post(_TCC_PATH, {"kraPIN": pin, "tccNumber": tcc_number})
        if data.get("Status", "").upper() != "OK":
            raise GavaConnectTCCError(
                f"TCC {tcc_number!r} for PIN {pin!r} is invalid or expired."
            )
        return data

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncGavaConnectClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
