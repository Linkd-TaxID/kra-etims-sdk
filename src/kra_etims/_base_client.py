"""
_BaseKRAeTIMSClient — shared transport foundation for KRAeTIMSClient and AsyncKRAeTIMSClient.

Anthropic/OpenAI SDK pattern: a generic base class holds all configuration, error-handling,
and response-parsing logic once. Concrete sync/async subclasses supply only the transport
(_request / _authenticate) and their API surface methods.

Both transports use httpx:
  httpx.Client       — sync, thread-safe, used by KRAeTIMSClient
  httpx.AsyncClient  — async, used by AsyncKRAeTIMSClient
"""

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

from .exceptions import (
    KRA_ERROR_MAP,
    CreditNoteConflictError,
    KRAConnectivityTimeoutError,
    KRAeTIMSAuthError,
    KRAAuthorizationError,
    KRAeTIMSError,
)

# KRA eTIMS success result codes — two officially documented variants:
#   "000"  KRA VSCU/OSCU Specification v2.0 §4.14/§4.18 (JSON HTTP API)
#   "00"   KRA TIS Specification v2.0 §21.6.3 (VSCU JAR HTTP endpoint,
#          derived from the XML serial protocol but emitted over HTTP)
# KRA eTIMS success result codes — all documented variants:
#   "00"   — VSCU JAR (KRA TIS Spec v2.0 §21.6.3)
#   "000"  — OSCU HTTP API (KRA VSCU/OSCU Spec v2.0 §4.14)
#   "0"    — observed in live GavaConnect responses
#   "0000" — observed in live GavaConnect production responses
#   "001"  — empty-list response (no records match query) — NOT an error; must not raise
_KRA_SUCCESS_CODES: frozenset = frozenset({"0", "00", "000", "0000", "001"})

_DEFAULT_BASE_URL = "https://taxid-production.up.railway.app"


