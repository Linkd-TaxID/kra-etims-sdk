"""
taxid-etims
==============
Python SDK for the KRA eTIMS TIaaS (Tax Identity as a Service) middleware.

Single-import developer experience::

    from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item

Clients
-------
- ``KRAeTIMSClient``       — sync client (Django, Flask, scripts)
- ``AsyncKRAeTIMSClient``  — async client (FastAPI, Starlette, async workers)

Tax Calculator
--------------
- ``calculate_item``       — zero-math: pass name + price + band, get ItemDetail
- ``build_invoice_totals`` — aggregate line items into invoice header totals

QR Code
-------
- ``render_kra_qr_string`` — extract KRA QR string from signed receipt
- ``generate_qr_bytes``    — render PNG bytes for thermal printer

Gateway
-------
- ``TaxIDSupplierGateway``      — sync USSD/WhatsApp reverse invoicing
- ``AsyncTaxIDSupplierGateway`` — async USSD/WhatsApp reverse invoicing

Reports
-------
- ``ReportsInterface``      — sync X/Z reporting
- ``AsyncReportsInterface`` — async X/Z reporting
- ``XReport``, ``ZReport``  — strictly-typed report models

Exceptions
----------
- ``KRAeTIMSError``           — root exception
- ``KRAeTIMSAuthError``       — bad credentials / token refresh failure
- ``KRAConnectivityTimeoutError`` — 24-hour VSCU offline ceiling breached
- ``TIaaSUnavailableError``   — Railway instance unreachable
- ``TIaaSAmbiguousStateError``— Schrödinger's Invoice (retry with same key)
- ``KRAValidationError``      — schema / business-rule validation failure
- ``KRAInvalidPINError``      — invalid TIN/PIN format (code 10)
- ``KRAVSCUMemoryFullError``  — VSCU storage at capacity (code 11)
- ``KRADuplicateInvoiceError``— already processed successfully (code 12)
- ``KRAInvalidItemCodeError`` — item not registered on eTIMS (code 13)
- ``KRAInvalidBranchError``   — branch not registered for this TIN (code 14)
- ``KRAServerError``          — transient KRA server error (codes 20/96/99)
- ``CreditNoteConflictError`` — credit note already issued for this sale (HTTP 409)
"""

# Clients
from .client import KRAeTIMSClient
from .async_client import AsyncKRAeTIMSClient

# Models
from .models import (
    # Enums
    ItemType,
    TaxType,
    ReceiptLabel,
    # Category 1
    DeviceInit,
    # Category 2
    DataSyncRequest,
    # Category 3
    BranchInfo,
    # Category 4
    ItemSave,
    # Category 5
    ImportItem,
    # Category 6 & 7
    ItemDetail,
    SaleInvoice,
    ReverseInvoice,
    # Category 8
    StockItem,
    StockAdjustmentLine,
    StockAdjustmentRequest,
    # Validation constant
    KRA_TIN_PATTERN,
)

# Tax calculator
from .tax import calculate_item, build_invoice_totals

# QR generator
from .qr import render_kra_qr_string, generate_qr_bytes, save_qr_image

# Gateway
from .gateway import (
    TaxIDSupplierGateway,
    AsyncTaxIDSupplierGateway,
    SupplierEntry,
    SupplierOnboardingResponse,
    BulkOnboardingResponse,
    SupplierGatewayStatus,
)

# Reports
from .reports import (
    ReportsInterface,
    AsyncReportsInterface,
    XReport,
    ZReport,
    TaxBreakdown,
)

# Exceptions
from .exceptions import (
    KRAeTIMSError,
    KRAeTIMSAuthError,
    KRAConnectivityTimeoutError,
    TIaaSUnavailableError,
    TIaaSAmbiguousStateError,
    KRAValidationError,
    KRAInvalidPINError,
    KRAVSCUMemoryFullError,
    KRADuplicateInvoiceError,
    KRAInvalidItemCodeError,
    KRAInvalidBranchError,
    KRAServerError,
    CreditNoteConflictError,
)

__version__ = "0.2.0"

__all__ = [
    # Clients
    "KRAeTIMSClient",
    "AsyncKRAeTIMSClient",
    # Models
    "ItemType",
    "TaxType",
    "ReceiptLabel",
    "DeviceInit",
    "DataSyncRequest",
    "BranchInfo",
    "ItemSave",
    "ImportItem",
    "ItemDetail",
    "SaleInvoice",
    "ReverseInvoice",
    "StockItem",
    "StockAdjustmentLine",
    "StockAdjustmentRequest",
    "KRA_TIN_PATTERN",
    # Tax
    "calculate_item",
    "build_invoice_totals",
    # QR
    "render_kra_qr_string",
    "generate_qr_bytes",
    "save_qr_image",
    # Gateway
    "TaxIDSupplierGateway",
    "AsyncTaxIDSupplierGateway",
    "SupplierEntry",
    "SupplierOnboardingResponse",
    "BulkOnboardingResponse",
    "SupplierGatewayStatus",
    # Reports
    "ReportsInterface",
    "AsyncReportsInterface",
    "XReport",
    "ZReport",
    "TaxBreakdown",
    # Exceptions
    "KRAeTIMSError",
    "KRAeTIMSAuthError",
    "KRAConnectivityTimeoutError",
    "TIaaSUnavailableError",
    "TIaaSAmbiguousStateError",
    "KRAValidationError",
    "KRAInvalidPINError",
    "KRAVSCUMemoryFullError",
    "KRADuplicateInvoiceError",
    "KRAInvalidItemCodeError",
    "KRAInvalidBranchError",
    "KRAServerError",
    "CreditNoteConflictError",
]
