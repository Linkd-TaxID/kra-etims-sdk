"""
Shared pytest fixtures for the kra-etims-sdk test suite.

KRA response envelope format (research-validated against KRA OSCU Spec v2.0
and confirmed in CLAUDE.md §4 Mathematical Integrity):

  {
    "resultCd":  "000",                  # "000" = success (GavaConnect), "00" = VSCU JAR
    "resultMsg": "It is succeeded",
    "resultDt":  "20260420120000",       # YYYYMMDDHHmmss
    "data": { ... }                      # present only on success
  }

Error codes per KRA_ERROR_MAP (§21.6.3 VSCU / GavaConnect):
  "000"/"00" = success
  "001"      = no records found
  "10"       = invalid PIN
  "11"       = VSCU memory full
  "12"       = duplicate invoice
  "13"       = invalid item code
  "14"       = invalid branch
  "20"       = auth error
  "96"       = system error
  "99"       = unknown error

VSCU signing response fields (KRA TIS §5.3, VSCU Spec §6.23.3–§6.23.8):
  rcptNo       — CU Invoice Number: "{CU_ID}/{sequence} {label}"
  sdcId        — VSCU/OSCU Serial Number (SCU ID)
  signature    — Receipt Signature (§6.23.7): 4-char groups separated by dashes
  internalData — Encrypted audit token (§6.23.6)
  totRcptNo    — Monotonic total receipt counter (GNumber, §5.3)
  vsdcRcptPbctDate — VSCU authoritative timestamp (YYYYMMDDHHmmss)
  qrCode       — Raw QR payload per §6.23.8:
                 ddMMyyyy#HHmmss#cuNumber#cuReceiptNumber#internalData#signature

QR format per KRA TIS §6.23.8:
  invoice_date(ddmmyyyy)#time(hhmmss)#cu_number#cu_receipt_number#internal_data#receipt_signature
"""

import pytest
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# KRA response envelope builders
# ---------------------------------------------------------------------------