class _BaseKRAeTIMSClient(ABC):
    """
    Abstract base for KRAeTIMSClient (sync) and AsyncKRAeTIMSClient (async).

    Concrete fields and methods defined here (once):
      - All constructor configuration (base_url, client_id, auth fields)
      - _KRA_SUCCESS_CODES, _is_kra_success(), _handle_error_response()
      - _parse_response()     — httpx.Response → Dict, raises typed exceptions
      - _build_url()          — path → absolute URL
      - _build_auth_headers() — produces X-API-Key or Bearer header dict
      - __repr__ / __str__

    Abstract methods (implemented differently per transport):
      - _authenticate()  — sync uses threading.Lock; async uses asyncio.Lock
      - _request()       — sync returns Dict; async returns Awaitable[Dict]
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret

        env_url = (os.getenv("TAXID_API_URL") or "").strip()
        raw_url = env_url or base_url or _DEFAULT_BASE_URL
        self.base_url = raw_url.strip().rstrip("/").strip()

        # API key takes priority over OAuth2 (env var overrides constructor arg).
        self._api_key: Optional[str] = os.getenv("TAXID_API_KEY") or api_key

        # OAuth2 token state — written under subclass-specific lock.
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Representation — never echo secrets
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"client_id={self.client_id!r}, "
            f"base_url={self.base_url!r}, "
            f"auth_mode={'api_key' if self._api_key else 'oauth2'}"
            f")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    # ------------------------------------------------------------------
    # KRA result code helpers (defined once, shared by both transports)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_kra_success(result: dict) -> bool:
        """
        True when the KRA result code indicates success.

        An absent 'resultCd' key means this is a TIaaS-native response;
        HTTP errors are already handled by raise_for_status(), so absent == no KRA error.
        """
        if "resultCd" not in result:
            return True
        return str(result["resultCd"]).strip() in _KRA_SUCCESS_CODES

    def _handle_error_response(self, response_json: Any) -> None:
        """
        Map KRA application error codes (embedded in HTTP 200 bodies) to typed exceptions.

        KRA returns resultCd "00"/"000" on success. Any other code maps to a precise,
        actionable exception so callers never parse raw JSON (KRA TIS Spec v2.0 §21.6.3).
        """
        if not isinstance(response_json, dict):
            raise KRAeTIMSError(
                f"Unexpected response format: expected JSON object, "
                f"got {type(response_json).__name__}"
            )
        if self._is_kra_success(response_json):
            return

        result_cd  = str(response_json.get("resultCd", "")).strip()
        result_msg = response_json.get("resultMsg", "Unknown KRA error")

        if result_cd in KRA_ERROR_MAP:
            exc_class, default_msg = KRA_ERROR_MAP[result_cd]
            raise exc_class(f"{default_msg}: {result_msg}")

        raise KRAeTIMSError(f"KRA Error [{result_cd}]: {result_msg}")

    # ------------------------------------------------------------------
    # Request construction helpers
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _build_auth_headers(
        self, idempotency_key: Optional[str] = None
    ) -> Dict[str, str]:
        if self._api_key:
            headers: Dict[str, str] = {"X-API-Key": self._api_key}
        else:
            headers = {"Authorization": f"Bearer {self._access_token}"}
        if idempotency_key:
            headers["X-TIaaS-Idempotency-Key"] = idempotency_key
        return headers

    # ------------------------------------------------------------------
    # Response parsing — httpx.Response is the same type from both transports.
    # Called by _request() in both sync and async subclasses after transport returns.
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        resp: httpx.Response,
        method: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Parse an httpx.Response into a typed result dict, raising on any error.

        This method is synchronous and pure — it performs no I/O. Both the sync
        and async _request() implementations call it after receiving the httpx.Response.
        """
        from .exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError  # noqa: F401

        # 503 from TIaaS signals the 24-hour KRA connectivity ceiling (VSCU Spec §2.2 Policy 4).
        if resp.status_code == 503:
            raise KRAConnectivityTimeoutError()

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            if sc == 503:
                raise KRAConnectivityTimeoutError()
            if sc == 409:
                try:
                    body = exc.response.json()
                    msg  = body.get("message") or body.get("error") or exc.response.text[:200]
                except Exception:
                    msg  = exc.response.text[:200]
                raise CreditNoteConflictError(msg) from exc
            if sc == 401:
                raise KRAeTIMSAuthError(
                    "Authentication failed (HTTP 401): invalid or missing API key. "
                    "Verify TAXID_API_KEY is set and the key is active."
                ) from exc
            if sc == 403:
                raise KRAAuthorizationError(
                    "Authorization denied (HTTP 403): the credential is valid but "
                    "lacks the required role for this endpoint."
                ) from exc
            if sc == 404:
                raise KRAeTIMSError(
                    f"Resource not found (HTTP 404): {exc.response.text[:200]}"
                ) from exc
            if sc == 500:
                # 500 on a mutating method: server received the request and may have
                # committed before erroring — state is ambiguous, not safe to retry
                # without an idempotency key.
                if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
                    raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key) from exc
                # 500 on a read-only method: server-side error with no side-effect.
                raise TIaaSUnavailableError() from exc
            raise KRAeTIMSError(f"TIaaS returned HTTP {sc}") from exc

        try:
            response_data = resp.json()
        except (ValueError, httpx.DecodingError):
            raise KRAeTIMSError(
                f"Non-JSON response from TIaaS [{resp.status_code}]: {resp.text[:200]}"
            )

        self._handle_error_response(response_data)
        return response_data

    # ------------------------------------------------------------------
    # Abstract transport interface
    # ------------------------------------------------------------------

    @abstractmethod
    def _authenticate(self) -> None:
        """
        Ensure a valid auth token is available before each request.

        Sync impl:  threading.Lock + time.time() double-checked locking.
        Async impl: asyncio.Lock  + time.time() double-checked locking.
        Skipped entirely when API key auth is configured.
        """
        ...

    @abstractmethod
    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """
        Core transport dispatcher.

        Sync impl  returns:        Dict[str, Any]
        Async impl returns: Awaitable[Dict[str, Any]]

        Both implementations call _authenticate(), _build_url(),
        _build_auth_headers(), issue the httpx request, then call
        _parse_response() on the returned httpx.Response.
        """
        ...
