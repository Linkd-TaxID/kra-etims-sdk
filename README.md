# KRA eTIMS SDK (Python)

A high-performance "Public Handshake" SDK for integrating with the **TIaaS (Tax Identity as a Service)** Middleware. This tool facilitates compliant communication with `taxid-production.up.railway.app` for enterprises operating within the Republic of Kenya.

## Installation

Install the SDK directly via pip. Ensure you are using Python 3.8+.

```bash
# Install from source
pip install .

# Or install directly from the repository
pip install git+https://github.com/Linkd-TaxID/kra-etims-sdk.git
```


---

## Legal Foundation & Obligation

This SDK is engineered to facilitate compliance with **Section 16(1)(c) of the Income Tax Act (Cap 470)**, as amended by the Finance Act (2023/2025). 

> [!IMPORTANT]
> **Statutory Notice**: Effective January 1, 2026, the Kenya Revenue Authority (KRA) will disallow any business expense deduction that is not supported by a valid eTIMS invoice transmitted via a compliant VSCU/OSCU architecture and linked to a verified Buyer PIN. 

By utilizing this SDK, organizations ensure their digital sales pipeline meets the rigorous standards of the **eTIMS Technical Specification v2.0**.

---

## The 'Middleware Moat'

While this SDK provides the interface logic (the "Remote Control"), it requires the **TIaaS Middleware** to function. The middleware manages the underlying, stateful KRA infrastructure:
- **VSCU Orchestration**: Management of the KRA-issued JAR files and Port 8088 services.
- **Security**: AES-256 `cmcKey` encryption and digital signature generation.
- **Resilience**: A 24-hour offline signing window, ensuring business continuity during transit-layer disruptions.

---

## Institutional Resilience

The SDK implements a strict mapping of HTTP 503 errors to the `KRAConnectivityTimeoutError` exception.

- **VSCU Offline Ceiling**: This error is triggered only when the 24-hour Virtual Sales Control Unit offline window is breached. 
- **Warm Cache Logic**: Validation of TCC and PIN metadata is served via local middleware caches to maintain sub-500ms latency for B2B checkout flows.

---

## Core Features

- **GavaConnect Sanitization**: Mandatory `@sanitize_kra_url` middleware strips trailing whitespace from URL strings to prevent signature failures on KRA production endpoints.
- **Proactive Token Refresh**: Implements a 60-second proactive OAuth 2.0 buffer, refreshing credentials preemptively to ensure zero-latency for high-volume high-concurrency environments.
- **Spec-Strict Models**: Full Pydantic V2 implementation of all 8 KRA functional categories (Initialization, Sync, Sale, Stock, etc.).
- **Idempotency Engine**: Native support for `X-TIaaS-Idempotency-Key` headers to safely retry "Schrödinger's Invoices" (network drops) without risking double taxation.
- **Asynchronous I/O**: Includes a fully native `AsyncKRAeTIMSClient` powered by `httpx` for non-blocking, high-concurrency event loops (FastAPI, Starlette).
- **High-Volume Batching**: The `batch_update_stock` method automatically chunks massive ERP inventory updates (e.g., 10,000 SKUs) into safe 500-item requests to prevent KRA rate-limiting.
- **Strict Decimal Math**: Automatically intercepts and normalizes floating-point drift (e.g., `100.1 * 3 = 300.300000000004`) to absolute 2-decimal precision before transmission.


---

## Quick Start (Category 6: Sale)

```python
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail, ReceiptLabel

# 1. Initialize Authority Client (Defaults to Railway Production)
client = KRAeTIMSClient(client_id="TIaaS_ID", client_secret="TIaaS_SEC")

# 2. Transmit Compliant Sale
try:
    sale = SaleInvoice(
        tin="P0000...", bhfId="00", invcNo="INV-001",
        custNm="Enterprise Client Ltd", rcptLbel=ReceiptLabel.NORMAL,
        confirmDt="20260221100000", totItemCnt=1, 
        totTaxblAmt=1000.0, totTaxAmt=160.0, totAmt=1160.0,
        itemList=[ItemDetail(...)]
    )
    result = client.submit_sale(sale)
    print(f"KRA Signature: {result['invoiceSignature']}")
except KRAConnectivityTimeoutError:
    # Handle VSCU offline ceiling breach
    pass
```

---

## Advanced Usage

### 1. Preventing Double Taxation (Idempotency)
If your network drops after sending a payload, you won't know if KRA recorded it. Pass a unique idempotency key. The TIaaS middleware will cache the KRA response and safely replay it on retries.

```python
try:
    result = client.submit_sale(sale, idempotency_key="INV-2026-001-RETRY-1")
except TIaaSAmbiguousStateError:
    # The request was sent, but the connection died. 
    # Safe to retry with the exact same idempotency_key.
    pass
```

### 2. High-Performance Async (FastAPI / Starlette)
For modern asynchronous backends, use the async client to prevent thread blocking.

```python
import asyncio
from kra_etims.async_client import AsyncKRAeTIMSClient

async def process_checkout(invoice):
    async with AsyncKRAeTIMSClient(client_id="ID", client_secret="SEC") as client:
        result = await client.submit_sale(invoice)
        return result['invoiceSignature']
```

### 3. Bulk Inventory Synchronization
Chunk and transmit thousands of SKUs automatically.

```python
from kra_etims.models import StockItem

# Generate thousands of stock updates from your ERP
items = [StockItem(tin="P0...", bhfId="00", itemCd=f"SKU-{i}", rsonCd="01", qty=100) for i in range(5000)]

# The SDK automatically chunks this into 10 separate requests of 500 items
client.batch_update_stock(items)
```

---

## Sovereignty & Data Protection

This SDK and the associated TIaaS Middleware are compliant with the **Kenya Data Protection Act (2019)**. All taxpayer metadata is handled in accordance with sovereign data residency requirements and encryption standards.

---

## Liability Disclaimer

> [!CAUTION]
> This SDK is a technical implementation tool, not tax advice. KRA eTIMS SDK and the authors are not responsible for KRA penalties, non-deductible expenses, or financial losses resulting from user error, misconfigured payloads, or middleware misapplication.

---

## Support
For architectural escalations or middleware orchestration support, contact `ronnyabuto@icloud.com`.
