import pytest
from pydantic import ValidationError
from kra_etims.models import SaleInvoice, ItemDetail, TaxType, ReceiptLabel

def test_schema_forbids_extra_fields():
    """
    Scenario: ERP developer attempts to inject unauthorized fields (schema evasion).
    Assertion: Pydantic must raise ValidationError because extra='forbid'.
    """
    # Attempt to inject foreign currency keys and extra levies
    with pytest.raises(ValidationError) as excinfo:
        SaleInvoice(
            tin="P000000000X",
            bhfId="00",
            invcNo="INV-PRO-001",
            custNm="Test Customer",
            confirmDt="20240221120000",
            totItemCnt=0,
            totTaxblAmt=0.0,
            totTaxAmt=0.0,
            totAmt=0.0,
            itemList=[],
            # Unauthorized fields
            currCd="USD", 
            exRt=130.50,
            tourismLevy=2.0
        )
    
    assert "Extra inputs are not permitted" in str(excinfo.value)
    print("\n✅ Success: Strict schema prevented unauthorized field injection.")

def test_item_detail_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ItemDetail(
            itemCd="ITM-X",
            itemNm="Test",
            qty=1,
            uprc=100,
            totAmt=100,
            taxTyCd=TaxType.A,
            taxblAmt=100,
            taxAmt=0,
            customLevy=10.0 # extra field
        )
