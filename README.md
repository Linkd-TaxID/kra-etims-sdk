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
| `PENDING_SYNC` | Queued before reaching the control unit. TaxID signs it later. | Poll `client.get_sale_status(receipt["purchaseId"])` with backoff (below) until the status changes |
| `RECONCILIATION_REQUIRED` | Automatic processing stopped. Sometimes TaxID already holds the signed receipt but has not yet stored its independent evidence copy; it retries that write, and the same `purchaseId` turns `SIGNED` once the write succeeds. Otherwise an operator reconciles it. | **Do not resubmit.** Keep polling the same `purchaseId` at a slow interval |
| `OUTCOME_UNKNOWN` | It may or may not have been signed | **Do not resubmit.** TaxID's operators reconcile it |
| `NOT_SENT`, `FAILED` | Not signed: reconciliation proved it was never applied, or the control unit rejected it | Investigate the cause before any new submission |
| anything else | A status this SDK version does not know | Treat as not signed and investigate; never as success |

Polling is the supported way to follow a sale. Webhooks are optional: whether
they are sent depends on the deployment's configuration, delivery is
at-least-once, and each delivery is signed with a per-branch secret. Do not rely
on receiving `receipt.signed`. A reasonable polling schedule starts at 2 seconds,
doubles up to 60 seconds, adds jitter, and honours `Retry-After` on HTTP 429
(`RateLimitError.retry_after_seconds`):

```python
import random, time

delay = 2.0
status = client.get_sale_status(purchase_id)
while status["status"] in ("PENDING_SYNC", "RECONCILIATION_REQUIRED"):
    time.sleep(delay + random.uniform(0, delay / 2))
    delay = min(delay * 2, 60.0)
    status = client.get_sale_status(purchase_id)
```

Failures are split by whether the request could have had an effect:

| Failure | Exception | Retry |
|---|---|---|
| Never left the client (connect error, pool timeout), or any failure on a GET | `TIaaSUnavailableError` | Yes |
| Sent, then no usable response (read timeout, dropped connection, HTTP 500/502/504), or HTTP 425 because an earlier request with this key is still running | `TIaaSAmbiguousStateError` | Look up the original reference first; preserve `exc.idempotency_key`. Do not blindly resubmit. |
| HTTP 409 `IDEMPOTENT_RESULT_LOOKUP_REQUIRED`, `IDEMPOTENT_IDENTITY_UNAVAILABLE` or `IDEMPOTENCY_SCOPE_UNBOUND`: an earlier request with this key exists but its result cannot be returned | `IdempotentOutcomeUnresolvedError` (a `TIaaSAmbiguousStateError` and `KRAConflictError`); on `issue_credit_note`, `CreditNoteOutcomeUnresolvedError` (also a `CreditNoteConflictError`) | As above: look up the original; never resubmit under a new key |
| Refused by the fiscal worker before dispatch: HTTP 403 `WORKER_DEFERRED`, HTTP 503 `WORKER_STOPPING` | `WorkerDeferredError` / `WorkerStoppingError` (`retryable = True`) | Yes, with the same key and body after a backoff |
| HTTP 503 `KRA_CONNECTIVITY_TIMEOUT` (24-hour offline ceiling) | `KRAConnectivityTimeoutError` | After connectivity is restored; inspect mutations first |
| Any other HTTP 503 on a GET | `TIaaSUnavailableError` (`exc.code` has the server code) | Yes |
| Any other HTTP 503 on a mutation | `MutationServiceUnavailableError` (a `TIaaSUnavailableError` and `TIaaSAmbiguousStateError`, `retryable = False`) | Inspect the original first; catch it before any `except TIaaSUnavailableError` that retries |
| HTTP 429 | `RateLimitError` (`exc.retry_after_seconds`) | Yes, after the delay; nothing was processed |

The implemented server cache replays a stored response for the same key and exact
request-body bytes; that response may still be uncertain. It is not an exactly-once
or indefinite-retention guarantee. Different bytes under the same key raise
`KRAConflictError` (`IDEMPOTENCY_KEY_REUSED`). The SDK serializes request bodies
itself (compact UTF-8 JSON), so the bytes for the same payload do not depend on
the installed httpx version. Preserve the original invoice and
reference instead of rebuilding them with a new date or identifier. If you omit
`idempotency_key`, the SDK uses `"{tin}:{invcNo}"` and warns.

```python
try:
    receipt = client.submit_sale(invoice, idempotency_key="INV-0001")
except TIaaSAmbiguousStateError:
    # submit_sale maps invoice.invcNo to the API clientReference.
    receipt = client.find_sale(client_reference=invoice.invcNo)
    # Inspect receipt["status"]; unresolved or missing results need investigation,
    # not a resend. A missing sale raises KRAeTIMSError (HTTP 404).
```

