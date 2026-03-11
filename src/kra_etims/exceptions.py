"""
KRA eTIMS SDK — Exception Taxonomy
====================================
Every exception maps 1-to-1 to an actionable failure mode.
The developer must never have to guess what went wrong or read raw JSON.

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


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class KRAeTIMSError(Exception):
    """Root exception for all KRA eTIMS SDK errors."""


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
    """Authentication failed — bad credentials or expired token."""


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
    """
    def __init__(self, message: str = (
        "TIaaS Ambiguous State: Request sent but connection dropped before "
        "response. Retry with the same idempotency key — the middleware will "
        "deduplicate it safely."
    )):
        super().__init__(message)


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
    """
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


# ---------------------------------------------------------------------------
# Internal error-code → exception mapping used by _handle_error_response()
# ---------------------------------------------------------------------------

KRA_ERROR_MAP: dict = {
    "01": (KRAeTIMSAuthError,       "Authentication failed"),
    "10": (KRAInvalidPINError,      "Invalid PIN Format: Expected A123456789B"),
    "11": (KRAVSCUMemoryFullError,  "VSCU Memory Full: Device storage is at capacity"),
    "12": (KRADuplicateInvoiceError,"Duplicate Invoice Number"),
    "13": (KRAInvalidItemCodeError, "Invalid Item Code"),
    "14": (KRAInvalidBranchError,   "Invalid Branch ID"),
    "20": (KRAServerError,          "KRA Server Processing Error"),
    "96": (KRAServerError,          "KRA System Error (transient)"),
    "99": (KRAServerError,          "KRA Unknown System Error"),
}
