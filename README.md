# KRA eTIMS SDK (Python) `v0.2.0`

A high-performance, idempotency-first Python SDK for integrating with the **TIaaS (Tax Identity as a Service)** Middleware. Engineered to shield enterprise developers from KRA's infrastructure volatility while exposing a clean, type-safe API surface.

```bash
pip install kra-etims-sdk          # core
pip install "kra-etims-sdk[qr]"    # + offline QR code image generation
pip install "kra-etims-sdk[dev]"   # + pytest, pytest-asyncio, pytest-httpx
```

Requires **Python 3.10+**.

---

## Legal Foundation

This SDK facilitates compliance with **Section 16(1)(c) of the Income Tax Act (Cap 470)**, as amended by the Finance Act (2023/2025).

> [!IMPORTANT]
> **Statutory Notice**: Effective January 1, 2026, the KRA will disallow business expense deductions not supported by a valid eTIMS invoice transmitted via a compliant VSCU/OSCU architecture and linked to a verified Buyer PIN.

---

## Architecture: The Middleware Moat

This SDK is the **remote control**. The **TIaaS Middleware** is the engine it controls:

| Layer | Responsibility |
|---|---|
| **This SDK** | Auth, payload validation, retry logic, tax math, QR rendering |
| **TIaaS Middleware** | VSCU JAR orchestration, AES-256 `cmcKey` encryption, KRA GavaConnect communication, 24-hour offline signing window |

---

## Quick Start

### Single-Import DX

```python
from kra_etims import (
    KRAeTIMSClient,
    SaleInvoice,
    calculate_item,
    build_invoice_totals,
    render_kra_qr_string,
    KRAConnectivityTimeoutError,
    TIaaSAmbiguousStateError,
    KRADuplicateInvoiceError,
)
```

### The Zero-Math Invoice (Category 6)

Pass retail prices and tax bands. The SDK calculates every exclusive amount, VAT split, and total required by the KRA v2.0 spec.

```python
from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item, build_invoice_totals

client = KRAeTIMSClient(client_id="TIaaS_ID", client_secret="TIaaS_SEC")

# Zero math: pass retail price + tax band → get a KRA-compliant ItemDetail
maize   = calculate_item("Maize Flour 2kg",  "HS110100", 200,  "D")  # Exempt (Band D)
laptop  = calculate_item("MacBook Pro M3",   "HS847130", 5800, "A")  # 16% VAT (Band A)
diesel  = calculate_item("Diesel 1L",        "HS270900", 216,  "B")  # 0% Zero-Rated (Band B)

items = [maize, laptop, diesel]

invoice = SaleInvoice(
    tin="P051234567X", bhfId="00",
    invcNo="INV-2026-001",
    custNm="Acacia Enterprises Ltd",
    confirmDt="20260311120000",
    itemList=items,
    **build_invoice_totals(items),  # totItemCnt, totTaxblAmt, totTaxAmt, totAmt
)

try:
    response = client.submit_sale(invoice, idempotency_key="INV-2026-001")
    print(f"Signature: {response['invoiceSignature']}")
except KRADuplicateInvoiceError:
    print("Already processed — retrieve original receipt instead of retrying.")
except KRAConnectivityTimeoutError:
    print("VSCU offline ceiling breached — queue for retry after connectivity restored.")
```

---

## Authentication

Two modes, in priority order:

```python
# Mode 1: API Key (preferred for production B2B — skips OAuth entirely)
client = KRAeTIMSClient(client_id="ID", client_secret="SEC", api_key="your_key")
# Or via environment variable (takes priority over constructor arg):
# export TAXID_API_KEY=your_key

# Mode 2: OAuth 2.0 Client Credentials (auto-refresh with 60s buffer)
client = KRAeTIMSClient(client_id="ID", client_secret="SEC")

# Custom middleware URL (defaults to https://taxid-production.up.railway.app)
client = KRAeTIMSClient("ID", "SEC", base_url="https://your-tiims-instance.railway.app")
```

---

## Mathematical Precision

The SDK enforces KRA's strict numeric precision rules automatically. There is nothing to configure.

### Quantity Precision — Fuel, Weight, Pharmaceuticals

