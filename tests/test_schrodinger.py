"""
Schrödinger's Invoice — SDK-side test suite.

Scenario: The TIaaS middleware receives a sale submission, the VSCU JAR
signs it (KRA has a fiscal receipt number), but the PostgreSQL commit fails
before the receipt is persisted. The invoice is in superposition:
  - Signed: KRA's eTIMS system has the receipt.
  - Unsigned (from the client's view): no rcptNo was returned.

The SDK must raise TIaaSAmbiguousStateError for any scenario where the
server received the request bytes but did not return a definitive signed
or rejected response. This class covers:
  1. ReadTimeout on POST (request sent, TCP drops before response arrives).
  2. HTTP 500 on POST where middleware body signals the split-brain condition.
  3. Partial response (connection reset mid-response via ReadError).

The client MUST NOT swallow these errors or retry automatically — retrying
a Schrödinger invoice would create a duplicate KRA receipt if the first
signing succeeded. The caller must use an idempotency key and let the
IdempotencyFilter on the server side handle deduplication (TIaaS Spec §4.8).
"""

import pytest
import httpx
from decimal import Decimal
from unittest.mock import patch

from kra_etims.client import KRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
from kra_etims.models import SaleInvoice, ItemDetail, TaxType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "https://api.test.co.ke"
SALE_URL = f"{BASE}/v2/etims/sale"


def _authed_client() -> KRAeTIMSClient:
    client = KRAeTIMSClient("id", "secret", base_url=BASE)
    client._access_token = "mock_bearer_token"
    client._token_expiry = 9_999_999_999
    return client


def _standard_vat_invoice(invc_no: str = "INV-SCHRODINGER-001") -> SaleInvoice:
    """
    Band B (Standard VAT 16%) invoice per KRA VSCU/OSCU Specification v2.0 §4.1.
    B = Standard VAT 16% — NOT A. All amounts use Decimal to prevent float drift.
    1 item × KShs 1,160.00 inclusive → taxable 1,000.00 + tax 160.00.
    """
    item = ItemDetail(
        itemCd="ITM-B-SCHROD-001",
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
        invcNo=invc_no,
        custNm="Schrödinger Customer",
        confirmDt="20260420120000",
        totItemCnt=1,
        totTaxblAmt=Decimal("1000.00"),
        totTaxAmt=Decimal("160.00"),
        totAmt=Decimal("1160.00"),
        itemList=[item],
    )


# ---------------------------------------------------------------------------
# Test 1: ReadTimeout on POST (classic Schrödinger — request sent, no response)
# ---------------------------------------------------------------------------

def test_read_timeout_on_post_raises_ambiguous_not_unavailable():
    """
    TCP connection drops after request bytes leave the socket.
    The VSCU may or may not have signed — the SDK cannot know.

    Must raise TIaaSAmbiguousStateError (not TIaaSUnavailableError).
    TIaaSUnavailableError is for GET-safe failures where the request
    definitely did not reach the server.
    """
    client = _authed_client()

    with patch.object(
        client._http, "request",
        side_effect=httpx.ReadTimeout("response never arrived"),
    ):
        with pytest.raises(TIaaSAmbiguousStateError) as exc_info:
            client.submit_sale(_standard_vat_invoice(), idempotency_key="idem-schrod-timeout-001")

    error_msg = str(exc_info.value)
    assert "Ambiguous" in error_msg or "ambiguous" in error_msg, (
        f"Expected 'Ambiguous' in error message, got: {error_msg!r}"
    )
    assert exc_info.value.idempotency_key == "idem-schrod-timeout-001"


# ---------------------------------------------------------------------------
# Test 2: HTTP 500 on POST — middleware signals split-brain DB failure
# ---------------------------------------------------------------------------

