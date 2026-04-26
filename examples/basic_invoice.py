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
# KRA eTIMS Tax Bands (VSCU/OSCU Specification v2.0 §4.1):
#   A = 0%  Exempt         (no input credit — e.g. basic food, medical supplies)
#   B = 16% Standard VAT   (most goods and services)
#   C = 0%  Zero-Rated     (exports, certain zero-rated supplies; input credit allowed)
#   D = 0%  Non-VAT        (supplies outside the VAT Act entirely)
#   E = 8%  Special Rate   (petroleum/LPG — verify current rate with KRA post-Finance Act 2023;
#                           update ETIMS_TAX_RATE_E env var if KRA confirms the rate changed)
items = [
    calculate_item("MacBook Pro M3",  "HS847130", 5800, "B"),  # Band B — 16% Standard VAT
    calculate_item("Maize Flour 2kg", "HS110100",  200, "A"),  # Band A — 0% Exempt (no input credit)
    calculate_item("Diesel 1L",       "HS270900",  216, "E"),  # Band E — 8% Special Rate (petroleum)
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
