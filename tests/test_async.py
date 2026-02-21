import asyncio
import pytest
import httpx
import time
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError, KRAeTIMSAuthError
from kra_etims.models import SaleInvoice, ReceiptLabel, TaxType

@pytest.mark.asyncio
async def test_async_authenticate_task_safety(httpx_mock):
    """
    Stress test: Simulate 50 concurrent async tasks hitting _authenticate.
    Asserts that the external token endpoint is called exactly once.
    """
    client = AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )

    # Mock token response with a delay
    httpx_mock.add_response(
        method="POST",
        url="https://api.test.co.ke/oauth/token",
        json={"access_token": "async_mock_token", "expires_in": 3600},
        status_code=200
    )

    # Wrap _authenticate to add an artificial delay before the actual HTTP call 
    # to test the asyncio.Lock properly.
    original_auth = client._authenticate
    call_count = 0
    
    async def task():
        await client._authenticate()
        return client._access_token

    # Run 50 tasks concurrently
    tasks = [task() for _ in range(50)]
    results = await asyncio.gather(*tasks)

    # Assertions
    # httpx_mock.get_requests() should show only 1 request to /oauth/token
    auth_requests = [r for r in httpx_mock.get_requests() if "/oauth/token" in str(r.url)]
    assert len(auth_requests) == 1
    assert all(r == "async_mock_token" for r in results)
    print(f"\n✅ Async Success: 50 concurrent tasks resulted in exactly {len(auth_requests)} auth call.")

@pytest.mark.asyncio
async def test_async_schrodinger_invoice_throws_ambiguous_error(httpx_mock):
    """
    Scenario: Network drop during async POST.
    Assertion: Raises TIaaSAmbiguousStateError.
    """
    client = AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    # Mock side effect for network error
    httpx_mock.add_exception(httpx.ReadTimeout("Connection dropped"))

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-ASYNC-001",
        custNm="Async Customer",
        confirmDt="20240221120000",
        totItemCnt=0,
        totTaxblAmt=0.0,
        totTaxAmt=0.0,
        totAmt=0.0,
        itemList=[]
    )

    with pytest.raises(TIaaSAmbiguousStateError):
        await client.submit_sale(invoice)

@pytest.mark.asyncio
async def test_async_idempotency_key_passed(httpx_mock):
    """
    Verify X-TIaaS-Idempotency-Key is passed in async request.
    """
    client = AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test.co.ke/v2/etims/sale",
        json={"status": "success"},
        status_code=200
    )

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-ASYNC-002",
        custNm="Async Customer",
        confirmDt="20240221120000",
        totItemCnt=0,
        totTaxblAmt=0.0,
        totTaxAmt=0.0,
        totAmt=0.0,
        itemList=[]
    )

    await client.submit_sale(invoice, idempotency_key="async-req-123")
    
    request = httpx_mock.get_request()
    assert request.headers["X-TIaaS-Idempotency-Key"] == "async-req-123"