Monetary amounts use 2 decimal places (`0.01`). Quantities use **4 decimal places** (`0.0001`) per the KRA v2.0 specification. This prevents fiscal misrepresentation for continuous-measure goods:

```python
# Fuel: 15.456 L — truncating to 2dp (15.45) would understate the taxable amount
diesel = calculate_item("Diesel", "HS270900", total_price=3236.57, tax_band="B", qty=15.456)
# qty stored as Decimal("15.4560") — transmitted to KRA exactly

# Weight: 0.375 kg of a controlled pharmaceutical
drug = calculate_item("Amoxicillin 500mg", "HS300490", total_price=450, tax_band="A", qty=0.375)
# qty stored as Decimal("0.3750")
```

### Residual Drift — Invoice Integrity

`ROUND_HALF_UP` applied independently to `taxblAmt` and `taxAmt` can leave a 1-cent gap at line and invoice level. The SDK absorbs this residual into `taxAmt` before transmission, preventing KRA result code 20 rejections:

```python
# Line level: tot_amt - taxbl_amt - tax_amt = residual → assigned to tax_amt
# Invoice level: totAmt - totTaxblAmt - totTaxAmt = residual → assigned to totTaxAmt

items = [
    calculate_item("Item A", "SKU001", 999.99, "A"),
    calculate_item("Item B", "SKU002", 1999.99, "A"),
]
totals = build_invoice_totals(items)
# totals["totTaxblAmt"] + totals["totTaxAmt"] == totals["totAmt"]  ← always true
```

> All inputs (`total_price`, `qty`) are coerced through `Decimal(str(value))` before any arithmetic. Floating-point intermediates are never used.

---

## Tax Bands (KRA eTIMS v2.0)

| Band | Rate | Description |
|---|---|---|
| `A` | 16% | Standard VAT (most goods & services) |
| `B` | 0% | Zero-Rated (petroleum products — VAT credit allowed) |
| `C` | 8% | Special Rate (specific goods per schedule) |
| `D` | 0% | Exempt (basic foodstuffs — no VAT credit) |
| `E` | 8% | Non-VAT (outside VAT scope, 8% levy applies) |

```python
from kra_etims import calculate_item

# Band A — 16% Standard VAT, inclusive pricing
laptop = calculate_item("MacBook Pro M3", "HS847130", 5800, "A")
# laptop.taxblAmt == Decimal("5000.00")
# laptop.taxAmt   == Decimal("800.00")
# laptop.totAmt   == Decimal("5800.00")

# Band C — 8% Special Rate, inclusive pricing
service = calculate_item("Hotel Accommodation", "SRV910", 10800, "C")
# service.taxblAmt == Decimal("10000.00")
# service.taxAmt   == Decimal("800.00")
# service.totAmt   == Decimal("10800.00")

# Band D — 0% Exempt (no VAT computation)
maize = calculate_item("Maize Flour 2kg", "HS110100", 200, "D")
# maize.taxblAmt == Decimal("200.00")
# maize.taxAmt   == Decimal("0.00")
# maize.totAmt   == Decimal("200.00")

# Exclusive pricing — net price supplied, SDK adds VAT on top
item = calculate_item("Consulting Fee", "SRV001", 1000, "A", price_is_inclusive=False)
# item.taxblAmt == Decimal("1000.00")
# item.taxAmt   == Decimal("160.00")
# item.totAmt   == Decimal("1160.00")
```

---

## Idempotency & Resilience

The SDK maps every failure mode to a precise, actionable exception.

### Preventing Double Taxation — Schrödinger's Invoice

When a network timeout interrupts a POST in-flight, the invoice state is unknown: it may have been signed by KRA or it may not. `TIaaSAmbiguousStateError` carries the `idempotency_key` that was in-flight so the caller can retry the exact same transaction without storing the key externally:

