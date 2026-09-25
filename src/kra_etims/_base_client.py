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
    KRADuplicateInvoiceError,
    CreditNoteConflictError,
    CreditNoteExceedsOriginalError,
    KRAConflictError,
    KRAConnectivityTimeoutError,
    KRAeTIMSAuthError,
    KRAAuthorizationError,
    KRAeTIMSError,
    OSCUUnavailableError,
    TIaaSAmbiguousStateError,
    TIaaSUnavailableError,
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

_DEFAULT_BASE_URL = "https://api.taxid.co.ke"

_MUTATING_METHODS: frozenset = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# Sale body states where TaxID holds a receipt that is not (yet) signed.
_UNSETTLED_SALE_STATES: frozenset = frozenset(
    {"PENDING_SYNC", "OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED"}
)

# Exceptions after which re-submitting with the SAME idempotency key is safe.
_RETRYABLE_ERRORS = (
    TIaaSUnavailableError,
    TIaaSAmbiguousStateError,
    OSCUUnavailableError,
    KRAConnectivityTimeoutError,
)

# Failures where the request provably never reached the server: no TCP/TLS
# connection was established, or no pooled connection became free in time.
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class _BaseKRAeTIMSClient(ABC):
    """
    Abstract base for KRAeTIMSClient (sync) and AsyncKRAeTIMSClient (async).

    Concrete fields and methods defined here (once):
      - All constructor configuration (base_url, client_id, auth fields)
      - _KRA_SUCCESS_CODES, _is_kra_success(), _handle_error_response()
      - _parse_response()     — httpx.Response → Dict, raises typed exceptions
      - _build_url()          — path → absolute URL
      - _build_auth_headers() — produces standard Bearer and Idempotency-Key headers
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

        # Opaque TaxID credential takes priority over OAuth2 token acquisition
        # (env var overrides constructor arg). Both use the standard Bearer
        # transport; the server continues accepting X-API-Key for older SDKs.
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
            headers: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        else:
            headers = {"Authorization": f"Bearer {self._access_token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    # ------------------------------------------------------------------
    # Response parsing — httpx.Response is the same type from both transports.
    # Called by _request() in both sync and async subclasses after transport returns.
    # ------------------------------------------------------------------

    @staticmethod
    def _error_fields(resp: httpx.Response) -> tuple[dict, str, Optional[str]]:
        """Read both TaxID v2 flat errors and RFC 9457 Problem Details.

        Returns the original body, a human-readable message, and the stable
        machine code when one is present. Unknown/non-JSON responses remain
        usable through their bounded response text.
        """
        try:
            parsed = resp.json()
            body = parsed if isinstance(parsed, dict) else {}
        except Exception:
            body = {}
        message = (
            body.get("message")
            or body.get("detail")
            or body.get("error")
            or body.get("title")
            or resp.text[:200]
            or f"HTTP {resp.status_code}"
        )
        code = body.get("code") or body.get("errorCode")
        return body, str(message), str(code) if code is not None else None

    @staticmethod
    def _raise_for_503(resp: httpx.Response) -> None:
        """
        Discriminates a 503 between two unrelated conditions that happen to
        share an HTTP status code: the 24-hour VSCU offline ceiling (VSCU
        Spec §2.2 Policy 4) and a transient OSCU-side failure (OSCU Spec
        v2.0 §4.18 — OSCU has zero offline tolerance by design, so it has
        no 24-hour-ceiling concept at all). Every 503 was treated as the
        former until this method existed; that stopped being safe once the
        middleware could actually sign through OSCU.

        TIaaS's ``GlobalExceptionHandler`` puts an ``oscu_code`` property on
        the response body specifically for OSCU failures (``vscu_code`` for
        VSCU ones) — presence of ``oscu_code`` is the discriminator. Falls
        back to the historical ``KRAConnectivityTimeoutError`` behavior if
        the body doesn't parse or doesn't carry either marker, so an older
        middleware version (or a 503 from something other than a signing
        call) doesn't change behavior.
        """
        body, message, _ = _BaseKRAeTIMSClient._error_fields(resp)
        if isinstance(body, dict) and "oscu_code" in body:
            raise OSCUUnavailableError(
                message=message or (
                    f"OSCU Temporarily Unavailable (code {body.get('oscu_code')}): "
                    f"{body.get('title', 'no further detail')}"
                ),
                oscu_code=body.get("oscu_code"),
            )
        raise KRAConnectivityTimeoutError()

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
        # 503 from TIaaS signals either the 24-hour VSCU offline ceiling or a
        # transient OSCU failure — see _raise_for_503's docstring.
        if resp.status_code == 503:
            self._raise_for_503(resp)

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            if sc == 503:
                self._raise_for_503(exc.response)
            if sc == 409:
                body, msg, code = self._error_fields(exc.response)
                message = f"Conflict (HTTP 409){f' [{code}]' if code else ''}: {msg}"
                if "/credit-note" in exc.request.url.path:
                    raise CreditNoteConflictError(
                        message,
                        existing_credit_note_id=body.get("existingCreditNoteId"),
                        existing_cu_invoice_no=body.get("existingCuInvoiceNo"),
                        code=code,
                    ) from exc
                raise KRAConflictError(message, code=code) from exc
            if sc == 425:
                # IDEMPOTENT_REQUEST_IN_FLIGHT: the first request with this key is
                # still running. Its outcome is not known yet, and the fix is the
                # same as for any ambiguous mutation — retry with the same key.
                _, msg, _ = self._error_fields(exc.response)
                raise TIaaSAmbiguousStateError(
                    f"Request with this idempotency key is still in flight (HTTP 425): {msg} "
                    "Retry shortly with the same idempotency key.",
                    idempotency_key=idempotency_key,
                ) from exc
            if sc == 422:
                # 422 covers VSCU terminal rejections AND the credit-note
                # over-reversal guard (middleware V15). Discriminate by body code.
                body, msg, code = self._error_fields(exc.response)
                if code == "CREDIT_NOTE_EXCEEDS_ORIGINAL":
                    raise CreditNoteExceedsOriginalError(
                        msg,
                        remaining=body.get("remaining"),
                        already_reversed=body.get("alreadyReversed"),
                    ) from exc
                # Other 422s fall through to the generic handler below.
            if sc == 401:
                _, message, code = self._error_fields(exc.response)
                detail = f" [{code}]" if code else ""
                raise KRAeTIMSAuthError(
                    f"Authentication failed (HTTP 401){detail}: {message}. "
                    "Verify TAXID_API_KEY is set and the credential is active."
                ) from exc
            if sc == 403:
                _, message, code = self._error_fields(exc.response)
                detail = f" [{code}]" if code else ""
                raise KRAAuthorizationError(
                    f"Authorization denied (HTTP 403){detail}: {message}"
                ) from exc
            if sc == 404:
                _, message, code = self._error_fields(exc.response)
                detail = f" [{code}]" if code else ""
                raise KRAeTIMSError(
                    f"Resource not found (HTTP 404){detail}: {message}"
                ) from exc
            if sc in (502, 504):
                # Gateway failures from an edge proxy: the upstream may have
                # committed before the proxy gave up.
                if method.upper() in _MUTATING_METHODS:
                    raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key) from exc
                raise TIaaSUnavailableError() from exc
            if sc == 500:
                # 500 on a mutating method: server received the request and may have
                # committed before erroring — state is ambiguous, not safe to retry
                # without an idempotency key.
                if method.upper() in _MUTATING_METHODS:
                    raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key) from exc
                # 500 on a read-only method: server-side error with no side-effect.
                raise TIaaSUnavailableError() from exc
            _, message, code = self._error_fields(exc.response)
            suffix = f" [{code}]" if code else ""
            raise KRAeTIMSError(f"TIaaS returned HTTP {sc}{suffix}: {message}") from exc

        try:
            response_data = resp.json()
        except (ValueError, httpx.DecodingError):
            raise KRAeTIMSError(
                f"Non-JSON response from TIaaS [{resp.status_code}]: {resp.text[:200]}"
            )

        self._handle_error_response(response_data)
        return response_data

    @staticmethod
    def _flush_outcome(invoice_no: str, idem_key: str, outcome: Any) -> Dict[str, Any]:
        """
        One flush_offline_queue result row.

        status keeps its historical values (success / already_processed /
        error); the added keys let callers tell a Schrodinger receipt from a
        rejection instead of parsing message. idempotency_key must be
        reused verbatim on any resubmission.
        """
        if isinstance(outcome, KRADuplicateInvoiceError):
            return {"invoice_no": invoice_no, "status": "already_processed",
                    "idempotency_key": idem_key}
        if isinstance(outcome, BaseException):
            return {
                "invoice_no":      invoice_no,
                "status":          "error",
                "message":         str(outcome),
                "error_type":      type(outcome).__name__,
                "ambiguous":       isinstance(outcome, TIaaSAmbiguousStateError),
                "retryable":       isinstance(outcome, _RETRYABLE_ERRORS),
                "idempotency_key": idem_key,
                "exception":       outcome,
            }
        sale_state = outcome.get("status") if isinstance(outcome, dict) else None
        return {
            "invoice_no":      invoice_no,
            "status":          "success",
            "data":            outcome,
            "signed":          sale_state not in _UNSETTLED_SALE_STATES,
            "sale_status":     sale_state,
            "idempotency_key": idem_key,
        }

    @staticmethod
    def _transport_error(
        exc: httpx.RequestError, method: str, idempotency_key: Optional[str]
    ) -> KRAeTIMSError:
        """
        Map an httpx transport failure to the SDK's retry-safety taxonomy.

        Only connect/pool failures prove the request never left the client.
        Anything later (read/write timeouts, resets, "server disconnected
        without sending a response") may follow a committed mutation, so a
        mutating call is ambiguous and must be reconciled with the same
        idempotency key, never re-issued under a new one.
        """
        if isinstance(exc, _NOT_SENT_ERRORS):
            return TIaaSUnavailableError()
        if method.upper() in _MUTATING_METHODS:
            return TIaaSAmbiguousStateError(idempotency_key=idempotency_key)
        return TIaaSUnavailableError()

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
