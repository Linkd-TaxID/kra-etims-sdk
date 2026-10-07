# taxid-etims

A Python client for [TaxID](https://taxid.co.ke), fiscal infrastructure embedded
in existing invoicing, accounting, POS and ERP software. TaxID retains original
transaction references and branch-scoped receipt evidence for safe recovery.

Current invited access simulates receipt workflows; it issues no fiscal invoices
and makes no KRA submissions. Production access requires merchant authorization,
real KRA evidence, operational acceptance and applicable KRA approval.

The SDK also includes offline VAT arithmetic and KRA GavaConnect PIN/TCC
lookups; those lookups require separate KRA developer credentials.

```bash
pip install taxid-etims          # core: httpx + pydantic
pip install "taxid-etims[cli]"   # + the `etims` command
pip install "taxid-etims[qr]"    # + QR image rendering
pip install "taxid-etims[otel]"  # + OpenTelemetry spans
```

Python 3.10 or later. The import name is `kra_etims`. KRA `resultCd` reference:
[docs.taxid.co.ke](https://docs.taxid.co.ke). Changes: [CHANGELOG.md](CHANGELOG.md).

| Feature | Needs |
|---|---|
| `calculate_item`, `build_invoice_totals`: VAT splitting and invoice totals | Nothing. Works offline. |
| `GavaConnectClient`: PIN lookup, TCC check | KRA developer credentials ([developer.go.ke](https://developer.go.ke)) |
| `KRAeTIMSClient`: signing, credit notes, reports, stock | A TaxID API key |

## Quickstart

Request developer access on the TaxID landing page, open your private
invitation and create a branch API key in the workspace. Store the key as
`TAXID_API_KEY` on your server and set `TAXID_API_URL` to that workspace’s API
origin. The invitation opens the workspace; it is not the application API key.
No client ID or client secret is required for TaxID API requests. Use synthetic
data and your assigned branch PIN in the example below.

```python
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from kra_etims import KRAeTIMSClient, SaleInvoice, calculate_item, build_invoice_totals

client = KRAeTIMSClient(
    api_key=os.environ["TAXID_API_KEY"],
    base_url=os.environ["TAXID_API_URL"],
)

items = [calculate_item("Consulting", "SRV-001", 5800, "B")]   # 5800 incl. 16% VAT
invoice = SaleInvoice(
    tin="A000123456B", bhfId="00", invcNo="INV-0001",
    confirmDt=datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S"),
    itemList=items, **build_invoice_totals(items),
)

receipt = client.submit_sale(invoice, idempotency_key="INV-0001")
receipt["status"]            # "SIGNED" in the simulator is not KRA acceptance
receipt["cuInvoiceNumber"]   # simulated receipt identifier
receipt["receiptSignature"], receipt["kraQrPayload"], receipt["sdcId"], receipt["vscuTimestamp"]
```

`tin` must match the branch your key is bound to; use the assigned synthetic
PIN in the simulator. Inspect the original invoice reference and practise
failure handling before pursuing real KRA testing.
`confirmDt` is `yyyyMMddHHmmss`.

## Configuration

| Setting | Constructor | Environment (used when constructor argument is omitted) | Default |
|---|---|---|---|
| API key | `api_key=` | `TAXID_API_KEY` | none |
| API URL | `base_url=` | `TAXID_API_URL` | `https://api.taxid.co.ke` |

Explicit constructor values override environment defaults for both sync and async
clients. This supports separate branch clients in the same process.

The key is sent as `Authorization: Bearer <key>` and idempotency keys as
`Idempotency-Key`. These headers need a TaxID server that accepts them; against
an older server that only reads `X-API-Key`, stay on `taxid-etims<0.6`. The
`client_id`/`client_secret` arguments are for OAuth, which the TaxID API does
not offer, so always pass `api_key`.

`repr(client)` and exception messages never contain the key.

## Tax bands

SDK default band rates are based on the historical KRA v2.0 code table.
Use current reference data and merchant classification before fiscal submission.

| Band | Rate | Meaning |
|---|---|---|
| A | 0% | Exempt (no input credit) |
| B | 16% | Standard VAT |
| C | 0% | Zero-rated (input credit allowed) |
| D | 0% | Non-VAT |
| E | 8% | Historical special-rate default; not a current commodity classification. |

Override a rate with `ETIMS_TAX_RATE_A` … `ETIMS_TAX_RATE_E` (for example
`ETIMS_TAX_RATE_E=0.16`).

## Tax arithmetic

`calculate_item(name, item_code, unit_price, band, qty=1, *, price_is_inclusive=True,
pkg_unit_cd="NT", qty_unit_cd="U", discount=None, discount_rate=None)` returns an
`ItemDetail`. Every input goes through `Decimal(str(x))` and rounds
`ROUND_HALF_UP` to the cent.

```python
calculate_item("Laptop", "SKU1", 5800, "B")                    # taxblAmt 5000.00  taxAmt 800.00  totAmt 5800.00
calculate_item("Fee", "SKU2", 1000, "B", price_is_inclusive=False)  # taxblAmt 1000.00  taxAmt 160.00  totAmt 1160.00
calculate_item("Widget", "SKU3", 100, "B", qty=10)             # totAmt 1000.00  taxblAmt 862.07  taxAmt 137.93
calculate_item("Measured service", "SKU4", "209.40", "B", qty="15.456")  # qty 15.4560  totAmt 3236.49
```

- VAT is split from the line total (`taxblAmt = totAmt / (1 + rate)`), which is
  exactly how the server checks it.
- Quantities keep 4 decimal places for amount calculations. The VSCU stores
  `qty` as `decimal(11,2)`, so the server sends `15.46` but keeps your amounts.
- `build_invoice_totals(items)` returns `totItemCnt`, `totTaxblAmt`, `totTaxAmt`
  and `totAmt`. It absorbs a cent of rounding residue so that
  `totTaxblAmt + totTaxAmt == totAmt` always holds.
- Model fields reject `float`. Pass `Decimal`, `str` or `int`.

## Mixed bands and discounts

A receipt with more than one band, or any discount, is sent line by line.
Every line then needs an `itemClsCd` (a KRA commodity code), and `submit_sale`
raises `ValueError` if one is missing.

```python
flour = calculate_item("Maize flour 2kg", "FLOUR-2KG", 200, "A", qty=2)
soda  = calculate_item("Soda 500ml", "SODA-500", 70, "B", qty=2)
flour.itemClsCd = "10101601"
soda.itemClsCd  = "5059690800"

svc = calculate_item("Service", "SVC-A", 650, "B", qty=5, discount="50")
# splyAmt 3250.00  dcAmt 50.00  totAmt 3200.00  taxblAmt 2758.62  taxAmt 441.38
svc.itemClsCd = "10101601"
```

`discount=` is an amount and `discount_rate=` a percentage. KRA validates `dcRt`
as a whole percent even though the spec types it `NUMBER(5,2)`
([issue #31](https://github.com/Linkd-TaxID/kra-etims-sdk/issues/31)). The server
therefore sends a whole-percent discount as `dcRt` and anything else as a net
unit price, so the signed total always equals what the customer paid. To apply
a basket-level discount, spread it across the lines within each band.

If you set `dcAmt`/`dcRt` on an `ItemDetail` yourself, `totAmt` must equal
`qty × prc − dcAmt`, and when both are set, `dcAmt` must equal
`round(qty × prc × dcRt / 100, 2)`.

## Outcomes, retries and idempotency

Check `receipt["status"]`. HTTP success does not mean the sale is signed.

| `status` | Meaning | What to do |
|---|---|---|
| `SIGNED` | Control-unit receipt persisted; not proof of central KRA acceptance | Inspect it; simulator receipts are test output only |
| `PENDING_SYNC` | Queued before reaching the control unit. TaxID signs it later. | `client.get_sale_status(receipt["purchaseId"])` until `SIGNED` |
| `OUTCOME_UNKNOWN`, `RECONCILIATION_REQUIRED` | It may or may not have been signed | **Do not resubmit.** TaxID's operators reconcile it. |

Failures are split by whether the request could have had an effect:

| Failure | Exception | Retry |
|---|---|---|
| Never left the client (connect error, pool timeout), or any failure on a GET | `TIaaSUnavailableError` | Yes |
| Sent, then no usable response (read timeout, dropped connection, HTTP 500/502/504), or HTTP 425 because an earlier request with this key is still running | `TIaaSAmbiguousStateError` | Look up the original reference first; preserve `exc.idempotency_key`. Do not blindly resubmit. |

The implemented server cache replays a stored response for the same key and exact
request-body bytes; that response may still be uncertain. It is not an exactly-once
or indefinite-retention guarantee. Different bytes under the same key raise
`KRAeTIMSError` (`IDEMPOTENCY_KEY_REUSED`). Preserve the original invoice and
reference instead of rebuilding them with a new date or identifier. If you omit
`idempotency_key`, the SDK uses `"{tin}:{invcNo}"` and warns.

```python
try:
    receipt = client.submit_sale(invoice, idempotency_key="INV-0001")
except TIaaSAmbiguousStateError:
    # submit_sale maps invoice.invcNo to the API clientReference.
    import httpx
    response = httpx.get(
        os.environ["TAXID_API_URL"].rstrip("/") + "/v2/etims/sales/search",
        params={"clientReference": invoice.invcNo},
        headers={"Authorization": "Bearer " + os.environ["TAXID_API_KEY"]},
        timeout=30,
    )
    response.raise_for_status()
    receipt = response.json()
    # Inspect status; unresolved or missing results need investigation, not resend.
```

## Credit notes

```python
client.issue_credit_note(original_purchase_id=42, reason="Customer return")   # full original amount

client.issue_credit_note(original_purchase_id=42, reason="One returned", items=[
    {"sku": "SODA-500", "itemNm": "Soda 500ml", "itemClsCd": "5059690800",
     "taxTyCd": "B", "qty": 1, "unitPrice": "70.00"},
])
```

With no `items`, the credit note reverses the whole original receipt. A receipt
can take several credit notes up to its total. After a partial return, reverse
the rest by listing the remaining lines. Going over the total raises
`CreditNoteExceedsOriginalError`, which carries `already_reversed` and
`remaining` as `Decimal`. A credit note that fails at the control unit does not count toward
the total.

## Reports

```python
x = client.reports.get_x_report("2026-09-25")   # read-only, safe any time
x.total_amount, x.band_b.taxable_amount, x.band_b.tax_amount

z = client.reports.get_daily_z("2026-09-25")    # closes the fiscal day: irreversible
```

TaxID issues the Z report automatically at 23:59 Nairobi time. A second Z
report for the same date raises `ZReportAlreadyIssuedError`. Do not retry it.

## Other operations

```python
client.get_sale_status(purchase_id)                       # current status and receipt fields
client.submit_stock_adjustment([StockAdjustmentLine(
    itemCd="SKU1", itemNm="Laptop", ioType="M", qty=10, prc=5000, totDcAmt=0, taxTyCd="B",
)], remark="Stock in")                                    # ioType: M in, A adjust out, I issue out
client.bulk_import_items("catalog.csv")                   # CSV item registration
client.initialize_device_handshake()                      # needs a branch admin key

from kra_etims import render_kra_qr_string, generate_qr_bytes     # [qr] extra
png = generate_qr_bytes(render_kra_qr_string(receipt))
```

**Flushing a local queue.** If your application stored invoices while TaxID was
unreachable, `client.flush_offline_queue(invoices)` submits them. Each result
row has `invoice_no`, `status` (`success`, `already_processed` or `error`) and
the `idempotency_key` to reuse on a resend. Success rows add `signed` and
`sale_status`. Error rows add `retryable` and `ambiguous`. The async client
submits 4 at a time by default. TaxID signs one sale per branch at a time, so
more concurrency only makes more sales come back `PENDING_SYNC`.

**TaxID Links** (`client.gateway`): reverse invoices for informal suppliers who
confirm by SMS or WhatsApp. It is disabled on TaxID servers by default.

## Async

`AsyncKRAeTIMSClient` has the same methods, awaited:

```python
async with AsyncKRAeTIMSClient(
    api_key=os.environ["TAXID_API_KEY"],
    base_url=os.environ["TAXID_API_URL"],
) as client:
    receipt = await client.submit_sale(invoice, idempotency_key="INV-0001")
```

## GavaConnect

```python
from kra_etims import GavaConnectClient, GavaConnectPINNotFoundError

kra = GavaConnectClient(consumer_key="…", consumer_secret="…")   # or GavaConnectClient.from_env()
kra.lookup_pin("A000123456B")["PINDATA"]["StatusOfPIN"]          # "Active"
kra.check_tcc("A000123456B", tcc_number="TCC2026001234")["Status"]
```

`from_env()` reads `GAVACONNECT_CONSUMER_KEY`, `GAVACONNECT_CONSUMER_SECRET` and
`GAVACONNECT_SANDBOX`. Tokens are cached and refreshed automatically.
`AsyncGavaConnectClient` is the async version.

## CLI

```bash
etims auth login --api-key txd_sb_…     # stored in the OS keyring; TAXID_API_KEY also works
etims tax calculate --price 5800 --band B
etims invoice validate invoice.json
etims invoice submit invoice.json --idempotency-key INV-0001
etims report x --date 2026-09-25
etims report z                           # asks for confirmation
etims queue status                       # local SQLite queue of unsent invoices
etims queue flush
etims pin validate A000123456B
etims tcc check --pin A000123456B --tcc-number TCC2026001234
```

Every command accepts `--json`. `etims --help` lists the rest (`device`,
`sandbox`, `auth status`).

## Concurrency and observability

- One `KRAeTIMSClient` can be shared by all threads (Celery workers, WSGI
  handlers). It wraps a thread-safe `httpx.Client`. Create it once per process.
- With the `[otel]` extra, calls emit spans (`kra_etims.submit_sale`,
  `kra_etims.request`, …) and propagate trace context. Spans never contain KRA
  PINs; the idempotency key is exported as a truncated SHA-256.

## Exceptions

All exceptions inherit from `KRAeTIMSError`.

| Exception | Raised for |
|---|---|
| `KRAeTIMSAuthError` | 401: the key is missing, wrong or revoked |
| `KRAAuthorizationError` | 403: the key's role or environment does not allow the call |
| `KRAConnectivityTimeoutError` | 503: the branch is suspended (24-hour offline limit) |
| `TIaaSUnavailableError` / `TIaaSAmbiguousStateError` | See [Outcomes, retries and idempotency](#outcomes-retries-and-idempotency) |
| `OSCUUnavailableError` | 503 from an OSCU-backed branch (the control unit is temporarily unavailable) |
| `KRADuplicateInvoiceError` | VSCU codes 12/994/902. `is_idempotent_success` is a legacy classification flag, not receipt evidence or permission to replay. |
| `KRAInvalidPINError`, `KRAInvalidItemCodeError`, `KRAInvalidBranchError`, `KRAVSCUMemoryFullError`, `KRAServerError` | VSCU codes 10, 13, 14, 11, and 20/96/99 |
| `CreditNoteExceedsOriginalError` | 422: the credit notes would exceed the receipt |
| `KRAConflictError` | 409. `exc.code` is `FISCAL_DAY_CLOSED` (the date is already Z-closed) or `ETIMS_NOT_INITIALIZED` (the branch has not completed its handshake). |
| `CreditNoteConflictError` | A `KRAConflictError` raised on a credit-note request |
| `ZReportAlreadyIssuedError` | 409: a Z report already exists for that date |
| `GavaConnectAuthError`, `GavaConnectPINNotFoundError`, `GavaConnectTCCError`, `GavaConnectError` | GavaConnect failures |

Any other 4xx/5xx raises `KRAeTIMSError` with the server's `code` and `message`.

## Upgrading to 0.6

- The server must accept `Authorization: Bearer` and `Idempotency-Key`.
- For `qty > 1`, VAT is now derived from the line total, which can change it by
  a few cents. Results for `qty = 1` are unchanged.
- `ItemDetail` rejects `float`.
- Discounts are sent to the server instead of being dropped, and a discounted
  line needs `itemClsCd`.
- `pkgUnitCd` defaults to `NT`. The previous default, `UNT`, is rejected by the
  VSCU with error 913.
- A transport failure after a POST, and 502/504 on a mutation, now raise
  `TIaaSAmbiguousStateError`.
- The async `flush_offline_queue` default concurrency is now 4 (was 50).

Full history: [CHANGELOG.md](CHANGELOG.md).

---

This SDK is an integration tool, not tax advice. You are responsible for the
tax treatment of what you submit.
