# KRA eTIMS SDK (Python) `v0.5.0`

```bash
pip install taxid-etims               # core SDK
pip install "taxid-etims[cli]"        # + etims CLI (auth, invoices, reports, tax, TCC)
pip install "taxid-etims[qr]"         # + offline QR code image generation
pip install "taxid-etims[otel]"       # + OpenTelemetry spans
pip install "taxid-etims[dev]"        # + pytest, pytest-asyncio, pytest-httpx
```

Requires **Python 3.10+**.

---

## Error Code Reference

Hitting a `resultCd` you don't recognize?

→ **[Complete KRA eTIMS Error Code Reference](https://linkd-taxid.github.io/kra-etims-sdk/)**

Covers all official OSCU/VSCU spec codes plus production-observed codes absent from the official KRA documentation.

One gotcha worth knowing: the SDK normalizes five distinct `resultCd` success variants — `"0"`, `"00"`, `"000"`, `"0000"`, and `"001"`. The spec documents `"000"` (OSCU) and `"00"` (VSCU JAR). The variants `"0"` and `"0000"` were discovered from live GavaConnect production traffic; `"001"` signals an empty result set and is not an error. Before this normalization, legitimately signed receipts were raising `KRAeTIMSError` in production on the GavaConnect path.

---

## Three ways to use this SDK

### Track 1 — Tax Calculator (offline, no account required)

`calculate_item` and `build_invoice_totals` are pure math functions. They work offline with no credentials, no network, and no account of any kind. Any Python developer in Kenya who needs KRA-compliant VAT arithmetic can use them independently.

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

### Track 2 — GavaConnect Direct (free, requires KRA developer registration)

`GavaConnectClient` connects directly to KRA's own API gateway — no TIaaS subscription required. Supports taxpayer PIN validation and Tax Compliance Certificate (TCC) checks. Registration is free at [developer.go.ke](https://developer.go.ke).

```python
from kra_etims import GavaConnectClient, GavaConnectPINNotFoundError, GavaConnectTCCError

client = GavaConnectClient(consumer_key="your_key", consumer_secret="your_secret")
# Also: GavaConnectClient.from_env(), AsyncGavaConnectClient(...), sandbox=True

try:
    result = client.lookup_pin("A000123456B")
    print(result["PINDATA"]["Name"])           # KRA masks: "J**n D**"
    print(result["PINDATA"]["StatusOfPIN"])    # "Active"
    print(result["PINDATA"]["TypeOfTaxpayer"]) # "Individual" | "Company"
except GavaConnectPINNotFoundError:
    print("PIN not found in KRA registry")

try:
    result = client.check_tcc("A000123456B", tcc_number="TCC2026001234")
    print(result["Status"])    # "OK"
except GavaConnectTCCError:
    print("TCC invalid or expired")
```

Token management is automatic — the client fetches and caches a Bearer token (valid ~1 hour) and refreshes it transparently. The sync client is thread-safe; async and `from_env()` variants available.

| Operation | GavaConnect | TIaaS |
|---|---|---|
| PIN validation (registry lookup) | ✅ | ✅ |
| TCC validation | ✅ | ❌ |
| Invoice submission | ❌ (roadmap) | ✅ |
| X/Z reports | ❌ | ✅ |
| Device initialization | ❌ | ✅ |

### Track 3 — Full KRA Submission via TaxID (requires account)

The full platform adds KRA invoice submission, digital signing via the VSCU JAR, durable offline queuing, idempotency, and the supplier onboarding gateway — none of which exist client-side.

```python
from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item, build_invoice_totals

client = KRAeTIMSClient(client_id="TIaaS_ID", client_secret="TIaaS_SEC")

items   = [calculate_item("MacBook Pro M3", "HS847130", 5800, "B")]  # B = 16% Standard VAT
invoice = SaleInvoice(
    tin="P051234567X", bhfId="00", invcNo="INV-2026-001",
    # B2B sale — supply buyer name. For B2C retail, omit custNm; defaults to "N/A".
    custNm="Acacia Enterprises Ltd",
    confirmDt="20260311120000",
    itemList=items, **build_invoice_totals(items),
)
response = client.submit_sale(invoice, idempotency_key="INV-2026-001")
print(response["cuInvoiceNumber"])   # e.g. "KRACU0100000001/152 NS"
```

`confirmDt` format: `yyyyMMddHHmmss` — e.g. `"20260311120000"` = 2026-03-11 12:00:00.

The middleware response also carries `receiptSignature`, `kraQrPayload`, `sdcId`, and
`vscuTimestamp` — there is no `invoiceSignature` key.

#### Mixed-band invoices (middleware V14+)

A single receipt may span multiple tax bands (e.g. exempt bread, Band A, plus standard
soda, Band B, on one grocery ticket). Set `itemClsCd` (UN/CEFACT commodity code) on
**every** line of a mixed-band invoice — `submit_sale` raises `ValueError` if any line
is missing it. The SDK then transmits a per-line `items[]` payload and the middleware
books the per-band split into X/Z reports. Single-band invoices keep the flat payload
and do not need `itemClsCd`.

```python
bread = calculate_item("Loaf bread", "BREAD", 120, "A")   # A = Exempt 0%
soda  = calculate_item("Soda 500ml", "SODA",  140, "B")   # B = Standard 16%
bread.itemClsCd = "50180000"
soda.itemClsCd  = "50200000"

invoice = SaleInvoice(
    tin="P051234567X", bhfId="00", invcNo="INV-2026-002",
    confirmDt="20260311120000",
    itemList=[bread, soda], **build_invoice_totals([bread, soda]),
)
response = client.submit_sale(invoice, idempotency_key="INV-2026-002")
```

---

## Legal Foundation

> **Statutory Notice:** Section 16(1)(c) of the Income Tax Act (Cap 470), as amended by the Finance Act (2023/2025), disallows business expense deductions not supported by a valid eTIMS invoice transmitted via a compliant VSCU/OSCU architecture.

---

## Architecture

| Layer | What it does | Account needed |
|---|---|---|
| **This SDK — offline** | Tax math, payload validation, QR rendering | None |
| **This SDK — GavaConnect** | PIN validation, TCC checks direct to KRA | Free (developer.go.ke) |
| **This SDK — TIaaS** | Auth, idempotency headers, offline queue | TIaaS subscription |
| **TIaaS Middleware** | VSCU JAR orchestration, AES-256 `cmcKey` encryption, 24-hour offline signing window | TIaaS subscription |

The VSCU JAR is KRA's proprietary device credential program — it cannot be called directly without device initialization and cryptographic key management. TIaaS handles all of that. For invoice submission, the SDK is the remote control; TIaaS is the engine. For PIN and TCC lookups, the SDK talks to KRA's GavaConnect gateway directly.

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

## CLI

```bash
pip install "taxid-etims[cli]"
```

All SDK features from the terminal. Credentials stored in the OS keyring; use env vars (`TAXID_API_KEY`, `GAVACONNECT_CONSUMER_KEY/SECRET`) in headless environments.

```bash
etims auth login --api-key YOUR_KEY                          # TIaaS
etims auth login --consumer-key KEY --consumer-secret SEC    # GavaConnect

etims tax calculate --price 5800 --band B --json | jq '.taxAmt'
etims invoice validate invoice.json
etims invoice submit invoice.json --idempotency-key INV-2026-001
etims invoice submit - < invoice.json   # stdin

etims pin validate A000123456B          # live KRA lookup
etims tcc check --pin A000123456B --tcc-number TCC2026001234

etims report x --date 2026-04-26        # read-only snapshot
etims report z                          # close fiscal day — irreversible, prompts first
etims queue flush
```

Every command: `--json` → machine-readable stdout, Rich → stderr. Exit `0`/`1`. `etims --help` and `etims <command> --help` for full reference.

---

## Tax Bands (KRA eTIMS v2.0)

| Band | Rate | Description |
|---|---|---|
| `A` |  0% | Exempt (basic foodstuffs, medicine — no input VAT credit) |
| `B` | 16% | Standard VAT (most goods & services) |
| `C` |  0% | Zero-Rated (exports, certain food — input credit allowed) |
| `D` |  0% | Non-VAT (outside VAT Act entirely) |
| `E` |  8% | Special Rate (petroleum products, LPG — **verify with KRA post-Finance Act 2023**) |

> ⚠️ **Band E rate advisory:** The Finance Act 2023 (Kenya) amended the VAT Act and may have changed the 8% petroleum rate. Do not use Band E on new items until confirmed with KRA at timsupport@kra.go.ke. If the rate changed, update the `ETIMS_TAX_RATE_E` environment variable — no SDK code change required.

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

Most financial libraries collapse network failures into one `TimeoutError` and leave retry semantics to the caller. This SDK partitions failures into four states with explicit retry rules:

| Failure | Exception | Safe to retry? |
|---|---|---|
| TCP `ConnectError` — request never left the socket | `TIaaSUnavailableError` | Yes — unconditional |
| `ReadTimeout` / `WriteTimeout` on POST — sent, no response | `TIaaSAmbiguousStateError` | Only with the **same** idempotency key |
| HTTP 500 on POST — server may have committed before erroring | `TIaaSAmbiguousStateError` | Only with the **same** idempotency key |
| `ReadTimeout` on GET / HTTP 500 on GET — no mutation possible | `TIaaSUnavailableError` | Yes — unconditional |

`TIaaSAmbiguousStateError` carries the `idempotency_key` that was in-flight. Re-submit with the same key: if the first attempt committed, KRA returns code 12 and the middleware deduplicates it; if it didn't commit, the invoice is submitted normally. Without an idempotency key the correct action after an ambiguous failure is undefined — which is why omitting it emits a `UserWarning`.

```python
# Explicit key — no warning, full control
result = client.submit_sale(invoice, idempotency_key="INV-2026-001")

# Omitted — auto-generates "P051234567X:INV-2026-001" + UserWarning
result = client.submit_sale(invoice)
```

```python
from kra_etims import TIaaSAmbiguousStateError, KRADuplicateInvoiceError
import time

IDEMPOTENCY_KEY = "INV-2026-001"

try:
    result = client.submit_sale(invoice, idempotency_key=IDEMPOTENCY_KEY)

except TIaaSAmbiguousStateError as exc:
    # Sent; connection dropped before response. Re-submit with the same key.
    time.sleep(2)
    try:
        result = client.submit_sale(invoice, idempotency_key=exc.idempotency_key)
    except KRADuplicateInvoiceError:
        # First attempt committed — middleware deduplicated it. Receipt exists on KRA.
        print(f"Invoice {exc.idempotency_key} already processed.")

except KRADuplicateInvoiceError:
    print("Already processed — retrieve original receipt.")
```

### Exception Taxonomy

| Exception | Trigger |
|---|---|
| `KRAeTIMSAuthError` | Bad credentials or token refresh failure (HTTP 401) |
| `KRAAuthorizationError` | Authenticated but not authorised for this operation (HTTP 403) — key lacks required role |
| `KRAConnectivityTimeoutError` | 24-hour VSCU offline ceiling breached (HTTP 503) |
| `TIaaSUnavailableError` | Middleware unreachable — TCP `ConnectError` or timeout on a read-only request; safe to retry unconditionally |
| `TIaaSAmbiguousStateError` | POST sent but connection dropped, or HTTP 500 on POST — carries `idempotency_key`; re-submit with the same key |
| `KRAInvalidPINError` | Invalid TIN format (code 10) |
| `KRAVSCUMemoryFullError` | VSCU storage at capacity — sync before invoicing (code 11) |
| `KRADuplicateInvoiceError` | Invoice already processed (codes 12, 994); `is_idempotent_success=True` — receipt exists on KRA, treat as success in retry loops |
| `KRAInvalidItemCodeError` | Item not registered on eTIMS (code 13) |
| `KRAInvalidBranchError` | Branch not registered for this TIN (code 14) |
| `KRAServerError` | Transient KRA server error (codes 20/96/99) |
| `KRADuplicateInvoiceError` | Device already initialized (code 902); `is_idempotent_success=True` — existing `cmcKey` remains valid, do not re-initialize |
| `KRAeTIMSError` | Device serial not approved (code 901) — contact timsupport@kra.go.ke |
| `KRAeTIMSError` | VSCU sequence error (code 921) — `saveSales` must precede `saveInvoice`; cannot mix OSCU and VSCU paths |
| `CreditNoteExceedsOriginalError` | Credit note would exceed the receipt's reversible balance (HTTP 422, code `CREDIT_NOTE_EXCEEDS_ORIGINAL`); carries `original_purchase_id`, `already_reversed`, `remaining` |
| `CreditNoteConflictError` | Generic HTTP 409 on a credit-note request (e.g. device not initialized); carries `original_purchase_id`. Since middleware V15 it no longer means "already reversed" — multiple credit notes per receipt are allowed |
| `ZReportAlreadyIssuedError` | Z-report already submitted for this date (HTTP 409); VSCU day-reset is irreversible — do not retry; carries `report_date` |

> `KRAeTIMSError` is the base class for all SDK exceptions. Unexpected HTTP 4xx/5xx responses not mapped to a subclass raise it directly — the message contains the status code only, never request URLs or PII.

**GavaConnect exceptions** (raised by `GavaConnectClient` / `AsyncGavaConnectClient`):

| Exception | Trigger |
|---|---|
| `GavaConnectAuthError` | Consumer key / secret rejected by KRA, or token fetch failed |
| `GavaConnectPINNotFoundError` | PIN is not in KRA's taxpayer registry |
| `GavaConnectTCCError` | TCC number is invalid, expired, or not found for the given PIN |
| `GavaConnectError` | Base class for all GavaConnect exceptions |

---

## Thread Safety & Concurrency

The sync client is safe to share across Celery workers and FastAPI request handlers. The async client is safe for concurrent `asyncio` tasks.

| Concern | Mechanism |
|---|---|
| OAuth token refresh | `threading.Lock` (sync) / `asyncio.Lock` (async) with double-checked locking |
| Sub-interface init (`client.reports`, `client.gateway`) | Double-checked locking prevents duplicate initialisation under concurrent first-access |
| HTTP connection pool | `httpx.Client` is natively thread-safe — a single instance is shared across all Celery workers with no `threading.local()` required. Each worker reuses connections from the pool concurrently without corruption. |

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
pip install "taxid-etims[otel]"
```

Emits OpenTelemetry spans when `opentelemetry-api` is installed. Without it, every span call is a no-op — no import error, no overhead.

| Span | Key attributes |
|---|---|
| `kra_etims.submit_sale` | `invoice.no`, `invoice.tin` |
| `kra_etims.issue_credit_note` | `sale.id` |
| `kra_etims.flush_offline_queue` | `queue.size` |
| `kra_etims.request` | `http.method`, `http.path`, `idempotency_key` |

On exception the span is marked `ERROR` and the exception recorded before re-raising. The SDK depends only on `opentelemetry-api` — wire your exporter (OTLP, Jaeger, Honeycomb) at the application layer as usual.

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

> Requires `pip install "taxid-etims[qr]"`

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
from kra_etims import ZReportAlreadyIssuedError

# X Report — interim read-only snapshot (safe at any time, no VSCU state change)
x = client.reports.get_x_report("2026-03-11")
print(x.band_b.taxable_amount)   # Decimal("43103.45")  # Band B = Standard VAT 16%
print(x.band_b.tax_amount)       # Decimal("6896.55")
print(x.band_a.taxable_amount)   # Decimal("5000.00")   # Band A = Exempt 0%
print(x.total_amount)            # Decimal("52340.00")

# Z Report — closes the VSCU fiscal period (POST internally — call once per day)
# TIaaS submits this automatically at 23:59 Kenya time; call manually only if needed.
z = client.reports.get_daily_z("2026-03-11")
print(z.vscu_acknowledged)       # True when VSCU day-reset completed
print(z.invoice_count)
print(z.total_vat)

# A second call for the same date raises ZReportAlreadyIssuedError (HTTP 409).
# The VSCU day-reset is irreversible — do not retry on this exception.
try:
    z = client.reports.get_daily_z("2026-03-11")
except ZReportAlreadyIssuedError:
    pass  # Already submitted — this is expected if the scheduler already ran

# Async
x = await client.reports.get_x_report("2026-03-11")
z = await client.reports.get_daily_z("2026-03-11")
```

---

## Credit Notes (Category 7)

Issue a credit note against a previously signed sale. The middleware sources amounts from the signed receipt (full reversal) or from the summed line values (partial reversal) — callers never supply a raw amount, preventing manipulation.

Since middleware V15, **multiple credit notes may be issued against one receipt** (e.g. items returned across separate visits), as long as the cumulative reversed amount does not exceed the original. Over-reversal raises `CreditNoteExceedsOriginalError` (HTTP 422).

```python
# Full reversal of the remaining balance
result = client.issue_credit_note(original_purchase_id=42, reason="Customer return")
print(result["cuInvoiceNumber"])   # Signed credit note CU number

# Partial reversal — supply the specific lines to reverse.
# Each line uses the middleware item schema: sku, itemNm, itemClsCd,
# taxTyCd, qty, and unitPrice are all required.
result = client.issue_credit_note(
    original_purchase_id=42,
    reason="Partial return",
    items=[{"sku": "SODA", "itemNm": "Soda 500ml", "itemClsCd": "50200000",
            "taxTyCd": "B", "qty": 1, "unitPrice": 140.00}],
)

# Async
result = await client.issue_credit_note(original_purchase_id=42, reason="Return")
```

```python
from kra_etims import CreditNoteExceedsOriginalError

try:
    client.issue_credit_note(original_purchase_id=42)
except CreditNoteExceedsOriginalError as exc:
    # HTTP 422 — the reversal would exceed the receipt's reversible balance.
    print(f"Already reversed {exc.already_reversed}, remaining {exc.remaining} "
          f"on purchase {exc.original_purchase_id}")
```

If a previous attempt failed terminally (VSCU rejection), it does not count against the
reversible balance — KRA never received the failed attempt, so the middleware allows
re-issuance. `CreditNoteConflictError` (HTTP 409) is now only the generic conflict
carrier on this path — it no longer means "already reversed".

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

> [!CAUTION]
> This SDK is a technical implementation tool, not tax advice. Complies with the Kenya Data Protection Act (2019). The authors are not responsible for KRA penalties, non-deductible expenses, or financial losses resulting from user error, misconfigured payloads, or middleware misapplication.

---

## Upgrading from v0.2.0


**Breaking changes in v0.3.0:**

**`requests` removed — transport unified on `httpx`.** The sync client (`KRAeTIMSClient`) now uses `httpx.Client` instead of `requests.Session`. If your code catches transport exceptions directly, update the exception types:

| v0.2.0 (`requests`) | v0.3.0 (`httpx`) |
|---|---|
| `requests.exceptions.ConnectionError` | `httpx.ConnectError` |
| `requests.exceptions.Timeout` | `httpx.TimeoutException` (or `httpx.ReadTimeout` / `httpx.ConnectTimeout`) |
| `requests.exceptions.JSONDecodeError` | `httpx.DecodingError` |

If you only catch SDK-level exceptions (`TIaaSUnavailableError`, `TIaaSAmbiguousStateError`, `KRAeTIMSError`, etc.) — no changes needed. Those exceptions are unchanged and still raised for all transport failures.

**New exception: `KRAAuthorizationError`.** HTTP 403 responses now raise `KRAAuthorizationError` (a subclass of `KRAeTIMSError`) instead of the generic base. If you have a bare `except KRAeTIMSError` handler, it still catches this — no action required unless you want to handle 403 specifically.

---

## Support

For architectural escalations or middleware orchestration support: `support@taxid.co.ke`
