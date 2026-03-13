import time
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail, TaxType, ReceiptLabel

def run_test():
    print("--- Initiating TIaaS / KRA VSCU E2E Test ---")
    
    # 1. Initialize Client (Pointing to Local Middleware with the Raw Key)
    client = KRAeTIMSClient(
        client_id="not_used",
        client_secret="not_used",
        api_key="6c8ed76ce581887c47d518ce6472de47a7331b114a78480a01ee9708659af5b7",
        base_url="http://localhost:8080",
    )
    
    # 2. Build the Item (Tax Band A - 16%)
    item = ItemDetail(
        itemCd="ITEM-001",
        itemNm="Software Engineering Services",
        qty=Decimal("1.0"),
        uprc=Decimal("1000.0"),
        totAmt=Decimal("1000.0"),
        taxTyCd=TaxType.A,
        taxblAmt=Decimal("862.07"),
        taxAmt=Decimal("137.93")
    )
    
    # 3. Build the Invoice
    invoice = SaleInvoice(
        tin="A008697103A",  # Must match the PIN you initialized with
        bhfId="00",
        invcNo=f"INV-{int(time.time())}",
        custNm="Test Client",
        confirmDt=time.strftime("%Y%m%d%H%M%S"),
        totItemCnt=1,
        totTaxblAmt=Decimal("862.07"),
        totTaxAmt=Decimal("137.93"),
        totAmt=Decimal("1000.0"),
        itemList=[item],
        rcptLbel=ReceiptLabel.NORMAL
    )

    print("Submitting payload to middleware...")
    
    # 4. Execute and catch errors
    try:
        response = client.submit_sale(invoice)
        print("\n✅ SUCCESS: VSCU Cryptographic Signature Acquired!")
        print(f"Status            : {response.get('status')}")
        print(f"Invoice Signature : {response.get('invoiceSignature')}")
        # Fields present when connected to live VSCU:
        print(f"Receipt No        : {response.get('rcptNo')}")
        print(f"SDC ID            : {response.get('sdcId')}")
        print(f"QR Payload        : {response.get('kraQrPayload')}")
    except Exception as e:
        print(f"\n❌ TRANSACTION FAILED: {e}")

if __name__ == "__main__":
    run_test()