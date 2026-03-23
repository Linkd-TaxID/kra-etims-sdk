import pytest
import responses
import requests
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import KRAConnectivityTimeoutError, KRAeTIMSError
from kra_etims.models import SaleInvoice, ReceiptLabel, TaxType

@responses.activate
def test_vscu_503_mapping():
    """
    Scenario: KRA GavaConnect returns 503 Service Unavailable (VSCU offline ceiling).
    Assertion: SDK maps 503 correctly to KRAConnectivityTimeoutError.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    responses.add(
        responses.GET,
        "https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status=503
    )

    with pytest.raises(KRAConnectivityTimeoutError):
        client.check_compliance("P000000000X")

@responses.activate
def test_flush_offline_queue_sends_exact_requests():
    """
    Scenario: 50 offline invoices are flushed once connectivity is restored.
    Assertion: flush_offline_queue executes exactly 50 POST requests.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    # Mock 50 successful POST requests
    responses.add(
        responses.POST,
        "https://api.test.co.ke/v2/etims/sale",
        json={"status": "success"},
        status=200
    )

    # Create 50 mock invoices
    invoices = []
    for i in range(50):
        invoices.append(SaleInvoice(
            tin="P000000000X",
            bhfId="00",
            invcNo=f"INV-OFFLINE-{i}",
            custNm="Offline Customer",
            confirmDt="20240221120000",
            totItemCnt=0,
            totTaxblAmt=0.0,
            totTaxAmt=0.0,
            totAmt=0.0,
            itemList=[]
        ))

    results = client.flush_offline_queue(invoices)

    # Assertions
    assert len(results) == 50
    # Check that exactly 50 POST requests were made
    post_requests = [r for r in responses.calls if r.request.method == "POST"]
    assert len(post_requests) == 50
    assert all(r["status"] == "success" for r in results)

@pytest.mark.asyncio
async def test_async_vscu_503_mapping(httpx_mock):
    """
    Async Scenario: KRA GavaConnect returns 503.
    """
    async with AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    ) as client:
        client._access_token = "mock_token"
        client._token_expiry = 9999999999

        httpx_mock.add_response(
            method="GET",
            url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
            status_code=503
        )

        with pytest.raises(KRAConnectivityTimeoutError):
            await client.check_compliance("P000000000X")

@pytest.mark.asyncio
async def test_async_flush_offline_queue(httpx_mock):
    """
    Async Scenario: Flushing 50 invoices.
    """
    async with AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    ) as client:
        client._access_token = "mock_token"
        client._token_expiry = 9999999999

        # Add 50 responses
        for _ in range(50):
            httpx_mock.add_response(
                method="POST",
                url="https://api.test.co.ke/v2/etims/sale",
                json={"status": "success"},
                status_code=200
            )

        invoices = [SaleInvoice(
            tin="P000000000X",
            bhfId="00",
            invcNo=f"INV-ASYNC-OFFLINE-{i}",
            custNm="Offline Customer",
            confirmDt="20240221120000",
            totItemCnt=0,
            totTaxblAmt=0.0,
            totTaxAmt=0.0,
            totAmt=0.0,
            itemList=[]
        ) for i in range(50)]

        results = await client.flush_offline_queue(invoices)

        assert len(results) == 50
        post_requests = [r for r in httpx_mock.get_requests() if r.method == "POST"]
        assert len(post_requests) == 50


# ---------------------------------------------------------------------------
# Non-503 HTTP errors must be converted — URL must not appear in str(exc)
# ---------------------------------------------------------------------------

@responses.activate
def test_non_503_http_error_raises_kra_etims_error_without_url():
    """
    A 422 from the middleware must raise KRAeTIMSError with only the status
    code in the message. The request URL (which may contain a KRA PIN in the
    path) must never appear in str(exc).
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    responses.add(
        responses.GET,
        "https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status=422,
    )

    with pytest.raises(KRAeTIMSError) as exc_info:
        client.check_compliance("P000000000X")

    error_message = str(exc_info.value)
    assert "422" in error_message
    assert "compliance" not in error_message
    assert "P000000000X" not in error_message


@pytest.mark.asyncio
async def test_async_non_503_http_error_raises_kra_etims_error_without_url(httpx_mock):
    """
    Async equivalent: 422 must raise KRAeTIMSError; URL must not be in str(exc).
    """
    async with AsyncKRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    ) as client:
        client._access_token = "mock_token"
        client._token_expiry = 9999999999

        httpx_mock.add_response(
            method="GET",
            url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
            status_code=422,
        )

        with pytest.raises(KRAeTIMSError) as exc_info:
            await client.check_compliance("P000000000X")

        error_message = str(exc_info.value)
        assert "422" in error_message
        assert "compliance" not in error_message
        assert "P000000000X" not in error_message
