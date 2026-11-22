"""
_BaseKRAeTIMSClient — shared transport foundation for KRAeTIMSClient and AsyncKRAeTIMSClient.

Anthropic/OpenAI SDK pattern: a generic base class holds all configuration, error-handling,
and response-parsing logic once. Concrete sync/async subclasses supply only the transport
(_request / _authenticate) and their API surface methods.

Both transports use httpx:
  httpx.Client       — sync, thread-safe, used by KRAeTIMSClient
  httpx.AsyncClient  — async, used by AsyncKRAeTIMSClient
"""

import json as _json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx
from pydantic import ValidationError
from pydantic import BaseModel
from .outcomes import (
    CreditNoteResult, FiscalResultDict, has_signed_evidence, ResponseMetadata, SaleOriginalResult, SaleResult, SaleStatusResult,
)
from .models import StockMasterResult, StockOperationResult

from .exceptions import (
    KRA_ERROR_MAP,
    KRADuplicateInvoiceError,
    CreditNoteConflictError,
    CreditNoteExceedsOriginalError,
    CreditNoteOutcomeUnresolvedError,
    IdempotentOutcomeUnresolvedError,
    KRAConflictError,
    KRAConnectivityTimeoutError,
    KRAeTIMSAuthError,
    KRAAuthorizationError,
    KRAeTIMSError,
    MutationServiceUnavailableError,
    OSCUUnavailableError,
    RateLimitError,
    TIaaSAmbiguousStateError,
    TIaaSUnavailableError,
    UnsupportedOperationError,
    WorkerDeferredError,
    WorkerStoppingError,
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

# IdempotencyFilter refusals where an earlier claim for the key exists but its
# outcome cannot be returned: the original may have been applied.
_UNRESOLVED_IDEMPOTENCY_CODES: frozenset = frozenset(
    {"IDEMPOTENT_RESULT_LOOKUP_REQUIRED", "IDEMPOTENT_IDENTITY_UNAVAILABLE", "IDEMPOTENCY_SCOPE_UNBOUND"}
)

# Routes whose 2xx bodies are fiscal outcomes: validated before they are returned
# (an invalid mutation body is ambiguous, never a raw pydantic error) and returned
# with response metadata attached.
_RESULT_MODELS: tuple = (
    (re.compile(r"/v2/etims/sale$"), SaleResult),
    (re.compile(r"/v2/etims/sales/[^/]+/status$"), SaleStatusResult),
    (re.compile(r"/v2/etims/sales/search$"), SaleStatusResult),
    (re.compile(r"/v2/etims/sales/[^/]+/original$"), SaleOriginalResult),
    (re.compile(r"/v2/etims/sale/[^/]+/credit-note$"), CreditNoteResult),
    (re.compile(r"/v2/etims/stock/adjustment$"), StockOperationResult),
    (re.compile(r"/v2/etims/stock/operations/[^/]+$"), StockOperationResult),
    (re.compile(r"/v2/etims/stock/master$"), StockMasterResult),
    (re.compile(r"/v2/etims/stock/master/operations/[^/]+$"), StockMasterResult),
)

_CREDIT_NOTE_PATH = re.compile(r"/v2/etims/sale/[^/]+/credit-note$")


def _unresolved_error_type(path: str) -> type[IdempotentOutcomeUnresolvedError]:
    """Credit notes get the subclass that is also a CreditNoteConflictError."""
    return CreditNoteOutcomeUnresolvedError if _CREDIT_NOTE_PATH.search(path) else IdempotentOutcomeUnresolvedError


# An UPPER_SNAKE machine code, as opposed to a reason phrase such as "Conflict".
_MACHINE_CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")

# Failures where the request provably never reached the server: no TCP/TLS
# connection was established, or no pooled connection became free in time.
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _encode_json_body(payload: Any) -> bytes:
    """Serialize a request body the same way under every httpx version.

    TaxID's IdempotencyFilter hashes the exact body bytes with the route and
    rejects a reused key whose bytes differ (422 IDEMPOTENCY_KEY_REUSED).
    httpx 0.27 sent ``json.dumps(obj)`` (spaced, ASCII-escaped) and 0.28 sends
    compact UTF-8, so the same retry could hash differently depending on the
    installed httpx. This matches the 0.28 format exactly, keeping keys first
    sent from 0.28 installs replayable. Keys are not sorted: the server
    digests bytes, not a canonical form, and sorting would change the bytes
    of every request an earlier SDK version already sent under a key.
    """
    return _json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


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
        client_id: str = "",
        client_secret: str = "",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret

        env_url = (os.getenv("TAXID_API_URL") or "").strip()
        raw_url = base_url if base_url is not None else (env_url or _DEFAULT_BASE_URL)
        self.base_url = raw_url.strip().rstrip("/").strip()

        # Opaque TaxID credential takes priority over OAuth2 token acquisition
        # (explicit values override environment defaults). Both use the standard Bearer
        # transport; the server continues accepting X-API-Key for older SDKs.
        self._api_key: Optional[str] = api_key if api_key is not None else os.getenv("TAXID_API_KEY")

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
            error = exc_class(f"{default_msg}: {result_msg}")
            error.result_cd = result_cd
            raise error

        error = KRAeTIMSError(f"KRA Error [{result_cd}]: {result_msg}")
        error.result_cd = result_cd
        raise error

    # ------------------------------------------------------------------
    # Request construction helpers
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _encode_request_body(json: Any, headers: Dict[str, str]) -> Optional[bytes]:
        """SDK-owned JSON body bytes (see _encode_json_body); sets Content-Type."""
        if json is None:
            return None
        headers["Content-Type"] = "application/json"
        return _encode_json_body(json)

    @staticmethod
    def _unsupported_update_stock() -> None:
        import warnings
        warnings.warn(
            "update_stock() targets the legacy /v2/etims/stock route, which TaxID refuses "
            "(HTTP 501), and will be removed. Use submit_stock_adjustment(..., idempotency_key=...).",
            DeprecationWarning,
            stacklevel=3,
        )
        raise UnsupportedOperationError(
            "update_stock() is not supported: TaxID refuses the legacy /v2/etims/stock route "
            "because it has no durable identity. Use submit_stock_adjustment(lines, "
            "idempotency_key=...) with a stable key."
        )

    @staticmethod
    def _unsupported_compliance() -> None:
        import warnings
        warnings.warn(
            "check_compliance() targets a route TaxID does not serve and will be removed. "
            "Use device_health() or lookup_customer().",
            DeprecationWarning,
            stacklevel=3,
        )
        raise UnsupportedOperationError(
            "check_compliance() is not supported: TaxID has no compliance-check route. "
            "Use device_health() for branch reachability or lookup_customer(pin) for a taxpayer PIN."
        )

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
    def _raise_for_503(resp: httpx.Response, mutating: bool = False) -> None:
        """
        Discriminate a TaxID 503. Several unrelated conditions share the status:

        - ``code: KRA_CONNECTIVITY_TIMEOUT`` — the 24-hour VSCU offline ceiling
          (``SystemSuspendedException``): :class:`KRAConnectivityTimeoutError`.
        - ``oscu_code`` property — transient OSCU failure (OSCU Spec v2.0 §4.18
          has no offline ceiling): :class:`OSCUUnavailableError`.
        - ``code: WORKER_STOPPING`` — worker shutdown, refused before dispatch:
          :class:`WorkerStoppingError` (retryable).
        - anything else (transient VSCU ``vscu_code``, worker role not fiscal,
          held stock dispatch, token outage, a proxy, an empty body):
          :class:`TIaaSUnavailableError`. These are not the 24-hour ceiling, so
          they no longer default to it. On a mutation the original may still
          have been applied, so it is :class:`MutationServiceUnavailableError`
          (also a :class:`TIaaSAmbiguousStateError`).
        """
        body, message, code = _BaseKRAeTIMSClient._error_fields(resp)
        if "oscu_code" in body:
            raise OSCUUnavailableError(
                message=message or (
                    f"OSCU Temporarily Unavailable (code {body.get('oscu_code')}): "
                    f"{body.get('title', 'no further detail')}"
                ),
                oscu_code=body.get("oscu_code"),
            )
        if code == "KRA_CONNECTIVITY_TIMEOUT":
            error: KRAeTIMSError = KRAConnectivityTimeoutError()
        elif code == "WORKER_STOPPING":
            error = WorkerStoppingError(f"TaxID fiscal worker is stopping (HTTP 503): {message}", code=code)
        else:
            detail = f" [{code}]" if code else ""
            text = f"TaxID service unavailable (HTTP 503){detail}: {message}"
            if mutating:
                error = MutationServiceUnavailableError(
                    f"{text} The original may have been applied; inspect it before any replay.")
            else:
                error = TIaaSUnavailableError(text)
            if body.get("vscu_code") is not None:
                error.vscu_code = str(body["vscu_code"])
        error.code = code
        raise error

    def _parse_response(
        self,
        resp: httpx.Response,
        method: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        body, _, body_code = self._error_fields(resp)
        metadata = ResponseMetadata(
            http_status=resp.status_code,
            request_id=resp.headers.get("X-Request-Id") or body.get("requestId"),
            environment=resp.headers.get("X-TaxID-Environment") or body.get("environment"),
            retry_after=resp.headers.get("Retry-After"),
        )
        path = resp.request.url.path
        result_model: Optional[type[BaseModel]] = next(
            (model for pattern, model in _RESULT_MODELS if pattern.search(path)), None)
        try:
            result = self._parse_response_body(resp, method, idempotency_key)
            if result_model is not None:
                self._validate_sale_result(result, method, idempotency_key, result_model)
                return FiscalResultDict(result, metadata)
            return result
        except KRAeTIMSError as error:
            if (result_model is not None and resp.is_success and method.upper() in _MUTATING_METHODS
                    and not isinstance(error, TIaaSAmbiguousStateError)
                    and getattr(error, "result_cd", None) is None):
                error = TIaaSAmbiguousStateError(
                    "Unusable fiscal mutation response; inspect the original operation before replay.",
                    idempotency_key=idempotency_key,
                )
            error.response_metadata = metadata
            error.request_id = metadata.request_id
            error.environment = metadata.environment
            error.retry_after = metadata.retry_after
            error.http_status = metadata.http_status
            error.idempotency_key = idempotency_key
            if error.code is None and body_code is not None and not resp.is_success:
                error.code = body_code
            raise error

    @staticmethod
    def _validate_sale_result(
        result: Any, method: str, idempotency_key: Optional[str], result_model: type[BaseModel]
    ) -> None:
        try:
            result_model.model_validate(result)
        except ValidationError as invalid:
            message = "Malformed or incomplete fiscal outcome; retain the original identity and inspect its status before replay."
            if method.upper() in _MUTATING_METHODS:
                raise TIaaSAmbiguousStateError(message, idempotency_key=idempotency_key) from invalid
            raise KRAeTIMSError(message) from invalid

    @staticmethod
    def _typed_result(model: type[BaseModel], result: Any) -> Any:
        """Build a typed stock result from an already-validated body, keeping its metadata."""
        typed = model.model_validate(result)
        typed.response_metadata = getattr(result, "response_metadata", None)
        return typed

    def _parse_response_body(
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
            try:
                self._raise_for_503(resp, method.upper() in _MUTATING_METHODS)
            except KRAeTIMSError as error:
                error.http_status = resp.status_code
                error.idempotency_key = idempotency_key
                error.requires_reconciliation = (method.upper() in _MUTATING_METHODS
                                                 and not getattr(error, "retryable", False))
                error.result_cd = getattr(error, "oscu_code", None)
                raise

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            if sc == 409:
                body, msg, code = self._error_fields(exc.response)
                if code is None and _MACHINE_CODE.match(str(body.get("error", ""))):
                    # Older backends put the 409 code (e.g. Z_REPORT_ALREADY_ISSUED)
                    # only in "error"; newer ones send both "code" and "error".
                    code = body["error"]
                message = f"Conflict (HTTP 409){f' [{code}]' if code else ''}: {msg}"
                if code in _UNRESOLVED_IDEMPOTENCY_CODES:
                    raise _unresolved_error_type(exc.request.url.path)(
                        f"{message} Retain the original idempotency key and look up the original "
                        "operation; do not resubmit under a new key.",
                        idempotency_key=idempotency_key, code=code,
                    ) from exc
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
                _, msg, code = self._error_fields(exc.response)
                raise _unresolved_error_type(exc.request.url.path)(
                    f"Request with this idempotency key is still in flight (HTTP 425): {msg} "
                    "Retain the original key and inspect its status before considering replay.",
                    idempotency_key=idempotency_key, code=code,
                ) from exc
            if sc == 422:
                # 422 covers VSCU terminal rejections AND the credit-note
                # over-reversal guard (middleware V15). Discriminate by body code.
                body, msg, code = self._error_fields(exc.response)
                if code in {"IDEMPOTENCY_KEY_REUSED", "FISCAL_IDENTITY_CONFLICT"}:
                    raise KRAConflictError(msg, code=code) from exc
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
                if code == "WORKER_DEFERRED":
                    raise WorkerDeferredError(
                        f"TaxID fiscal worker deferred the request (HTTP 403): {message}", code=code
                    ) from exc
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
            if sc == 429:
                _, message, code = self._error_fields(exc.response)
                suffix = f" [{code}]" if code else ""
                raise RateLimitError(f"Rate limited (HTTP 429){suffix}: {message}") from exc
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

        if resp.status_code in {204,205}:
            return {}
        try:
            if resp.request.url.path.startswith("/v2/withholding/"):
                from decimal import Decimal
                response_data = resp.json(parse_float=Decimal)
            else:
                response_data = resp.json()
        except (ValueError, httpx.DecodingError) as invalid:
            if method.upper() in _MUTATING_METHODS:
                raise TIaaSAmbiguousStateError(
                    "Unreadable mutation response; inspect the original operation before retrying.",
                    idempotency_key=idempotency_key,
                ) from invalid
            raise KRAeTIMSError(
                f"Non-JSON response from TIaaS [{resp.status_code}]: {resp.text[:200]}"
            )

        if resp.request.url.path == "/v2/admin/tenants" and isinstance(response_data, list):
            if not all(isinstance(row, dict) for row in response_data):
                raise KRAeTIMSError("Invalid tenant summary response")
            return response_data
        if isinstance(response_data,list) and resp.request.url.path == "/v2/admin/reconciliation":
            for row in response_data:self._handle_error_response(row)
            return response_data
        try:
            self._handle_error_response(response_data)
        except KRAeTIMSError as error:
            error.http_status = resp.status_code
            error.idempotency_key = idempotency_key
            if method.upper() in _MUTATING_METHODS:
                error.requires_reconciliation = True
            raise
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
        if isinstance(outcome, BaseException):
            return {
                "invoice_no":      invoice_no,
                "status":          "error",
                "message":         str(outcome),
                "error_type":      type(outcome).__name__,
                "ambiguous":       isinstance(outcome, TIaaSAmbiguousStateError) or getattr(outcome, "requires_reconciliation", False),
                "retryable":       isinstance(outcome, RateLimitError) or (
                                       isinstance(outcome, TIaaSUnavailableError)
                                       and not getattr(outcome, "requires_reconciliation", False)),
                "result_cd":       getattr(outcome, "result_cd", None),
                "http_status":     getattr(outcome, "http_status", None),
                "idempotency_key": idem_key,
                "exception":       outcome,
            }
        sale_state = outcome.get("status") if isinstance(outcome, dict) else None
        return {
            "invoice_no":      invoice_no,
            "status":          "success",
            "data":            outcome,
            "signed":          has_signed_evidence(outcome),
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
            error = TIaaSUnavailableError()
        elif method.upper() in _MUTATING_METHODS:
            error = TIaaSAmbiguousStateError(idempotency_key=idempotency_key)
        else:
            error = TIaaSUnavailableError()
        error.idempotency_key = idempotency_key
        return error

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
