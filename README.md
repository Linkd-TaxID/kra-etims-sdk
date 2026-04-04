# KRA eTIMS SDK (Python) `v0.2.0`

```bash
pip install kra-etims-sdk               # core
pip install "kra-etims-sdk[qr]"         # + offline QR code image generation
pip install "kra-etims-sdk[otel]"       # + OpenTelemetry spans
pip install "kra-etims-sdk[dev]"        # + pytest, pytest-asyncio, pytest-httpx
```

Requires **Python 3.10+**.

---

## Error Code Reference

Hitting a `resultCd` you don't recognize?

→ **[Complete KRA eTIMS Error Code Reference](https://linkd-taxid.github.io/kra-etims-sdk/)**

Covers all official OSCU/VSCU spec codes plus production-observed codes absent
from the official KRA documentation — including the critical success code
normalization issue (`"00"` vs `"000"` vs `"0000"`).

---

## Two ways to use this SDK

### Track 1 — Tax Calculator (no account required)

`calculate_item` and `build_invoice_totals` are pure math functions. They work offline with no credentials, no network, and no TaxID account. Any Python developer in Kenya who needs KRA-compliant VAT arithmetic can use them independently.

```python
from kra_etims import calculate_item, build_invoice_totals

items = [
    calculate_item("MacBook Pro M3",  "HS847130", 5800, "B"),  # 16% Standard VAT
    calculate_item("Maize Flour 2kg", "HS110100",  200, "A"),  # 0% Exempt
    calculate_item("Diesel 1L",       "HS270900",  216, "E"),  # 8% Special Rate (petroleum)
]
totals = build_invoice_totals(items)

print(items[0].taxblAmt)      # Decimal("5000.00")
print(items[0].taxAmt)        # Decimal("800.00")
print(totals["totAmt"])       # Decimal("6216.00")
```

No configuration needed. The calculator handles all five KRA tax bands, inclusive and exclusive pricing, 4dp quantity precision for fuel/pharmaceuticals, and invoice-level residual absorption so KRA never rejects with result code 20.

### Track 2 — Full KRA Submission via TaxID (requires account)

The full platform adds KRA invoice submission, digital signing via the VSCU JAR, durable offline queuing, idempotency, and the supplier onboarding gateway — none of which exist client-side.

```python
from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item, build_invoice_totals

client = KRAeTIMSClient(client_id="TIaaS_ID", client_secret="TIaaS_SEC")

items   = [calculate_item("MacBook Pro M3", "HS847130", 5800, "A")]
invoice = SaleInvoice(
    tin="P051234567X", bhfId="00", invcNo="INV-2026-001",
    custNm="Acacia Enterprises Ltd", confirmDt="20260311120000",
    itemList=items, **build_invoice_totals(items),
)
response = client.submit_sale(invoice, idempotency_key="INV-2026-001")
print(response["invoiceSignature"])
```

`confirmDt` format: `yyyyMMddHHmmss` — e.g. `"20260311120000"` = 2026-03-11 12:00:00.

---

## Legal Foundation

> **Statutory Notice:** Section 16(1)(c) of the Income Tax Act (Cap 470), as amended by the Finance Act (2023/2025), disallows business expense deductions not supported by a valid eTIMS invoice transmitted via a compliant VSCU/OSCU architecture.

---

## Architecture: The Middleware Moat

| Layer | Responsibility |
|---|---|
| **This SDK** | Auth, payload validation, tax math, QR rendering, idempotency headers |
| **TIaaS Middleware** | VSCU JAR orchestration, AES-256 `cmcKey` encryption, KRA GavaConnect communication, 24-hour offline signing window |

The VSCU JAR is KRA's proprietary device credential program — it cannot be called directly without device initialization and cryptographic key management. TIaaS handles all of that. The SDK is the remote control; TIaaS is the engine.

---

## Authentication

Two modes, in priority order:

```python
# Mode 1: API Key (preferred for production B2B — skips OAuth round-trip)
client = KRAeTIMSClient(client_id="ID", client_secret="SEC", api_key="your_key")
# Or via environment variable (takes priority over constructor arg):
# export TAXID_API_KEY=your_key

# Mode 2: OAuth 2.0 Client Credentials (auto-refresh with 60s expiry buffer)
client = KRAeTIMSClient(client_id="ID", client_secret="SEC")

# Custom middleware URL (defaults to https://taxid-production.up.railway.app)
client = KRAeTIMSClient("ID", "SEC", base_url="https://your-instance.railway.app")
# Or via environment variable (takes priority over constructor base_url):
# export TAXID_API_URL=https://your-instance.railway.app
```

---

## Tax Bands (KRA eTIMS v2.0)

| Band | Rate | Description |
|---|---|---|
| `A` |  0% | Exempt (basic foodstuffs, medicine — no input VAT credit) |
| `B` | 16% | Standard VAT (most goods & services) |
| `C` |  0% | Zero-Rated (exports, certain food — input credit allowed) |
| `D` |  0% | Non-VAT (outside VAT Act entirely) |
| `E` |  8% | Special Rate (petroleum products, LPG per Kenya VAT Act) |

> **Warning:** A≠16% and B≠0%. This ordering is counterintuitive but is explicit in KRA VSCU/OSCU Specification v2.0 §4.1. Swapping A and B is the single most common integration error and results in incorrect Z-Report aggregation.

```python
from kra_etims import calculate_item

# Inclusive pricing (default) — SDK back-calculates net from retail
laptop  = calculate_item("MacBook Pro M3",    "HS847130", 5800,  "B")
# B=16% Standard VAT: taxblAmt=5000.00, taxAmt=800.00, totAmt=5800.00

diesel  = calculate_item("Diesel 1L",         "HS270900",  216,  "E")
# E=8% Special Rate: taxblAmt=200.00, taxAmt=16.00, totAmt=216.00

maize   = calculate_item("Maize Flour 2kg",   "HS110100",  200,  "A")
# A=0% Exempt: taxblAmt=200.00, taxAmt=0.00, totAmt=200.00

# Exclusive pricing — net price supplied, SDK adds VAT on top
fee = calculate_item("Consulting Fee", "SRV001", 1000, "B", price_is_inclusive=False)
# B=16% exclusive: taxblAmt=1000.00, taxAmt=160.00, totAmt=1160.00
```

### Quantity Precision — Fuel, Weight, Pharmaceuticals

```python
# Fuel: 15.456L — truncating to 2dp would understate the taxable amount
diesel = calculate_item("Diesel", "HS270900", 3236.57, "E", qty=15.456)
# Band E (8% Special Rate — petroleum products)
# qty stored as Decimal("15.4560") — transmitted to KRA exactly
```

### Residual Drift — Invoice Integrity

`ROUND_HALF_UP` applied independently to each line can leave a 1-cent gap at invoice level. The SDK absorbs this residual into `totTaxAmt`, preventing KRA result code 20 rejections.

```python
items  = [calculate_item("Item A", "SKU001", 999.99, "A"),
          calculate_item("Item B", "SKU002", 1999.99, "A")]
totals = build_invoice_totals(items)
# totals["totTaxblAmt"] + totals["totTaxAmt"] == totals["totAmt"]  ← always true
```

> All inputs are coerced through `Decimal(str(value))` before any arithmetic. Floating-point intermediates are never used.

---

## Idempotency & Resilience

### Preventing Double Taxation — Schrödinger's Invoice

When a network timeout interrupts a POST in-flight, the invoice state is unknown. `TIaaSAmbiguousStateError` carries the `idempotency_key` that was in-flight:

```python
from kra_etims import TIaaSAmbiguousStateError, KRADuplicateInvoiceError
import time

IDEMPOTENCY_KEY = "INV-2026-001"

try:
    result = client.submit_sale(invoice, idempotency_key=IDEMPOTENCY_KEY)

except TIaaSAmbiguousStateError as exc:
    # Request sent; connection dropped before response arrived.
    time.sleep(2)
    try:
        result = client.submit_sale(invoice, idempotency_key=exc.idempotency_key)
    except KRADuplicateInvoiceError:
        # First attempt succeeded — middleware deduplicated it.
        print(f"Invoice {exc.idempotency_key} already processed.")

except KRADuplicateInvoiceError:
    print("Already processed — retrieve original receipt.")
```

### Exception Taxonomy

| Exception | Trigger |
|---|---|
| `KRAeTIMSAuthError` | Bad credentials or token refresh failure |
| `KRAConnectivityTimeoutError` | 24-hour VSCU offline ceiling breached (HTTP 503) |
| `TIaaSUnavailableError` | Middleware instance unreachable (TCP failure) |
| `TIaaSAmbiguousStateError` | Network dropped mid-POST; state unknown — carries `idempotency_key` |
| `KRAInvalidPINError` | Invalid TIN format (code 10) |
| `KRAVSCUMemoryFullError` | VSCU storage at capacity — sync before invoicing (code 11) |
| `KRADuplicateInvoiceError` | Already processed; retrieve original receipt (code 12) |
| `KRAInvalidItemCodeError` | Item not registered on eTIMS (code 13) |
| `KRAInvalidBranchError` | Branch not registered for this TIN (code 14) |
| `KRAServerError` | Transient KRA server error (codes 20/96/99) |
| `CreditNoteConflictError` | Credit note already issued for this sale (HTTP 409); carries `original_purchase_id` |
| `KRAeTIMSError` | Base class for all SDK exceptions; also raised directly for unexpected HTTP 4xx/5xx responses from the middleware (message contains only the status code — no request URLs or PII) |

---

## Thread Safety & Concurrency

The sync client is safe to share across Celery workers and FastAPI request handlers. The async client is safe for concurrent `asyncio` tasks.

| Concern | Mechanism |
|---|---|
| OAuth token refresh | `threading.Lock` (sync) / `asyncio.Lock` (async) with double-checked locking |
| Sub-interface init (`client.reports`, `client.gateway`) | Double-checked locking prevents duplicate initialisation under concurrent first-access |
| `requests.Session` connection pool | One session per thread via `threading.local()` — each Celery worker gets its own pool, preventing urllib3 connection corruption under concurrent access |

### Celery worker pattern

```python
# One client instance per worker process — initialise at module level.
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

`client_secret` and `api_key` are never emitted by `__repr__`, `__str__`, or exception messages:

```python
print(client)
# KRAeTIMSClient(client_id='TIaaS_ID', base_url='https://...', auth_mode='api_key')
```

---

## Observability

```bash
pip install "kra-etims-sdk[otel]"
```

The SDK emits [OpenTelemetry](https://opentelemetry.io/) spans when `opentelemetry-api` is installed. Without it, every span call is a no-op — existing integrations are unaffected.

### Spans emitted

| Span name | Emitted by | Key attributes |
|---|---|---|
| `kra_etims.submit_sale` | `submit_sale()` | `invoice.no`, `invoice.tin` |
| `kra_etims.issue_credit_note` | `issue_credit_note()` | `sale.id` |
| `kra_etims.flush_offline_queue` | `flush_offline_queue()` | `queue.size` |
| `kra_etims.request` | `_request()` (internal) | `http.method`, `http.path`, `idempotency_key` |

On exception, the span is marked `ERROR` and the exception recorded before re-raising — failures always appear in traces regardless of how the caller handles them.

### Wiring spans to an exporter

The SDK only depends on `opentelemetry-api`. To see spans in a backend (Jaeger, Grafana Tempo, Honeycomb, etc.) your application configures the SDK and exporter — the SDK never touches that layer.

```python
# Minimal setup — add this once at application startup, before any SDK calls.
# Example uses the OTLP exporter; swap for your backend's exporter package.
#
#   pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

# From here, all kra_etims.* spans flow to your backend automatically.
from kra_etims import KRAeTIMSClient
client = KRAeTIMSClient(client_id="...", client_secret="...")
```

The same setup works for the async client — `opentelemetry-api` is context-propagation-aware and works across `asyncio` task boundaries without any changes.

---

## Async Client (FastAPI / Starlette)

Full API parity with the sync client.

```python
from kra_etims import AsyncKRAeTIMSClient

async def process_checkout(invoice):
    async with AsyncKRAeTIMSClient(client_id="ID", client_secret="SEC") as client:
        return await client.submit_sale(invoice, idempotency_key="INV-001")
```

### Concurrent Offline Queue Flush

When your application loses connectivity and queues invoices locally, flush them once the middleware is reachable again. Uses `asyncio.gather` with `asyncio.Semaphore(50)` — a single failed invoice never aborts the batch.

```python
async with AsyncKRAeTIMSClient("ID", "SEC") as client:
    results = await client.flush_offline_queue(locally_queued_invoices)
    failed  = [r for r in results if r["status"] == "error"]
```

> Note: This flushes invoices your application queued locally when the middleware was unreachable. The middleware also maintains its own durable server-side queue for VSCU outages — that queue drains automatically without SDK involvement.

---

## Offline QR Code Generator

```python
from kra_etims import render_kra_qr_string, generate_qr_bytes

response   = client.submit_sale(invoice)
qr_string  = render_kra_qr_string(response)
png_bytes  = generate_qr_bytes(qr_string)
thermal_printer.write(png_bytes)
```

> Requires `pip install "kra-etims-sdk[qr]"`

---

## Gateway: Supplier Onboarding (TaxID Links)

Enables buyers to obtain KRA Category 5 (Reverse Invoice) receipts for purchases from **informal suppliers** (kiosks, jua kali, market vendors) who have no eTIMS software.

**Why this exists:** Finance Act 2023 §16(1)(c) disallows expense deductions for purchases not backed by a valid eTIMS invoice. KRA's Category 5 spec allows the buyer to issue the invoice — but only with the supplier's explicit consent, obtained via SMS or WhatsApp.

**Flow:**
1. Buyer calls `onboard_supplier()` with the supplier's phone and transaction amount
2. TIaaS sends the supplier an SMS/WhatsApp message with the amount and a confirmation token
3. Supplier replies `YES {token}` (or `YES {KRA-PIN} {token}` if registered)
4. TIaaS raises a KRA Category 5 Reverse Invoice and signs it via the VSCU JAR
5. Buyer polls `get_status()` until `status == "SIGNED"`

```python
# Single supplier
result = client.gateway.onboard_supplier(
    phone="+254712345678",
    amount=5000,
    buyer_pin="A000123456B",
    buyer_name="Acme Superstore",
    item_description="Maize supply — March 2026",
)
print(result.request_id)   # 42 — use to poll status
print(result.token)        # "XK9T" — embedded in the outbound SMS
print(result.channel)      # "whatsapp" | "sms"

# Poll until signed
status = client.gateway.get_status(result.request_id)
print(status.status)       # PENDING → CONFIRMED → SIGNED
print(status.purchase_id)  # set once VSCU signing completes
```

```python
# Bulk — multiple suppliers in one call
from kra_etims import SupplierEntry

result = client.gateway.onboard_suppliers(
    suppliers=[
        SupplierEntry(phone="+254712345678", amount=5000, item_description="Produce"),
        SupplierEntry(phone="+254798765432", amount=12000, item_description="Hardware"),
    ],
    buyer_pin="A000123456B",
    buyer_name="Acme Superstore",
)
print(result.initiated, result.failed)  # 2, 0
```

```python
# Async
result = await client.gateway.onboard_supplier(
    phone="+254712345678", amount=5000,
    buyer_pin="A000123456B", buyer_name="Acme Superstore",
)
```

**Status lifecycle:** `PENDING` → `CONFIRMED` → `SIGNED` (success), or `EXPIRED` (no reply within window) / `FAILED` (VSCU error).

---

## Reports (X/Z)

```python
# X Report — interim read-only snapshot (safe at any time, no VSCU state change)
x = client.reports.get_x_report("2026-03-11")
print(x.band_a.taxable_amount)   # Decimal("43103.45")
print(x.band_a.tax_amount)       # Decimal("6896.55")
print(x.total_amount)            # Decimal("52340.00")

# Z Report — closes the VSCU fiscal period (POST internally — call once per day)
z = client.reports.get_daily_z("2026-03-11")
print(z.vscu_acknowledged)       # True when VSCU day-reset completed
print(z.invoice_count)
print(z.total_vat)

# A second call for the same date raises KRAeTIMSError (middleware returns 409 Conflict).

# Async
x = await client.reports.get_x_report("2026-03-11")
z = await client.reports.get_daily_z("2026-03-11")
```

---

## Credit Notes (Category 7)

Issue a credit note against a previously signed sale. The middleware sources the original amount from the signed receipt — callers cannot supply amounts, preventing manipulation.

```python
from kra_etims import CreditNoteConflictError

# Full reversal
result = client.issue_credit_note(original_purchase_id=42, reason="Customer return")
print(result["cuInvoiceNumber"])   # Signed credit note CU number

# Partial reversal — supply specific line items to reverse
result = client.issue_credit_note(
    original_purchase_id=42,
    reason="Partial return",
    items=[{"itemCd": "SKU-001", "qty": 1}],
)

# Async
result = await client.issue_credit_note(original_purchase_id=42, reason="Return")
```

```python
try:
    client.issue_credit_note(original_purchase_id=42)
except CreditNoteConflictError as exc:
    # HTTP 409 — a credit note already exists for this sale.
    print(f"Already reversed: purchase {exc.original_purchase_id}")
```

> `submit_reverse_invoice()` is deprecated and targets a removed endpoint. Use `issue_credit_note()` instead.

---

## Stock Adjustments (Category 8)

Submit stock movements (imports, write-offs, transfers) to `POST /v2/etims/stock/adjustment`. Financial totals are computed server-side from `qty` and `prc` — do not supply them.

```python
from kra_etims import StockAdjustmentLine

lines = [
    StockAdjustmentLine(
        itemCd="HS847130",
        itemNm="MacBook Pro M3",
        ioType="M",          # M=Import/IN, A=Adjustment/OUT, I=Issue/OUT
        qty=10,
        prc=5000,            # unit price excl. VAT
        totDcAmt=0,
        taxTyCd="B",         # 16% Standard VAT
    ),
]

# 201 = VSCU signed synchronously; 202 = queued for retry
result = client.submit_stock_adjustment(lines, remark="March stock receive")
print(result["sarNo"])       # KRA Stock Adjustment Receipt number

# B2B movement — include counterparty TIN
result = client.submit_stock_adjustment(
    lines,
    cust_tin="A000123456B",
    cust_nm="Supplier Ltd",
)

# Async
result = await client.submit_stock_adjustment(lines)
```

---

## Sovereignty & Data Protection

This SDK and the TIaaS Middleware comply with the **Kenya Data Protection Act (2019)**.

---

> [!CAUTION]
> This SDK is a technical implementation tool, not tax advice. The authors are not responsible for KRA penalties, non-deductible expenses, or financial losses resulting from user error, misconfigured payloads, or middleware misapplication.

---

## Support

For architectural escalations or middleware orchestration support: `ronnyabuto@icloud.com`
