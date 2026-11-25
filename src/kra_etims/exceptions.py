"""KRA/TaxID exception taxonomy.

Official OSCU v2.0 §4.18 and VSCU v2.0 §4.14 describe 902 as an
installed-device condition and 994 as overlapping data. Neither establishes
transaction readiness, identical contents, or a recovered signed receipt.
Codes 894 and 990 do not prove a fiscal mutation was never applied.
Retain original identities and investigate outcomes before considering replay.
Legacy two-digit/GavaConnect mappings are separate from these contracts.
"""
from decimal import Decimal
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class KRAeTIMSError(Exception):
    """
    Root exception for all KRA eTIMS SDK errors.

    Full result code reference: https://linkd-taxid.github.io/kra-etims-sdk/

    ``code`` is the server's machine-readable error code (e.g. ``INVALID_REQUEST``)
    when the response carried one, else ``None``. ``retry_after`` is the raw
    ``Retry-After`` header and ``retry_after_seconds`` its parsed value.
    """
    code: Optional[str] = None

    @property
    def retry_after_seconds(self) -> Optional[float]:
        """The raw ``retry_after`` header parsed to seconds (RFC 9110 §10.2.3:
        delay-seconds or an HTTP-date), or ``None`` when absent or invalid."""
        return self.parse_retry_after(getattr(self, "retry_after", None))

    @staticmethod
    def parse_retry_after(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if value.isascii() and value.isdigit():
            return float(value)
        try:
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


# ---------------------------------------------------------------------------
# Infrastructure Errors (network / platform layer)
# ---------------------------------------------------------------------------

class KRAConnectivityTimeoutError(KRAeTIMSError):
    """
    The 24-hour VSCU offline ceiling has been breached (HTTP 503).
    The middleware cannot sign invoices until KRA GavaConnect is reachable.
    """
    def __init__(self, message: str = (
        "KRA Connectivity Timeout: The 24-hour VSCU offline ceiling has been "
        "breached. TIaaS cannot sign invoices until connectivity to KRA "
        "GavaConnect is restored."
    )):
        super().__init__(message)


class OSCUUnavailableError(KRAeTIMSError):
    """
    OSCU (KRA-hosted, always-online-only control unit) signing failed
    transiently (HTTP 503 from TIaaS).

    Distinct from :class:`KRAConnectivityTimeoutError` on purpose: OSCU has
    no 24-hour offline ceiling — it has zero offline tolerance by design
    (there is no local JAR to fall back to while KRA is unreachable, unlike
    VSCU). "OSCU is briefly unreachable" and "the 24-hour VSCU ceiling was
    breached" are unrelated failure conditions that happen to share the same
    HTTP status code from the middleware — conflating them (as every 503 did
    before this exception existed) misdiagnoses the failure for anyone
    building alerting or retry logic around the specific exception type.

    ``oscu_code`` carries the raw KRA OSCU Specification v2.0 §4.18 result
    code (e.g. ``"894"`` communication error, ``"990"`` rate limit exceeded)
    when the middleware's response body includes it (TaxID's
    ``GlobalExceptionHandler.handleOscuSigning`` sets this as the
    ``oscu_code`` property on its RFC 9457 ``ProblemDetail`` body).
    """

    def __init__(
        self,
        message: str = (
            "OSCU Temporarily Unavailable: KRA's OSCU server could not be "
            "reached or returned a transient error. This is unrelated to the "
            "24-hour VSCU offline ceiling — OSCU has no such ceiling by "
            "design. Inspect the original mutation before considering replay; backoff alone does not establish a safe retry."
        ),
        oscu_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.oscu_code = oscu_code


class KRAeTIMSAuthError(KRAeTIMSError):
    """
    Authentication failed (HTTP 401) — invalid API key, missing key,
    or expired/invalid OAuth2 token.
    """


class KRAAuthorizationError(KRAeTIMSError):
    """
    Authorization denied (HTTP 403) — the credential is valid but lacks
    the required role for this endpoint (e.g. ROLE_SDK_CLIENT attempting
    an ROLE_ADMIN endpoint such as /v2/etims/init-handshake).
    """


class RateLimitError(KRAeTIMSError):
    """
    HTTP 429 (``code`` usually ``RATE_LIMITED``). Nothing was processed.

    Use ``retry_after_seconds`` (inherited) for the delay.
    """


class TIaaSUnavailableError(KRAeTIMSError):
    """The middleware service is unavailable."""
    def __init__(self, message: str = (
        "TIaaS Service Unavailable: The middleware service is unavailable."
    )):
        super().__init__(message)


class FiscalWorkerUnavailableError(TIaaSUnavailableError):
    """
    TaxID's fiscal worker refused the request before dispatching it to the
    control unit, and the server marked the refusal ``retryable``. Safe to
    retry with the same idempotency key and body after a backoff.

    ``code`` carries the server code (``WORKER_DEFERRED`` or ``WORKER_STOPPING``).
    """
    retryable: bool = True

    def __init__(self, message: str = (
        "TaxID fiscal worker unavailable: the request was refused before dispatch. "
        "Retry with the same idempotency key after a backoff."
    ), code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class WorkerDeferredError(FiscalWorkerUnavailableError, KRAAuthorizationError):
    """HTTP 403 ``WORKER_DEFERRED``: worker-gate refusal, safe to retry.

    Also a :class:`KRAAuthorizationError` so existing 403 handlers still match.
    """


class WorkerStoppingError(FiscalWorkerUnavailableError):
    """HTTP 503 ``WORKER_STOPPING``: the worker is shutting down, safe to retry."""


class TIaaSAmbiguousStateError(KRAeTIMSError):
    """
    Network dropped after the request was sent but before a response was
    received.  The invoice state on KRA / TIaaS is unknown — Schrödinger's
    Invoice. Retain the original idempotency key and inspect the original
    status. Do not automatically replay or invent a replacement reference.

    ``idempotency_key`` carries the original in-flight identity for status lookup
    and evidence-based recovery.
    """
    def __init__(
        self,
        message: str = (
            "TIaaS Ambiguous State: Request sent but connection dropped before "
            "response. Retain the original idempotency key and inspect its status "
            "before considering replay."
        ),
        idempotency_key: str | None = None,
    ):
        super().__init__(message)
        self.idempotency_key = idempotency_key


class MutationServiceUnavailableError(TIaaSUnavailableError, TIaaSAmbiguousStateError):
    """
    An unrecognised HTTP 503 on a mutating request (no ``WORKER_STOPPING``,
    ``KRA_CONNECTIVITY_TIMEOUT`` or ``oscu_code``).

    TaxID cannot always show such a 503 was raised before dispatch (a transient
    control-unit code, a proxy, an empty body), so the original may have been
    applied. It is a :class:`TIaaSAmbiguousStateError`: keep ``idempotency_key``
    and inspect the original before any replay. It stays a
    :class:`TIaaSUnavailableError` so existing handlers still match, but it is
    not retryable: handle it before any ``except TIaaSUnavailableError`` that
    retries. Reads keep raising a plain :class:`TIaaSUnavailableError`.
    """
    retryable: bool = False

    def __init__(self, message: str, idempotency_key: Optional[str] = None):
        TIaaSAmbiguousStateError.__init__(self, message, idempotency_key=idempotency_key)


class MutationOSCUUnavailableError(OSCUUnavailableError, TIaaSAmbiguousStateError):
    """
    A transient OSCU 503 (``oscu_code``) on a mutating request.

    The control unit may have applied the original before it failed, so this is
    also a :class:`TIaaSAmbiguousStateError`: keep ``idempotency_key`` and inspect
    the original before any replay. Reads raise a plain
    :class:`OSCUUnavailableError`.
    """
    def __init__(self, message: str, oscu_code: Optional[str] = None,
                 idempotency_key: Optional[str] = None):
        TIaaSAmbiguousStateError.__init__(self, message, idempotency_key=idempotency_key)
        self.oscu_code = oscu_code


class UnsupportedOperationError(KRAeTIMSError, NotImplementedError):
    """The SDK method targets a route the TaxID backend does not serve.

    Raised locally without sending a request. Also a ``NotImplementedError``.
    """


# ---------------------------------------------------------------------------
# KRA Application Errors (business / validation layer)
# ---------------------------------------------------------------------------

class KRAValidationError(KRAeTIMSError):
    """
    The payload failed KRA v2.0 schema / business-rule validation.
    Inspect the message for the specific field or constraint.
    """


class KRAInvalidPINError(KRAValidationError):
    """
    The TIN / PIN supplied does not match the KRA format.
    Expected pattern: A123456789B (letter + 9 digits + letter).
    Result code: 10
    """
    def __init__(self, message: str = (
        "Invalid PIN Format: Expected pattern A123456789B "
        "(1 letter + 9 digits + 1 letter)."
    )):
        super().__init__(message)


class KRAVSCUMemoryFullError(KRAeTIMSError):
    """
    The VSCU device storage is at capacity (KRA result code 11).
    The device must sync / purge stored receipts before accepting new invoices.
    """
    def __init__(self, message: str = (
        "VSCU Memory Full (Code 11): The device storage is at capacity. "
        "Sync and purge stored receipts before submitting new invoices."
    )):
        super().__init__(message)


class KRADuplicateInvoiceError(KRAeTIMSError):
    """Duplicate data requires original receipt and content evidence.

    The public exception name and compatibility attribute are retained, but a
    bare duplicate response cannot be counted as signed or completed success.
    """
    is_idempotent_success: bool = False
    requires_reconciliation: bool = True

    def __init__(self, message: str = (
        "Duplicate data: retain the original reference and inspect its outcome "
        "and receipt. Do not create a replacement invoice reference."
    )):
        super().__init__(message)


class KRADeviceInstalledError(KRAeTIMSError):
    """902 reports an installed device, not a duplicate invoice or recovered key."""
    is_idempotent_success: bool = False
    requires_reconciliation: bool = True

    def __init__(self, message: str = (
        "Device installed (902): verify the retained identity and protocol-specific "
        "checkpoint. Missing credentials require operator/KRA recovery."
    )):
        super().__init__(message)


class KRAInvalidItemCodeError(KRAValidationError):
    """Item code not registered on eTIMS (KRA result code 13)."""
    def __init__(self, message: str = (
        "Invalid Item Code (Code 13): The item code is not registered on "
        "eTIMS. Register the item via Category 4 before invoicing."
    )):
        super().__init__(message)


class KRAInvalidBranchError(KRAValidationError):
    """Branch ID not recognised for this TIN (KRA result code 14)."""
    def __init__(self, message: str = (
        "Invalid Branch ID (Code 14): The bhfId is not registered for this "
        "TIN. Verify the branch was initialised via Category 1."
    )):
        super().__init__(message)


class KRAServerError(KRAeTIMSError):
    """Transient KRA server-side processing failure (result codes 20, 96, 99)."""


class KRAConflictError(KRAeTIMSError):
    """
    HTTP 409 Conflict. ``code`` is the middleware's error code, e.g.
    ``FISCAL_DAY_CLOSED`` (the sale's date is already Z-closed) or
    ``ETIMS_NOT_INITIALIZED`` (the branch has not completed its handshake).
    """
    def __init__(self, message: str = "Conflict (HTTP 409)", code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class IdempotentOutcomeUnresolvedError(TIaaSAmbiguousStateError, KRAConflictError):
    """
    The server holds an earlier claim for this idempotency key but cannot
    return its result (HTTP 409/425 from TaxID's idempotency layer).

    ``code`` is one of ``IDEMPOTENT_RESULT_LOOKUP_REQUIRED`` (original still in
    flight or its cached response is unavailable), ``IDEMPOTENT_IDENTITY_UNAVAILABLE``
    (the claim exists but cannot be read back) or ``IDEMPOTENCY_SCOPE_UNBOUND``
    (a historical claim recorded before request scopes were stored). In every
    case the original may have been applied: keep ``idempotency_key``, look the
    original up (``find_sale(operation_key=...)``) and never resubmit under a
    new key. Subclasses :class:`KRAConflictError` so existing 409 handlers match.
    """
    def __init__(self, message: str, idempotency_key: Optional[str] = None, code: Optional[str] = None):
        TIaaSAmbiguousStateError.__init__(self, message, idempotency_key=idempotency_key)
        self.code = code


class CreditNoteConflictError(KRAConflictError):
    """
    HTTP 409 on a credit-note request (for example ``ETIMS_NOT_INITIALIZED``).

    .. note::
       Partial or repeated returns are available only in explicit synthetic
       fixtures. Production dispatch requires a separately accepted contract.
       Over-reversal raises :class:`CreditNoteExceedsOriginalError` (HTTP 422).

    ``original_purchase_id`` carries the sale ID when present, so callers can
    look it up without parsing the message.
    """
    def __init__(
        self,
        message: str = (
            "Credit Note Conflict (HTTP 409): A credit note has already been "
            "issued for this sale. Retrieve the existing credit note receipt — "
            "KRA prohibits duplicate credit notes per original invoice."
        ),
        original_purchase_id: Optional[int] = None,
        existing_credit_note_id: Optional[int] = None,
        existing_cu_invoice_no: Optional[str] = None,
        code: Optional[str] = None,
    ):
        super().__init__(message, code=code)
        self.original_purchase_id = original_purchase_id
        # Populated from the middleware's 409 body ({existingCreditNoteId,
        # existingCuInvoiceNo}) — the fields callers need to fetch the winner.
        self.existing_credit_note_id = existing_credit_note_id
        self.existing_cu_invoice_no = existing_cu_invoice_no


class CreditNoteOutcomeUnresolvedError(IdempotentOutcomeUnresolvedError, CreditNoteConflictError):
    """
    :class:`IdempotentOutcomeUnresolvedError` raised by ``issue_credit_note``:
    TaxID holds an earlier claim for this credit-note idempotency key but cannot
    return its result. The original correction may have been applied; keep
    ``idempotency_key``, inspect the original and never resubmit under a new key.

    Also a :class:`CreditNoteConflictError` (and so a :class:`KRAConflictError`),
    so credit-note 409 handlers written for SDK 0.8.1 still match.
    """
    def __init__(self, message: str, idempotency_key: Optional[str] = None, code: Optional[str] = None):
        IdempotentOutcomeUnresolvedError.__init__(self, message, idempotency_key=idempotency_key, code=code)
        self.original_purchase_id = None
        self.existing_credit_note_id = None
        self.existing_cu_invoice_no = None


class CreditNoteExceedsOriginalError(KRAeTIMSError):
    """
    Issuing this credit note would exceed the original receipt's reversible
    balance (HTTP 422, code ``CREDIT_NOTE_EXCEEDS_ORIGINAL``).

    Supported synthetic return fixtures reserve original quantities and amounts.
    This error means the requested cumulative reversal exceeds the original.
    It does not establish permission for repeated production corrections.

    ``remaining`` is the amount still reversible and ``already_reversed`` the
    sum already credited (both from the 422 body). Retry with a note whose
    amount is ``<= remaining``.
    """
    def __init__(
        self,
        message: str = (
            "Credit Note Exceeds Original (HTTP 422): the reversal would exceed "
            "the receipt's remaining reversible balance. Issue a smaller credit note."
        ),
        remaining: Any = None,
        already_reversed: Any = None,
        original_purchase_id: Optional[int] = None,
    ):
        super().__init__(message)
        # The 422 body carries JSON numbers; go through str() so a float such as
        # 70.0 becomes Decimal("70.0") rather than its binary expansion.
        self.remaining: Optional[Decimal] = None if remaining is None else Decimal(str(remaining))
        self.already_reversed: Optional[Decimal] = (
            None if already_reversed is None else Decimal(str(already_reversed)))
        self.original_purchase_id = original_purchase_id


class CreditNoteQuantityExceedsOriginalError(CreditNoteExceedsOriginalError):
    """
    A returned line exceeds the quantity still reversible on the original (HTTP
    422, code ``CREDIT_NOTE_QUANTITY_EXCEEDS_ORIGINAL``). Also a
    :class:`CreditNoteExceedsOriginalError`; its amount balances are ``None``.

    ``original_item_seq`` is the original line and ``remaining_quantity`` the
    quantity still reversible on it.
    """
    def __init__(self, message: str, original_item_seq: Optional[int] = None,
                 remaining_quantity: Any = None):
        super().__init__(message)
        self.original_item_seq = original_item_seq
        self.remaining_quantity: Optional[Decimal] = (
            None if remaining_quantity is None else Decimal(str(remaining_quantity)))


class ZReportAlreadyIssuedError(KRAConflictError):
    """
    The Z-report for the requested date has already been issued (HTTP 409,
    ``code`` ``Z_REPORT_ALREADY_ISSUED``). Also a :class:`KRAConflictError`, so
    generic 409 handlers match it.

    This records completion of the local daily aggregate. No remote device
    fiscal-close request or central KRA acknowledgement is implied. Retrieve
    the existing report for totals rather than issuing another local close.

    ``report_date`` carries the date string from the 409 response body,
    if present, so callers can log or display it without parsing the message.
    """
    def __init__(
        self,
        message: str = (
            "Z-Report Already Issued (HTTP 409): The daily Z-report has already "
            "been finalized locally for this date. Retrieve the existing report "
            "for totals; no remote fiscal-close acknowledgement is implied."
        ),
        report_date: Optional[str] = None,
        code: Optional[str] = "Z_REPORT_ALREADY_ISSUED",
    ):
        super().__init__(message, code=code)
        self.report_date = report_date


# ---------------------------------------------------------------------------
# Internal error-code → exception mapping used by _handle_error_response()
# ---------------------------------------------------------------------------

KRA_ERROR_MAP: dict = {
    # Numeric result codes — KRA eTIMS Technical Specification v2.0
    "01": (KRAeTIMSAuthError,        "Authentication failed"),
    "10": (KRAInvalidPINError,       "Invalid PIN Format: Expected A123456789B"),
    "11": (KRAVSCUMemoryFullError,   "VSCU Memory Full: Device storage is at capacity"),
    "12": (KRADuplicateInvoiceError, "Duplicate Invoice Number"),
    "13": (KRAInvalidItemCodeError,  "Invalid Item Code"),
    "14": (KRAInvalidBranchError,    "Invalid Branch ID"),
    "20": (KRAServerError,           "KRA Server Processing Error"),
    "96": (KRAServerError,           "KRA System Error (transient)"),
    "99": (KRAServerError,           "KRA Unknown System Error"),
    # Letter-prefixed variants observed in live KRA GavaConnect responses
    "E04": (KRAInvalidBranchError,   "Device/Branch Not Found"),
    "E11": (KRAVSCUMemoryFullError,  "VSCU Memory Full (prefixed)"),
    # Official installed-device/overlapping-data codes; no receipt success is inferred.
    "994": (KRADuplicateInvoiceError, "Overlapping data (994) — original receipt evidence required"),
    "901": (KRAeTIMSError, "Device not valid (901) — verify device enrollment and environment"),
    "902": (KRADeviceInstalledError, "Device installed (902) — verify retained identity and checkpoint"),
    "921": (KRAeTIMSError, "VSCU sequence error (code 921) — inspect the original operation and documented adapter contract"),
}
