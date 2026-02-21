import pytest
import responses
import requests
from kra_etims.client import KRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError
from kra_etims.models import SaleInvoice, ReceiptLabel, TaxType

@responses.activate
def test_schrodinger_invoice_throws_ambiguous_error():
    """
    Scenario: The TCP connection drops after the request bytes are sent 
    but before the KRA response is read.
    Assertion: The SDK must raise TIaaSAmbiguousStateError for POST requests.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    # Bypass auth for this test
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    # Mock a connection error during the request
    responses.add(
        responses.POST,
        "https://api.test.co.ke/v2/etims/sale",
        body=requests.exceptions.ConnectionError("Connection dropped after sending bytes")
    )

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-001",
        custNm="Test Customer",
        rcptLbel=ReceiptLabel.NORMAL,
        confirmDt="20240221120000",
        totItemCnt=1,
        totTaxblAmt=1000.0,
        totTaxAmt=160.0,
        totAmt=1160.0,
        itemList=[
            {
                "itemCd": "ITM-001",
                "itemNm": "Test Item",
                "qty": 1.0,
                "uprc": 1160.0,
                "totAmt": 1160.0,
                "taxTyCd": TaxType.A,
                "taxblAmt": 1000.0,
                "taxAmt": 160.0
            }
        ]
    )

    with pytest.raises(TIaaSAmbiguousStateError) as excinfo:
        client.submit_sale(invoice)
    
    assert "TIaaS Ambiguous State" in str(excinfo.value)

@responses.activate
def test_idempotency_key_is_passed_in_headers():
    """
    Scenario: User provides an idempotency key to prevent double taxation.
    Assertion: The SDK must include X-TIaaS-Idempotency-Key in the request headers.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    def request_callback(request):
        # Verify header exists
        assert "X-TIaaS-Idempotency-Key" in request.headers
        assert request.headers["X-TIaaS-Idempotency-Key"] == "unique-req-123"
        return (200, {}, '{"status": "success"}')

    responses.add_callback(
        responses.POST,
        "https://api.test.co.ke/v2/etims/sale",
        callback=request_callback,
        content_type='application/json',
    )

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-001",
        custNm="Test Customer",
        confirmDt="20240221120000",
        totItemCnt=1,
        totTaxblAmt=1000.0,
        totTaxAmt=160.0,
        totAmt=1160.0,
        itemList=[
            {
                "itemCd": "ITM-001",
                "itemNm": "Test Item",
                "qty": 1.0,
                "uprc": 1160.0,
                "totAmt": 1160.0,
                "taxTyCd": TaxType.A,
                "taxblAmt": 1000.0,
                "taxAmt": 160.0
            }
        ]
    )

    client.submit_sale(invoice, idempotency_key="unique-req-123")