```python
import time
from kra_etims import TIaaSAmbiguousStateError, KRADuplicateInvoiceError

IDEMPOTENCY_KEY = "INV-2026-001"

try:
    result = client.submit_sale(invoice, idempotency_key=IDEMPOTENCY_KEY)

except TIaaSAmbiguousStateError as exc:
    # Request was sent; connection dropped before response arrived.
    # exc.idempotency_key is guaranteed to equal IDEMPOTENCY_KEY —
    # use it directly to retry without relying on outer scope.
    time.sleep(2)  # brief back-off before retry
    try:
        result = client.submit_sale(invoice, idempotency_key=exc.idempotency_key)
    except KRADuplicateInvoiceError:
        # The first attempt succeeded after all — middleware deduplicated it.
        # The fiscal record exists on KRA; this is a confirmed safe state.
        print(f"Invoice {exc.idempotency_key} was already processed. Retrieve original receipt.")

except KRADuplicateInvoiceError:
    # Explicit duplicate on first attempt — already on KRA. Do not re-submit.
    print("Already processed — retrieve original receipt instead of retrying.")
```

### Async Pattern (FastAPI / Celery)

```python
from kra_etims import AsyncKRAeTIMSClient, TIaaSAmbiguousStateError

async def submit_with_retry(invoice, idempotency_key: str):
    async with AsyncKRAeTIMSClient("ID", "SEC") as client:
        try:
            return await client.submit_sale(invoice, idempotency_key=idempotency_key)
        except TIaaSAmbiguousStateError as exc:
            await asyncio.sleep(2)
            return await client.submit_sale(invoice, idempotency_key=exc.idempotency_key)
```

### Exception Taxonomy

| Exception | Trigger |
|---|---|
| `KRAeTIMSAuthError` | Bad credentials or token refresh failure |
| `KRAConnectivityTimeoutError` | 24-hour VSCU offline ceiling breached (HTTP 503) |
| `TIaaSUnavailableError` | Railway instance unreachable |
| `TIaaSAmbiguousStateError` | Network dropped mid-POST; state unknown — carries `idempotency_key` |
| `KRAInvalidPINError` | Invalid TIN format — expected `A123456789B` (code 10) |
| `KRAVSCUMemoryFullError` | VSCU storage at capacity — sync before invoicing (code 11) |
| `KRADuplicateInvoiceError` | Already processed; retrieve original receipt (code 12) |
| `KRAInvalidItemCodeError` | Item not registered on eTIMS — register via Category 4 (code 13) |
| `KRAInvalidBranchError` | Branch not registered for this TIN (code 14) |
| `KRAServerError` | Transient KRA server error (codes 20/96/99) |

---

## Thread Safety & Concurrency

The sync client (`KRAeTIMSClient`) is safe to share across Celery workers and FastAPI request handlers without any external locking. The async client (`AsyncKRAeTIMSClient`) is safe for concurrent `asyncio` tasks.

### What is protected

| Concern | Mechanism |
|---|---|
| OAuth token refresh | `threading.Lock` (sync) / `asyncio.Lock` (async) with double-checked locking — only one thread/task refreshes at a time |
| Sub-interface init (`client.reports`, `client.gateway`) | Double-checked locking prevents duplicate initialisation under concurrent first-access |
| `requests.Session` connection pool | One session per client instance; safe for multi-threaded use per `urllib3` guarantees |

### Celery worker pattern

```python
# One client instance per worker process — not per task.
# Initialise at module level so the connection pool is reused across tasks.
from kra_etims import KRAeTIMSClient

etims_client = KRAeTIMSClient(
    client_id=os.environ["TIIMS_CLIENT_ID"],
    client_secret=os.environ["TIIMS_CLIENT_SECRET"],
)

@celery_app.task
def submit_invoice_task(invoice_data: dict):
    invoice = SaleInvoice(**invoice_data)
    return etims_client.submit_sale(invoice, idempotency_key=invoice.invcNo)
```

### Credential sanitization

`client_secret` and `api_key` are never emitted by `__repr__`, `__str__`, or exception messages. The client is safe to log or include in error reports:

```python
client = KRAeTIMSClient("TIaaS_ID", "super_secret_key", api_key="ak_live_xxxx")

print(client)
# KRAeTIMSClient(client_id='TIaaS_ID', base_url='https://taxid-production.up.railway.app', auth_mode='api_key')
# ↑ client_secret and api_key are never printed
```

---

## Async Client (FastAPI / Starlette)

Full API parity with the sync client, including `api_key` auth and concurrent offline flush.

