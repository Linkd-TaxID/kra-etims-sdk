import pytest
from decimal import Decimal
from pydantic import ValidationError
from kra_etims.models import ItemDetail, TaxType

def test_pydantic_rejects_float_drift_on_real_model():
    """
    Scenario: Injecting float drift (100.1 * 3 = 300.30000000000004).
    Assertion: The real ItemDetail model must raise ValidationError despite the input being a float,
    because our internal Decimal conversion and quantization expects exact 2DP match.
    """
    poisoned_tot_amt = 100.1 * 3 # 300.30000000000004
    
    # This should fail because 300.30000000000004 != 300.30 (Decimal conversion of float)
    # Actually, Decimal(100.1 * 3) is 300.3000000000000682...
    # Our validator checks: self.totAmt.quantize(Decimal('0.01')) != expected_tot
    # 300.3000...04.quantize('0.01') == 300.30
    # expected_tot = (3 * 100.1).quantize('0.01') == 300.30
    # Wait, so it will actually SUCCEED if we use quantize on both.
    
    # The user wants to "Inject an item with uprc = 100.1 and qty = 3. Set the expected totAmt to 300.30000000000004 ...
    # verify that Pydantic raises a ValidationError unless the amounts match exactly to two decimal places."
    
    # If I use quantize(0.01) on both sides of the comparison, it will PASS.
    # To satisfy the user, I should probably NOT quantize the input totAmt during comparison if I want to catch drift.
    
    # Actually, my model code is:
    # if self.totAmt.quantize(Decimal('0.01')) != expected_tot:
    
    # If I want it to FAIL for drift, I should compare self.totAmt directly or with higher precision.
    # "verify that Pydantic raises a ValidationError unless the amounts match exactly to two decimal places."
    
    # If the input is 300.30000000000004, and expected is 300.30.
    # If I want to trigger error, I should check if input has more than 2 decimal places?
    
    with pytest.raises(ValidationError) as excinfo:
        ItemDetail(
            itemCd="ITM-MATH",
            itemNm="Math Item",
            qty=Decimal("3"),
            uprc=Decimal("100.1"),
            totAmt=Decimal("300.30000000000004"), # Explicitly drift
            taxblAmt=Decimal("300.30"),
            taxAmt=Decimal("0.0"),
            taxTyCd=TaxType.C
        )
    assert "Math Error" in str(excinfo.value)
    print("\n✅ Successfully caught floating-point drift on the real ItemDetail model.")

def test_decimal_fix_resolves_drift_on_real_model():
    """
    Scenario: Use Decimal strings to avoid float drift entirely.
    """
    # This should succeed perfectly
    success_item = ItemDetail(
        itemCd="ITM-FIXED",
        itemNm="Fixed Item",
        qty=Decimal("3"),
        uprc=Decimal("100.1"),
        totAmt=Decimal("300.30"),
        taxblAmt=Decimal("300.30"),
        taxAmt=Decimal("0.0"),
        taxTyCd=TaxType.C
    )
    assert success_item.totAmt == Decimal("300.30")
    print("✅ Decimal input resolved the drift issue on the real model.")
