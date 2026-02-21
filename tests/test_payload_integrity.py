import pytest
import responses
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice

@responses.activate
def test_payload_integrity_excludes_none():
    """
    Scenario: Submit a SaleInvoice with optional fields (like orgInvcNo) set to None.
    Assertion: The raw JSON payload must NOT contain the key "orgInvcNo".
    """
    client = KRAeTIMSClient("id", "secret", "https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999
    
    responses.add(responses.POST, "https://api.test/v2/etims/sale", status=200, json={})
    
    # invoice without orgInvcNo and custPin
    invoice = SaleInvoice(
        tin="P1", bhfId="00", invcNo="1", custNm="C", confirmDt="20240101000000",
        totItemCnt=0, totTaxblAmt=Decimal("0"), totTaxAmt=Decimal("0"), totAmt=Decimal("0"),
        itemList=[]
    )
    
    client.submit_sale(invoice)
    
    import json
    payload = json.loads(responses.calls[0].request.body)
    
    # Assertions
    assert "orgInvcNo" not in payload
    assert "custPin" not in payload
    assert payload["invcNo"] == "1"
    print("\n✅ Payload Integrity: exclude_none=True verified.")