## Credit notes

```python
client.issue_credit_note(
    original_purchase_id=42, reason="Customer return",
    client_reference="refund-event-42", idempotency_key="refund-operation-42",
)
```

A stable `client_reference` or `idempotency_key` is required; retain it across retries.
Full original corrections and explicit original-line selections use the middleware canonical contract. Partial/repeated combinations remain synthetic-only until an official device contract is established; production corrections keep their separate approval gate. Creator-sale and platform-fee invoices need separate corrections
under their original issuer grants. An uncertain correction reserves its amount and
requires reconciliation; it cannot be assumed safe to replace with another request.
Over-reversal raises `CreditNoteExceedsOriginalError`, with exact Decimal balances.

## Reports

```python
x = client.reports.get_x_report("2026-09-25")   # read-only, safe any time
x.total_amount, x.band_b.taxable_amount, x.band_b.tax_amount

z = client.reports.get_daily_z("2026-09-25")    # finalises a local aggregate; no remote fiscal close
```

Configured scheduling may finalize the local aggregate after unresolved work settles. This is neither device fiscal closure nor KRA acknowledgement. An already completed report raises `ZReportAlreadyIssuedError`; other conflicts retain their original code for investigation.

## Other operations

```python
client.get_sale_status(purchase_id)                       # current status and receipt fields
client.submit_stock_adjustment([StockAdjustmentLine(
    itemCd="SKU1", itemNm="Laptop", ioType="M", qty=10, prc=5000, totDcAmt=0, taxTyCd="B",
)], remark="Stock in", idempotency_key="stock-in-1")  # ioType: M in, A adjust out, I issue out
                                                          # reuse the same key on any retry
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

**TaxID Links** (`client.gateway`) records supplier outreach and consent. Fiscal
reverse-invoicing submission remains unavailable pending a verified dedicated KRA
contract and adapter; a feature flag cannot enable it. Inspect status gate reasons.

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
etims queue flush                        # sends pending rows; polls PENDING_SYNC (accepted) rows
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
| `CreditNoteExceedsOriginalError` | 422: the credit notes would exceed the receipt (`CreditNoteQuantityExceedsOriginalError` when a returned line exceeds its remaining quantity) |
| `KRAConflictError` | 409. `exc.code` is `FISCAL_DAY_CLOSED` (the date is already Z-closed) or `ETIMS_NOT_INITIALIZED` (the branch has not completed its handshake). |
| `CreditNoteConflictError` | A `KRAConflictError` raised on a credit-note request (`CreditNoteOutcomeUnresolvedError` for the unresolved idempotency codes) |
| `ZReportAlreadyIssuedError` | 409 `Z_REPORT_ALREADY_ISSUED`: a Z report already exists for that date (also a `KRAConflictError`) |
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

TaxID is platform agnostic: these SDK capabilities are available to any authorised
application. The [withholding ledger](docs/withholding-ledger.md) records receipt,
deduction and refund accounting; configured calculations and deductions are sandbox
simulations, and production treatment remains unconfigured.

[Scoped application sessions and operator-assisted evidence](docs/authorised-operations.md)
support separate authorised issuers with sync/async parity. Reviewed remittance records
remain supplied, unverified evidence; they do not execute KRA payments, filing or
certificate issuance. Reverse invoicing remains gated.

The [synthetic marketplace example](docs/synthetic-marketplace.md) exercises the SDK
against isolated middleware and PostgreSQL, including process restart. It is one example,
not a restriction on supported platforms. Simulated receipts, payments and remittance
documents do not establish real-KRA acceptance.

[Connected creator and retail demonstrations](docs/connected-demonstrations.md) cover
separate invoice obligations, withholding estimates and original-reference recovery.
Use [control-unit onboarding](docs/control-unit-onboarding.md) and
[registration recovery](docs/control-unit-registration.md) for explicit VSCU/OSCU
provisioning, durable initialization and retained original operations.

[Retail operations](docs/retail-operations.md) and [stock recovery](docs/stock-recovery.md)
cover original itemised corrections, explicit stock movements and local closure.
[Withholding operations](docs/withholding-operations.md) adds versioned assessed
sandbox policies, separate approval, recorded deductions and supplied-evidence batching.
Production withholding and automatic remittance remain gated; certificates remain
supplied and unverified.

[Product integration](docs/product-integration.md) exercises both synthetic adapters
through TaxID HTTP and PostgreSQL with distinct seller/platform issuer grants and an
installed SDK wheel. `capabilities()`, `metadata(kind)`, `initialize_branch()` and
`device_monitoring()` use the stored branch adapter. Readiness and engineering support
do not establish KRA approval, payment, remittance or authentic certificates.