def kra_success_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Wraps data in the standard KRA GavaConnect success envelope.
    Use for mocking /v2/etims/sale, /v2/etims/compliance/:tin, and
    any other endpoint that returns a signed receipt or identity result.
    """
    return {
        "resultCd":  "000",
        "resultMsg": "It is succeeded",
        "resultDt":  "20260420120000",
        "data":      data,
    }


def kra_empty_response() -> dict[str, Any]:
    """
    Standard KRA 'no records found' envelope.
    Returned by compliance endpoints when the TIN is unknown to KRA.
    """
    return {
        "resultCd":  "001",
        "resultMsg": "No records found",
        "resultDt":  "20260420120000",
        "data":      {},
    }


def kra_error_response(code: str, msg: str) -> dict[str, Any]:
    """
    Standard KRA error envelope for non-success result codes.
    'data' key is absent on error — the real KRA API omits it.

    Args:
        code: KRA result code string (e.g. "10", "11", "12")
        msg:  Human-readable error message from KRA
    """
    return {
        "resultCd":  code,
        "resultMsg": msg,
        "resultDt":  "20260420120000",
    }


def kra_vscu_signing_response(
    *,
    rcpt_no: str = "KRACU0100000001/001 NS",
    sdc_id: str = "KRACU0100000001",
    signature: str = "V249-J39C-FJ48-HE2W",
    internal_data: str = "INTR0001ABCDTEST",
    tot_rcpt_no: int = 1,
    vsdc_date: str = "20260420120000",
) -> dict[str, Any]:
    """
    Realistic VSCU JAR signing response per KRA TIS §5.3 and §6.23.3–§6.23.8.

    The 'resultCd' is absent on VSCU success — VscuSignedReceiptDto.isSuccess()
    treats null resultCd as success. This mirrors the real JAR behavior.

    QR format per §6.23.8: ddMMyyyy#HHmmss#cuNumber#cuReceiptNumber#internalData#signature
    cuReceiptNumber is the sequence part of rcptNo (e.g. "001" from "KRACU.../001 NS").
    """
    # Extract sequence number from rcptNo for QR construction
    try:
        seq_part = rcpt_no.split("/")[1].split(" ")[0]  # "001" from "KRACU.../001 NS"
    except (IndexError, AttributeError):
        seq_part = "001"

    # QR: ddMMyyyy#HHmmss#cuNumber#cuReceiptNumber#internalData#signature
    qr_code = f"20042026#120000#{sdc_id}#{seq_part}#{internal_data}#{signature}"

    return {
        "rcptNo":            rcpt_no,
        "totRcptNo":         tot_rcpt_no,
        "vsdcRcptPbctDate":  vsdc_date,
        "sdcId":             sdc_id,
        "signature":         signature,
        "internalData":      internal_data,
        "qrCode":            qr_code,
    }


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kra_sale_success():
    """
    Complete KRA success envelope for a sale submission.
    Use with responses.add(..., json=kra_sale_success) in SDK tests.
    """
    return kra_success_response(kra_vscu_signing_response())


@pytest.fixture
def kra_sale_success_seq2():
    """Second receipt in sequence — used in idempotency and sequence gap tests."""
    return kra_success_response(
        kra_vscu_signing_response(rcpt_no="KRACU0100000001/002 NS", tot_rcpt_no=2)
    )


@pytest.fixture
def kra_compliance_ok():
    """Realistic compliance check success — TIN is active and compliant."""
    return kra_success_response({
        "tin":    "A000111222B",
        "status": "ACTIVE",
        "name":   "Test Company Ltd",
        "tccExpiry": "20261231",
    })


@pytest.fixture
def kra_compliance_unknown():
    """TIN not found in KRA iTax — compliance endpoint returns 001."""
    return kra_empty_response()


@pytest.fixture
def kra_vscu_memory_full():
    """VSCU memory full (code 11) — terminal error per §21.6.3."""
    return kra_error_response("11", "Internal memory full — VSCU storage exhausted")


@pytest.fixture
def kra_duplicate_invoice():
    """Duplicate invoice (code 12) — terminal error, KRA already has this invcNo."""
    return kra_error_response("12", "Duplicate invoice number")


@pytest.fixture
def kra_invalid_pin():
    """Invalid PIN (code 10) — terminal error, PIN not in iTax."""
    return kra_error_response("10", "PIN not found in iTax database")


@pytest.fixture
def kra_transient_error():
    """Internet/connectivity error (code 90) — transient, safe to retry."""
    return kra_error_response("90", "Internet connection error — retry")


@pytest.fixture
def minimal_invoice():
    """
    Minimal valid SaleInvoice for use in tests that don't care about item detail.
    All amounts use Decimal to avoid float drift (CLAUDE.md §4 Mathematical Integrity).
    """
    from kra_etims.models import SaleInvoice
    return SaleInvoice(
        tin="A000111222B",
        bhfId="00",
        invcNo="INV-TEST-001",
        custNm="Test Customer",
        confirmDt="20260420120000",
        totItemCnt=0,
        totTaxblAmt=Decimal("0.00"),
        totTaxAmt=Decimal("0.00"),
        totAmt=Decimal("0.00"),
        itemList=[],
    )


@pytest.fixture
def standard_vat_invoice():
    """
    Full invoice with one Band B (Standard VAT 16%) item.
    Tax band mapping per KRA VSCU/OSCU Specification v2.0 §4.1:
      B = Standard VAT 16% (NOT A — the mapping is counterintuitive, see CLAUDE.md)
    Item: 1 × KShs 1,160.00 inclusive → taxable 1,000.00 + tax 160.00
    """
    from kra_etims.models import SaleInvoice, ItemDetail, TaxType
    item = ItemDetail(
        itemCd="ITM-B-001",
        itemNm="Standard VAT Item",
        qty=Decimal("1"),
        uprc=Decimal("1160.00"),
        totAmt=Decimal("1160.00"),
        taxblAmt=Decimal("1000.00"),
        taxAmt=Decimal("160.00"),
        taxTyCd=TaxType.B,
    )
    return SaleInvoice(
        tin="A000111222B",
        bhfId="00",
        invcNo="INV-VAT-001",
        custNm="VAT Customer",
        confirmDt="20260420120000",
        totItemCnt=1,
        totTaxblAmt=Decimal("1000.00"),
        totTaxAmt=Decimal("160.00"),
        totAmt=Decimal("1160.00"),
        itemList=[item],
    )
