from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail, TaxType, ReceiptLabel

# Initialize Authority Client
client = KRAeTIMSClient(
    client_id="YOUR_TIaaS_ID",
    client_secret="YOUR_TIaaS_SECRET"
)

# Construct Compliant Sale (Category 6)
invoice = SaleInvoice(
    tin="P051234567X",
    bhfId="00",
    invcNo="INV-2026-001",
    custNm="John Doe Enterprises",
    rcptLbel=ReceiptLabel.NORMAL,
    confirmDt="20260221113000",
    totItemCnt=1,
    totTaxblAmt=1000.00,
    totTaxAmt=160.00,
    totAmt=1160.00,
    itemList=[
        ItemDetail(
            itemCd="HS847130",
            itemNm="MacBook Pro M3",
            uprc=1000.00,
            qty=1,
            taxTyCd=TaxType.A,
            taxblAmt=1000.00,
            taxAmt=160.00,
            totAmt=1160.00
        )
    ]
)

# Submit to TIaaS Middleware
try:
    response = client.submit_sale(invoice)
    print(f"QR Code: {response['qrCode']}")
    print(f"Signature: {response['invoiceSignature']}")
except Exception as e:
    print(f"Integration Error: {e}")
