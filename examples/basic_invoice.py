"""
TaxID SDK — Zero-Math Invoice Example
======================================
Pass item names, retail prices, and KRA tax bands.
The SDK computes every exclusive amount, VAT split, and invoice total
required by the KRA eTIMS v2.0 spec — no manual arithmetic.

confirmDt format: yyyyMMddHHmmss  (e.g. "20260311120000" = 2026-03-11 12:00:00)
"""

from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item, build_invoice_totals

client = KRAeTIMSClient(
    client_id="YOUR_TIIMS_CLIENT_ID",
    client_secret="YOUR_TIIMS_CLIENT_SECRET",
)

# Pass retail price + tax band — the SDK handles all VAT arithmetic.
items = [
    calculate_item("MacBook Pro M3",  "HS847130", 5800, "A"),   # 16% VAT  → Band A
    calculate_item("Maize Flour 2kg", "HS110100",  200, "D"),   # 0% Exempt → Band D
    calculate_item("Diesel 1L",       "HS270900",  216, "B"),   # 0% Zero-Rated → Band B
]

invoice = SaleInvoice(
    tin="P051234567X",
    bhfId="00",
    invcNo="INV-2026-001",
    custNm="Acacia Enterprises Ltd",
    confirmDt="20260311120000",   # yyyyMMddHHmmss
    itemList=items,
    **build_invoice_totals(items),   # totItemCnt, totTaxblAmt, totTaxAmt, totAmt
)

try:
    response = client.submit_sale(invoice, idempotency_key="INV-2026-001")
    print(f"Signature : {response['invoiceSignature']}")
    print(f"Receipt No: {response.get('rcptNo')}")
    print(f"QR Code   : {response.get('qrCode')}")
except Exception as e:
    print(f"Error: {e}")
