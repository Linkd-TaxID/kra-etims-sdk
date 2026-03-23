import pytest
import responses
import requests
import httpx
from decimal import Decimal
from unittest.mock import patch, MagicMock
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
from kra_etims.models import SaleInvoice, TaxType


def _minimal_invoice():
    return SaleInvoice(
        tin="P1", bhfId="00", invcNo="1", custNm="C",
        confirmDt="20240101000000",
        totItemCnt=0,
        totTaxblAmt=Decimal("0"),
        totTaxAmt=Decimal("0"),
        totAmt=Decimal("0"),
        itemList=[],
    )


@responses.activate
def test_sync_idempotency_header_injection():
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    responses.add(responses.POST, "https://api.test/v2/etims/sale", status=200, json={})

    client.submit_sale(_minimal_invoice(), idempotency_key="unique_123")

    assert responses.calls[0].request.headers["X-TIaaS-Idempotency-Key"] == "unique_123"


def test_sync_ambiguous_state_on_post():
    """
    A ReadTimeout on a POST means the request was sent but no response
    arrived. The SDK must raise TIaaSAmbiguousStateError (not swallow it).
    """
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    mock_session = MagicMock()
    mock_session.request.side_effect = requests.exceptions.ReadTimeout(
        "response never arrived"
    )

    with patch.object(client, "_get_session", return_value=mock_session):
        with pytest.raises(TIaaSAmbiguousStateError):
            client.submit_sale(_minimal_invoice())


@responses.activate
def test_sync_unavailable_state_on_get():
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    responses.add(
        responses.GET,
        "https://api.test/v2/etims/compliance/P1",
        body=requests.exceptions.ConnectionError("Down"),
    )

    with pytest.raises(TIaaSUnavailableError):
        client.check_compliance("P1")


@pytest.mark.asyncio
async def test_async_ambiguous_state(httpx_mock):
    async with AsyncKRAeTIMSClient("id", "secret", base_url="https://api.test") as client:
        client._access_token = "mock"
        client._token_expiry = 9999999999

        httpx_mock.add_exception(
            httpx.ReadTimeout("Timeout"),
            method="POST",
            url="https://api.test/v2/etims/sale",
        )

        with pytest.raises(TIaaSAmbiguousStateError):
            await client.submit_sale(_minimal_invoice())
