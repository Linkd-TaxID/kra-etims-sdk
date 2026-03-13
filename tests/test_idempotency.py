import pytest
import responses
import requests
import httpx
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
from kra_etims.models import SaleInvoice, TaxType

@responses.activate
def test_sync_idempotency_header_injection():
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999
    
    responses.add(responses.POST, "https://api.test/v2/etims/sale", status=200, json={})
    
    invoice = SaleInvoice(
        tin="P1", bhfId="00", invcNo="1", custNm="C", confirmDt="20240101000000",
        totItemCnt=0, totTaxblAmt=Decimal("0"), totTaxAmt=Decimal("0"), totAmt=Decimal("0"),
        itemList=[]
    )
    
    client.submit_sale(invoice, idempotency_key="unique_123")
    
    assert responses.calls[0].request.headers["X-TIaaS-Idempotency-Key"] == "unique_123"
    print("\n✅ Sync: X-TIaaS-Idempotency-Key found in headers.")

def test_sync_ambiguous_state_on_post():
    from unittest.mock import patch
    client = KRAeTIMSClient("id", "secret", "https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    # `responses` wraps body exceptions in ConnectionError, so we patch the
    # session directly to inject a genuine ReadTimeout (request sent, no reply).
    with patch.object(
        client._session, "request",
        side_effect=requests.exceptions.ReadTimeout("response never arrived"),
    ):
        invoice = SaleInvoice(
            tin="P1", bhfId="00", invcNo="1", custNm="C", confirmDt="20240101000000",
            totItemCnt=0, totTaxblAmt=Decimal("0"), totTaxAmt=Decimal("0"), totAmt=Decimal("0"),
            itemList=[]
        )
        exc = pytest.raises(TIaaSAmbiguousStateError, client.submit_sale, invoice)
    print("✅ Sync: ReadTimeout during POST raised TIaaSAmbiguousStateError.")

@responses.activate
def test_sync_unavailable_state_on_get():
    client = KRAeTIMSClient("id", "secret", "https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999
    
    responses.add(responses.GET, "https://api.test/v2/etims/compliance/P1", body=requests.exceptions.ConnectionError("Down"))
    
    with pytest.raises(TIaaSUnavailableError):
        client.check_compliance("P1")
    print("✅ Sync: ConnectionError during GET raised TIaaSUnavailableError.")

@pytest.mark.asyncio
async def test_async_ambiguous_state(httpx_mock):
    async with AsyncKRAeTIMSClient("id", "secret", base_url="https://api.test") as client:
        client._access_token = "mock"
        client._token_expiry = 9999999999
        
        # httpx RequestError is ambiguous for POST
        # We use a specific URL to avoid matching other requests if any
        httpx_mock.add_exception(
            httpx.ReadTimeout("Timeout"), 
            method="POST", 
            url="https://api.test/v2/etims/sale"
        )
        
        invoice = SaleInvoice(
            tin="P1", bhfId="00", invcNo="1", custNm="C", confirmDt="20240101000000",
            totItemCnt=0, totTaxblAmt=Decimal("0"), totTaxAmt=Decimal("0"), totAmt=Decimal("0"),
            itemList=[]
        )
        
        with pytest.raises(TIaaSAmbiguousStateError):
            await client.submit_sale(invoice)
        print("✅ Async: ReadTimeout during POST raised TIaaSAmbiguousStateError.")
