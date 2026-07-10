import pytest
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import KRAConnectivityTimeoutError, KRAeTIMSError, OSCUUnavailableError
from kra_etims.models import SaleInvoice, ReceiptLabel, TaxType


def test_vscu_503_mapping(httpx_mock):
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

    httpx_mock.add_response(
        method="GET",
        url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status_code=503,
    )

    with pytest.raises(KRAConnectivityTimeoutError):
        client.check_compliance("P000000000X")


def test_oscu_503_mapping_is_not_confused_with_vscu_ceiling(httpx_mock):
    """
    Scenario: TIaaS returns 503 with an `oscu_code` body (GlobalExceptionHandler.
    handleOscuSigning) -- an OSCU transient failure, NOT the 24-hour VSCU ceiling
    (OSCU has no such ceiling; this is what _raise_for_503 exists to discriminate).
    Assertion: raises OSCUUnavailableError, not KRAConnectivityTimeoutError, and
    carries the raw oscu_code through for programmatic handling.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="GET",
        url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status_code=503,
        json={
            "type": "https://docs.taxid.co.ke/errors/oscu-signing",
            "title": "OSCU Temporarily Unavailable",
            "status": 503,
            "detail": "OSCU error 894 — An error regarding server communication occurred",
            "oscu_code": "894",
            "transient": True,
        },
    )

    with pytest.raises(OSCUUnavailableError) as exc_info:
        client.check_compliance("P000000000X")
    assert exc_info.value.oscu_code == "894"


def test_vscu_503_with_json_body_but_no_oscu_code_still_raises_connectivity_timeout(httpx_mock):
    """
    Scenario: a 503 body IS present (VSCU's own ProblemDetail shape has
    `vscu_code`, not `oscu_code`) -- must not be misread as an OSCU failure.
    Assertion: still raises KRAConnectivityTimeoutError, matching pre-OSCU behavior.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="GET",
        url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status_code=503,
        json={
            "type": "https://docs.taxid.co.ke/errors/vscu-signing",
            "title": "VSCU Temporarily Unavailable",
            "status": 503,
            "vscu_code": "90",
            "transient": True,
        },
    )

    with pytest.raises(KRAConnectivityTimeoutError):
        client.check_compliance("P000000000X")


def test_flush_offline_queue_sends_exact_requests(httpx_mock):
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

    for _ in range(50):
        httpx_mock.add_response(
            method="POST",
            url="https://api.test.co.ke/v2/etims/sale",
            json={"resultCd": "000", "resultMsg": "It is succeeded", "data": {"status": "success"}},
            status_code=200,
        )

    invoices = []
    for i in range(50):
        invoices.append(SaleInvoice(
            tin="P000000000X",
            bhfId="00",
            invcNo=f"INV-OFFLINE-{i}",
            custNm="Offline Customer",
            confirmDt="20240221120000",
            totItemCnt=0,
            totTaxblAmt=Decimal("0.00"),
            totTaxAmt=Decimal("0.00"),
            totAmt=Decimal("0.00"),
            itemList=[]
        ))

    results = client.flush_offline_queue(invoices)

    post_requests = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(post_requests) == 50
    assert len(results) == 50

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
            totTaxblAmt=Decimal("0.00"),
            totTaxAmt=Decimal("0.00"),
            totAmt=Decimal("0.00"),
            itemList=[]
        ) for i in range(50)]

        results = await client.flush_offline_queue(invoices)

        assert len(results) == 50
        post_requests = [r for r in httpx_mock.get_requests() if r.method == "POST"]
        assert len(post_requests) == 50


# ---------------------------------------------------------------------------
# Non-503 HTTP errors must be converted — URL must not appear in str(exc)
# ---------------------------------------------------------------------------

def test_non_503_http_error_raises_kra_etims_error_without_url(httpx_mock):
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

    httpx_mock.add_response(
        method="GET",
        url="https://api.test.co.ke/v2/etims/compliance/P000000000X",
        status_code=422,
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
