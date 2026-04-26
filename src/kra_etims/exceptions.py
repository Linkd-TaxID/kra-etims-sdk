"""
KRA eTIMS SDK — Exception Taxonomy
====================================
Every exception maps 1-to-1 to an actionable failure mode.
The developer must never have to guess what went wrong or read raw JSON.

Full KRA result code reference (all 30 codes, including production-only codes
absent from the official spec): https://linkd-taxid.github.io/kra-etims-sdk/

Critical integration facts
--------------------------
- Success codes: "00" (VSCU JAR), "000" (OSCU spec), "0000" (GavaConnect).
  Never check ``resultCd == "000"`` alone — always test membership in
  ``{"0", "00", "000", "0000"}``.

- resultCd 001 is NOT an error. It means no records match the query (empty
  list). Most public SDKs mistakenly raise here on day-one queries.

- resultCd 994 on retry is idempotent success. The invoice is already on KRA.
  Do not re-raise; do not re-submit with a new invoice number.

- resultCd 921: VSCU requires saveSales → saveInvoice in sequence. OSCU
  uses a single combined call. These paths cannot be mixed.

- resultCd 901: device serial not approved. Unrecoverable without KRA
  intervention. Email timsupport@kra.go.ke to register.

- resultCd 902: device already initialized. Extract the existing cmcKey from
  the response body — do not re-initialize.

- cmcKey is issued exactly once. Store AES-256-GCM encrypted. Redact before
  any logging. No self-service rotation path exists.

KRA Result Code Reference (eTIMS v2.0 spec):
  00  Success
  01  Authentication / credential failure
  10  Invalid PIN format (expected pattern: A123456789B)
  11  VSCU memory full — device must sync / purge before accepting invoices
  12  Duplicate invoice number — idempotent: already processed successfully
  13  Invalid item code — item not registered on eTIMS
  14  Invalid branch ID — bhfId not recognised for this TIN
  20  KRA server-side processing error
  96  KRA system error (transient)
  99  Unknown / catch-all KRA error
"""
from typing import Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class KRAeTIMSError(Exception):
    """
    Root exception for all KRA eTIMS SDK errors.

    Full result code reference: https://linkd-taxid.github.io/kra-etims-sdk/
    """


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


class TIaaSUnavailableError(KRAeTIMSError):
    """The TIaaS Railway instance is unreachable (sleeping or down)."""
    def __init__(self, message: str = (
        "TIaaS Service Unavailable: The Railway instance is unreachable."
    )):
        super().__init__(message)


class TIaaSAmbiguousStateError(KRAeTIMSError):
    """
    Network dropped after the request was sent but before a response was
    received.  The invoice state on KRA / TIaaS is unknown — Schrödinger's
    Invoice.  Retry with the **same** idempotency key; the middleware will
    deduplicate automatically.

    ``idempotency_key`` carries the key that was in-flight so callers can
    retry without having to manage key storage separately.
    """
    def __init__(
        self,
        message: str = (
            "TIaaS Ambiguous State: Request sent but connection dropped before "
            "response. Retry with the same idempotency key — the middleware will "
            "deduplicate it safely."
        ),
        idempotency_key: str | None = None,
    ):
        super().__init__(message)
        self.idempotency_key = idempotency_key


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
    """
    The invoice number was already processed successfully (KRA result code 12).
    This is idempotent — the original submission succeeded.
    Do NOT retry with a new invoice number; retrieve the original receipt instead.

    ``is_idempotent_success = True`` signals callers (e.g. flush_offline_queue)
    that the invoice IS on KRA and the exception should be treated as success.
    """
    is_idempotent_success: bool = True

    def __init__(self, message: str = (
        "Duplicate Invoice Number (Code 12): This invoice was already "
        "processed. Retrieve the original signed receipt instead of retrying."
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


class CreditNoteConflictError(KRAeTIMSError):
    """
    A credit note has already been issued for this sale (HTTP 409).

    KRA prohibits issuing more than one credit note per original invoice.
    Retrieve the existing credit note receipt instead of retrying.

    ``original_purchase_id`` carries the ID of the sale that already has a
    credit note attached, so callers can look it up without parsing the message.
    """
    def __init__(
        self,
        message: str = (
            "Credit Note Conflict (HTTP 409): A credit note has already been "
            "issued for this sale. Retrieve the existing credit note receipt — "
            "KRA prohibits duplicate credit notes per original invoice."
        ),
        original_purchase_id: Optional[int] = None,
    ):
        super().__init__(message)
        self.original_purchase_id = original_purchase_id


class ZReportAlreadyIssuedError(KRAeTIMSError):
    """
    The Z-report for the requested date has already been issued (HTTP 409).

    The VSCU day-reset command is irreversible (KRA TIS v2.0 §21.6.1 —
    ★ RESEARCH-VALIDATED). TIaaS enforces exactly one Z-report per branch
    per calendar day via a UNIQUE(branch_id, report_date) constraint.

    This exception is NOT retryable. The Z-report was already submitted
    successfully. Retrieve the existing report if you need the totals.

    ``report_date`` carries the date string from the 409 response body,
    if present, so callers can log or display it without parsing the message.
    """
    def __init__(
        self,
        message: str = (
            "Z-Report Already Issued (HTTP 409): The daily Z-report has already "
            "been submitted for this date. The VSCU day-reset is irreversible — "
            "do not retry. Retrieve the existing report for totals."
        ),
        report_date: Optional[str] = None,
    ):
        super().__init__(message)
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
    # Extended codes observed in live GavaConnect / VSCU responses
    # 994: invoice already processed on a prior retry — idempotent success.
    # Do not re-submit with a new invoice number; the receipt exists on KRA.
    "994": (KRADuplicateInvoiceError, "Duplicate invoice (code 994 — prior retry succeeded)"),
    # 901: device serial not approved — requires KRA eTIMS portal action.
    "901": (KRAeTIMSError, "Device serial not approved — contact timsupport@kra.go.ke to register"),
    # 902: device already initialized — retrieve existing cmcKey, do not re-initialize.
    # Treated as KRADuplicateInvoiceError so is_idempotent_success=True is available.
    "902": (KRADuplicateInvoiceError, "Device already initialized (code 902) — existing cmcKey remains valid"),
    # 921: VSCU requires saveSales → saveInvoice in sequence. Cannot mix OSCU path.
    "921": (KRAeTIMSError, "VSCU sequence error (code 921) — saveSales must precede saveInvoice"),
}