```python
from kra_etims import AsyncKRAeTIMSClient

async def process_checkout(invoice):
    async with AsyncKRAeTIMSClient(client_id="ID", client_secret="SEC") as client:
        return await client.submit_sale(invoice, idempotency_key="INV-001")
```

### Concurrent Offline Queue Flush

When connectivity is restored, the SDK flushes queued invoices concurrently (up to 50 in-flight) using `asyncio.gather` + `asyncio.Semaphore`. A single failed invoice never aborts the batch.

```python
async with AsyncKRAeTIMSClient("ID", "SEC") as client:
    results = await client.flush_offline_queue(offline_invoices)
    # Returns list of {"invoice_no": ..., "status": "success"|"already_processed"|"error", ...}
    failed = [r for r in results if r["status"] == "error"]
```

---

## Offline QR Code Generator

Takes a signed receipt response and renders the KRA QR string locally — no second round-trip required.

```python
from kra_etims import render_kra_qr_string, generate_qr_bytes

response = client.submit_sale(invoice)

# Extract the signed KRA QR string
qr_string = render_kra_qr_string(response)

# Render as PNG bytes → stream directly to thermal printer
png_bytes = generate_qr_bytes(qr_string)
thermal_printer.write(png_bytes)

# Or save to file
from kra_etims import save_qr_image
save_qr_image(qr_string, "/tmp/receipt_qr.png")
```

> Requires the optional `qr` extra: `pip install "kra-etims-sdk[qr]"`

---

## Gateway: USSD / WhatsApp Reverse Invoicing

Enable suppliers in the field — with no POS, no app, just a feature phone — to initiate a compliant reverse invoice via USSD or WhatsApp. The TIaaS backend orchestrates the full KRA eTIMS flow and sends an SMS confirmation.

```python
# Sync
result = client.gateway.request_reverse_invoice(
    phone_number="+254712345678",
    amount=5000,
    description="Maize supply — March 2026",
)
print(result.request_id)   # poll for status
print(result.status)       # "pending" | "processing" | "completed" | "failed"

# Poll status
status = client.gateway.get_status(result.request_id)
print(status.invoice_no)   # set once KRA invoice is raised
print(status.qr_string)    # KRA QR string for the receipt
```

```python
# Async
result = await client.gateway.request_reverse_invoice(
    phone_number="+254712345678",
    amount=5000,
)
```

---

## Reports (X/Z)

Strictly-typed Pydantic models ready for ERP system consumption — no JSON parsing required.

```python
# Interim X-report (safe at any time, does not reset VSCU counters)
x = client.reports.get_x_report("2026-03-11")
print(x.band_a.taxable_amount)   # Decimal("45120.69")
print(x.band_a.tax_amount)       # Decimal("7219.31")
print(x.total_amount)            # Decimal("52340.00")

# Daily Z-report (closes the VSCU period — call once after close of trade)
z = client.reports.get_daily_z("2026-03-11")
print(z.period_number)           # Z-counter (increments per daily close)
print(z.invoice_count)
print(z.total_vat)

# Async
x = await client.reports.get_x_report("2026-03-11")
z = await client.reports.get_daily_z("2026-03-11")
```

---

## Bulk Inventory Synchronisation

Automatically chunks thousands of SKUs into safe 500-item requests to avoid rate-limit violations.

```python
from kra_etims import StockItem

items = [
    StockItem(tin="P051234567X", bhfId="00", itemCd=f"SKU-{i}", rsonCd="01", qty=100)
    for i in range(5000)
]
# Dispatches 10 sequential POST requests of 500 items each
client.batch_update_stock(items)
```

---

## Sovereignty & Data Protection

This SDK and the TIaaS Middleware are compliant with the **Kenya Data Protection Act (2019)**. All taxpayer metadata is handled in accordance with sovereign data residency requirements and encryption standards.

---

> [!CAUTION]
> This SDK is a technical implementation tool, not tax advice. The authors are not responsible for KRA penalties, non-deductible expenses, or financial losses resulting from user error, misconfigured payloads, or middleware misapplication.

---

## Support

For architectural escalations or middleware orchestration support, contact `ronnyabuto@icloud.com`.
