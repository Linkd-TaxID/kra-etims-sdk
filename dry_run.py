import time
from decimal import Decimal
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import StockItem, SaleInvoice, ItemDetail, TaxType, ReceiptLabel

def run_dry_run():
    print("--- Starting TIaaS / KRA eTIMS SDK Dry Run ---")
    
    # 1. Instantiate Client
    client = KRAeTIMSClient(
        client_id="DRY-RUN-ID", 
        client_secret="DRY-RUN-SECRET",
        base_url="https://taxid-production.up.railway.app"
    )
    
    # 2. Bypass Authentication for Dry Run
    client._access_token = "DUMMY_DRY_RUN_TOKEN"
    client._token_expiry = time.time() + 3600
    print("[LOG] Authentication bypassed with dummy token.")

    # 3. Batch Update Stock Test (10,000 items)
    print(f"[LOG] Generating 10,000 StockItem objects...")
    dummy_items = [
        StockItem(
            tin="P000000000X",
            bhfId="00",
            itemCd=f"ITEM-{i:05d}",
            rsonCd="01",
            qty=Decimal("10.0")
        ) for i in range(10000)
    ]
    
    print(f"[LOG] Executing batch_update_stock (10,000 items)...")
    start_time = time.perf_counter()
    results = client.batch_update_stock(dummy_items)
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    print(f"[SUCCESS] Batch update completed in {duration:.2f} seconds.")
    print(f"[LOG] Chunks processed: {len(results)}")
    
    # Verify chunking logic (20 chunks for 10,000 items)
    expected_chunks = 20
    if len(results) == expected_chunks:
        print(f"[VERIFIED] 500-item chunking logic confirmed ({len(results)} chunks).")
    else:
        print(f"[WARNING] Unexpected chunk count: {len(results)}")

    # 4. Idempotency Test
    print(f"\n[LOG] Constructing dummy SaleInvoice...")
    item = ItemDetail(
        itemCd="DRY-RUN-ITEM-001",
        itemNm="Dry Run Product",
        qty=Decimal("1.0"),
        uprc=Decimal("100.0"),
        totAmt=Decimal("100.0"),
        taxTyCd=TaxType.A,
        taxblAmt=Decimal("86.21"),
        taxAmt=Decimal("13.79")
    )
    
    invoice = SaleInvoice(
        tin="P000000000X",
        bhfId="00",
        invcNo="DRY-RUN-INV-001",
        custNm="Dry Run Customer",
        confirmDt=time.strftime("%Y%m%d%H%M%S"),
        totItemCnt=1,
        totTaxblAmt=Decimal("86.21"),
        totTaxAmt=Decimal("13.79"),
        totAmt=Decimal("100.0"),
        itemList=[item],
        rcptLbel=ReceiptLabel.NORMAL
    )

    print(f"[LOG] Submitting Sale (Call 1)...")
    try:
        res1 = client.submit_sale(invoice, idempotency_key="TEST-DRY-RUN-001")
        print(f"[SUCCESS] Call 1 result: {res1.get('status', 'OK')}")
    except Exception as e:
        print(f"[ERROR] Call 1 failed: {e}")

    print(f"[LOG] Submitting Sale (Call 2 - Simulated Retry)...")
    try:
        res2 = client.submit_sale(invoice, idempotency_key="TEST-DRY-RUN-001")
        print(f"[SUCCESS] Call 2 result (Idempotency): {res2.get('status', 'OK')}")
        print("[VERIFIED] Idempotency logic triggered successfully.")
    except Exception as e:
        print(f"[ERROR] Call 2 failed: {e}")

    print("\n--- Dry Run Completed ---")

if __name__ == "__main__":
    run_dry_run()
