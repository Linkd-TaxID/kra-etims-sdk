import pytest
from decimal import Decimal
from pydantic import ValidationError
from kra_etims.models import ItemDetail, TaxType


def test_pydantic_rejects_float_drift_on_real_model():
    """
    The ItemDetail validator compares totAmt directly against qty * uprc
    (quantized to 2dp). Decimal("300.30000000000004") != Decimal("300.30"),
    so the validator raises — catching any float-origin drift before it
    reaches the KRA signing endpoint.
    """
    with pytest.raises(ValidationError) as exc_info:
        ItemDetail(
            itemCd="ITM-MATH",
            itemNm="Math Item",
            qty=Decimal("3"),
            uprc=Decimal("100.1"),
            totAmt=Decimal("300.30000000000004"),  # float-origin drift
            taxblAmt=Decimal("300.30"),
            taxAmt=Decimal("0.00"),
            taxTyCd=TaxType.C,
        )
    assert "Math Error" in str(exc_info.value)


def test_decimal_fix_resolves_drift_on_real_model():
    """
    Correctly quantized Decimal inputs satisfy all validators without error.
    """
    item = ItemDetail(
        itemCd="ITM-FIXED",
        itemNm="Fixed Item",
        qty=Decimal("3"),
        uprc=Decimal("100.1"),
        totAmt=Decimal("300.30"),
        taxblAmt=Decimal("300.30"),
        taxAmt=Decimal("0.00"),
        taxTyCd=TaxType.C,
    )
    assert item.totAmt == Decimal("300.30")
