import pytest
import requests
from decimal import Decimal
from unittest.mock import patch, MagicMock
from kra_etims.client import KRAeTIMSClient
from kra_etims.exceptions import TIaaSAmbiguousStateError
from kra_etims.models import SaleInvoice, ReceiptLabel, TaxType


def test_schrodinger_invoice_throws_ambiguous_error():
    """
    TCP connection drops after the request bytes leave the socket but before
    the response arrives (ReadTimeout). The SDK must raise
    TIaaSAmbiguousStateError: the invoice may or may not have been signed.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    mock_session = MagicMock()
    mock_session.request.side_effect = requests.exceptions.ReadTimeout(
        "response never arrived"
    )

    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="INV-001",
        custNm="Test Customer",
        rcptLbel=ReceiptLabel.NORMAL,
        confirmDt="20240221120000",
        totItemCnt=1,
        totTaxblAmt=Decimal("1000.00"),
        totTaxAmt=Decimal("160.00"),
        totAmt=Decimal("1160.00"),
        itemList=[
            {
                "itemCd": "ITM-001",
                "itemNm": "Test Item",
                "qty": Decimal("1"),
                "uprc": Decimal("1160.00"),
                "totAmt": Decimal("1160.00"),
                "taxTyCd": TaxType.A,
                "taxblAmt": Decimal("1000.00"),
                "taxAmt": Decimal("160.00"),
            }
        ],
    )

    with patch.object(client, "_get_session", return_value=mock_session):
        with pytest.raises(TIaaSAmbiguousStateError) as exc_info:
            client.submit_sale(invoice)

    assert "TIaaS Ambiguous State" in str(exc_info.value)
