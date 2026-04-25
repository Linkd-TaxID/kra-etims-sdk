import pytest
import json
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice


def test_payload_integrity_excludes_none(httpx_mock):
    """
    Submit a SaleInvoice with optional fields (orgInvcNo, custPin) unset.
    The raw JSON payload must not contain those keys (exclude_none=True).
    """
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/v2/etims/sale",
        json={"resultCd": "000", "resultMsg": "It is succeeded", "data": {}},
        status_code=200,
    )

    invoice = SaleInvoice(
        tin="P1", bhfId="00", invcNo="1", custNm="C", confirmDt="20240101000000",
        totItemCnt=0, totTaxblAmt=Decimal("0"), totTaxAmt=Decimal("0"), totAmt=Decimal("0"),
        itemList=[]
    )

    client.submit_sale(invoice)

    sent = httpx_mock.get_requests()[0]
    payload = json.loads(sent.content)

    assert "orgInvcNo" not in payload
    assert "custPin" not in payload
    assert payload["invcNo"] == "1"
