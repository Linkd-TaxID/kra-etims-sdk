import asyncio
import pytest
import httpx
from decimal import Decimal
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.models import SaleInvoice


@pytest.mark.asyncio
async def test_async_authenticate_task_safety(httpx_mock):
    """
    50 concurrent async tasks all hit _authenticate with no token set.
    The asyncio.Lock double-checked pattern must result in exactly one
    call to /oauth/token regardless of concurrency.
    """
    client = AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    )

    httpx_mock.add_response(
        method="POST",
        url="https://api.test.co.ke/oauth/token",
        json={"access_token": "async_mock_token", "expires_in": 3600},
        status_code=200,
    )

    async def task():
        await client._authenticate()
        return client._access_token

    results = await asyncio.gather(*[task() for _ in range(50)])

    auth_requests = [
        r for r in httpx_mock.get_requests() if "/oauth/token" in str(r.url)
    ]
    assert len(auth_requests) == 1, (
        f"Expected exactly 1 OAuth call, got {len(auth_requests)}. "
        "asyncio.Lock is broken."
    )
    assert all(r == "async_mock_token" for r in results)
    await client.aclose()


@pytest.mark.asyncio
async def test_async_idempotency_key_passed(httpx_mock):
    """
    X-TIaaS-Idempotency-Key must be present in the outgoing async request.
    """
    client = AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test.co.ke/v2/etims/sale",
        json={"status": "success"},
        status_code=200,
    )

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-ASYNC-002",
        custNm="Async Customer",
        confirmDt="20240221120000",
        totItemCnt=0,
        totTaxblAmt=Decimal("0.00"),
        totTaxAmt=Decimal("0.00"),
        totAmt=Decimal("0.00"),
        itemList=[],
    )

    await client.submit_sale(invoice, idempotency_key="async-req-123")

    request = httpx_mock.get_request()
    assert request.headers["X-TIaaS-Idempotency-Key"] == "async-req-123"
    await client.aclose()
