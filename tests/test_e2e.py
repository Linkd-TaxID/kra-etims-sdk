"""
End-to-end smoke test against a live TIaaS middleware instance.

Skipped in CI and normal test runs. To run manually:
    pytest tests/test_e2e.py -m integration -s

Requires:
    - TIaaS middleware running at localhost:8080
    - A valid API key seeded in that instance
"""
import time
import pytest
from decimal import Decimal

from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail, TaxType, ReceiptLabel


@pytest.mark.skip(reason="Integration test: requires live TIaaS middleware at localhost:8080")
def test_submit_sale_live():
    client = KRAeTIMSClient(
        client_id="not_used",
        client_secret="not_used",
        api_key="6c8ed76ce581887c47d518ce6472de47a7331b114a78480a01ee9708659af5b7",
        base_url="http://localhost:8080",
    )

    item = ItemDetail(
        itemCd="ITEM-001",
        itemNm="Software Engineering Services",
        qty=Decimal("1.00"),
        uprc=Decimal("1000.00"),
        totAmt=Decimal("1000.00"),
        taxTyCd=TaxType.A,
        taxblAmt=Decimal("862.07"),
        taxAmt=Decimal("137.93"),
    )

    invoice = SaleInvoice(
        tin="A008697103A",
        bhfId="00",
        invcNo=f"INV-{int(time.time())}",
        custNm="Test Client",
        confirmDt=time.strftime("%Y%m%d%H%M%S"),
        totItemCnt=1,
        totTaxblAmt=Decimal("862.07"),
        totTaxAmt=Decimal("137.93"),
        totAmt=Decimal("1000.00"),
        itemList=[item],
        rcptLbel=ReceiptLabel.NORMAL,
    )

    response = client.submit_sale(invoice)
    assert response is not None