def test_http_500_on_post_raises_ambiguous_state(httpx_mock):
    """
    The TIaaS middleware received the sale, the VSCU signed it, but the
    PostgreSQL commit failed. Middleware returns 500 with a body indicating
    the ambiguous condition.

    The SDK must map POST + 500 to TIaaSAmbiguousStateError, not a generic
    KRAeTIMSError. The distinction: the request reached and was processed by
    the server — the invoice may exist in KRA's system.
    """
    httpx_mock.add_response(
        method="POST",
        url=SALE_URL,
        json={
            "error": "TIaaS Ambiguous State",
            "detail": (
                "VSCU signed the receipt but the database commit failed. "
                "The invoice may exist in KRA eTIMS. "
                "Use the original idempotency key to safely retry."
            ),
            "resultCd": "AMBIGUOUS",
        },
        status_code=500,
    )

    client = _authed_client()
    with pytest.raises(TIaaSAmbiguousStateError) as exc_info:
        client.submit_sale(_standard_vat_invoice(), idempotency_key="idem-schrod-001")

    error_msg = str(exc_info.value)
    assert "Ambiguous" in error_msg or "ambiguous" in error_msg


# ---------------------------------------------------------------------------
# Test 3: Connection reset mid-response (partial data received — indeterminate)
# ---------------------------------------------------------------------------

def test_connection_reset_mid_response_raises_ambiguous():
    """
    The server sent the response headers but the connection reset before
    the body was fully received. The signing outcome is indeterminate.
    Must raise TIaaSAmbiguousStateError, not ConnectionError or generic exception.
    """
    client = _authed_client()

    with patch.object(
        client._http, "request",
        side_effect=httpx.ReadError("Connection broken: incomplete read"),
    ):
        with pytest.raises(TIaaSAmbiguousStateError) as exc_info:
            client.submit_sale(_standard_vat_invoice(), idempotency_key="idem-schrod-reset-001")

    assert exc_info.value.idempotency_key == "idem-schrod-reset-001"


# ---------------------------------------------------------------------------
# Test 4: GET 500 is NOT ambiguous — safe to retry without idempotency
# ---------------------------------------------------------------------------

def test_http_500_on_get_raises_unavailable_not_ambiguous(httpx_mock):
    """
    A 500 on a GET (compliance check) is a server-side error on a read-only
    request. It cannot have a signing side-effect.
    Must raise TIaaSUnavailableError, not TIaaSAmbiguousStateError.
    """
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v2/etims/compliance/A000111222B",
        json={"error": "Internal Server Error"},
        status_code=500,
    )

    client = _authed_client()
    with pytest.raises(TIaaSUnavailableError):
        client.check_compliance("A000111222B")


# ---------------------------------------------------------------------------
# Test 5: Idempotency key on retry — server returns cached result (not re-signed)
# ---------------------------------------------------------------------------

def test_retry_with_idempotency_key_returns_cached_receipt_not_new_signing(httpx_mock):
    """
    After a Schrödinger event, the caller retries with the SAME idempotency key.
    The TIaaS IdempotencyFilter (§4.8) detects the duplicate key and returns
    the previously committed response WITHOUT calling VSCU again.

    This test verifies that the SDK correctly sends X-TIaaS-Idempotency-Key
    on retry, enabling the server-side deduplication.
    """
    client = _authed_client()

    # First call: simulate the ambiguous state (timeout)
    with patch.object(
        client._http, "request",
        side_effect=httpx.ReadTimeout("timeout"),
    ):
        with pytest.raises(TIaaSAmbiguousStateError):
            client.submit_sale(_standard_vat_invoice(), idempotency_key="idem-retry-001")

    # Second call (retry): server returns the cached signed receipt.
    httpx_mock.add_response(
        method="POST",
        url=SALE_URL,
        json={
            "resultCd": "000",
            "resultMsg": "It is succeeded",
            "data": {
                "rcptNo":       "KRACU0100000001/042 NS",
                "sdcId":        "KRACU0100000001",
                "signature":    "V249-J39C-FJ48-HE2W",
                "internalData": "INTR0042ABCDTEST",
                "qrCode":       "20042026#120000#KRACU0100000001#042#INTR0042ABCDTEST#V249-J39C-FJ48-HE2W",
            },
        },
        status_code=200,
    )

    result = client.submit_sale(
        _standard_vat_invoice(), idempotency_key="idem-retry-001"
    )

    # Verify the idempotency key was sent in the retry request
    sent_requests = httpx_mock.get_requests()
    assert len(sent_requests) == 1
    sent_key = sent_requests[0].headers.get("X-TIaaS-Idempotency-Key")
    assert sent_key == "idem-retry-001", (
        f"Retry must send the SAME idempotency key. Got: {sent_key!r}"
    )
