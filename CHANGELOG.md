# Changelog

All notable changes to kra-etims-sdk are documented here.

## [0.5.1]

### Changed
- Published package metadata: added `[project.urls]` (Homepage, Documentation,
  Repository, Changelog) so the PyPI project page links to the docs and source
  instead of showing bare/`None` fields.

### Security / hygiene
- Removed a hardcoded real KRA taxpayer PIN from the example day-scripts, test
  fixtures, and the concurrent-stress harness; the stress script now reads the
  PIN from `SANDBOX_TIN`. Campaign/day transcripts (which embed real KRA receipt
  signatures) are no longer tracked and are git-ignored going forward.

## [Unreleased]

### Added
- **`bulk_import_items(csv_path)`** (sync + async) — multipart upload to the middleware's
  `POST /v2/etims/items/bulk-import` (Track C5). Per-row failures don't abort the upload;
  check `failed`/`results` on the returned dict rather than the HTTP status alone.
- **`get_sale_status(invc_no)`** (sync + async) — polls `GET /v2/etims/sales/{invcNo}/status`
  (Track C4), the fallback for a `submit_sale()` that returned 202 PENDING_SYNC when no
  webhook is configured or a delivery was missed. Raises `KRAeTIMSError` on 404 (no sale
  with that `purchaseId` for the authenticated tenant).
- **`OSCUUnavailableError`** — the middleware's `PurchaseService` can now sign through
  either VSCU or OSCU depending on a branch's `TenantDevice.controlUnitType` (TaxID
  V18 migration). OSCU is KRA-hosted and always-online-only — it has no 24-hour offline
  ceiling at all, unlike VSCU. A 503 from a transient OSCU failure is therefore not the
  same condition as `KRAConnectivityTimeoutError` and needed its own exception. Carries
  `oscu_code` (the raw KRA OSCU Spec v2.0 §4.18 result code, e.g. `"894"`) when the
  middleware's response body includes it.

### Fixed
- **Every 503 from TIaaS was raised as `KRAConnectivityTimeoutError`, unconditionally,
  without reading the response body** — correct for VSCU's 24-hour offline ceiling, wrong
  the moment a transient OSCU failure also returns 503. `_raise_for_503()` now checks the
  response body for an `oscu_code` property (set by the middleware's
  `GlobalExceptionHandler.handleOscuSigning`) and raises `OSCUUnavailableError` instead
  when present. Falls back to the historical behavior for any 503 without that marker —
  a bare 503 with no body, or a `vscu_code` body, is unaffected.
- **`examples/basic_invoice.py` demonstrated a dead auth path and non-existent response
  fields** — the constructor showed OAuth2 `client_id`/`client_secret` with no `api_key`,
  and `response['invoiceSignature']` / `response.get('rcptNo')` / `response.get('qrCode')`
  — none of which the middleware's sale response contains. Switched to the working
  `api_key` mode (matching `examples/javahouse_day.py`/`petrol_station_day.py`) and the
  real field names (`receiptSignature`, `cuInvoiceNumber`, `kraQrPayload`), confirmed
  against `PurchaseService.buildResponse` in the middleware.
- **README Track 3 / Async Client quickstarts showed the same dead OAuth2 path** — the
  middleware has no `/oauth/token` route at all (`_authenticate()`'s POST to
  `{base_url}/oauth/token` 401s/404s against the real server today). Quickstarts now use
  `api_key`; the Authentication section's Mode 2 example is annotated with this caveat.
- **`__version__` fallback string was `0.4.0`** — stale by one release; `pyproject.toml`
  is `0.5.0` (matches the latest PyPI release). Only affects the `importlib.metadata`
  lookup-failure fallback (e.g. running from source without installed package metadata).

## [0.5.0] — 2026-07-08

### Fixed
- **`save_item()` sent the wrong wire schema to the wrong path — HTTP 404/400 on every
  call** — the client POSTed the KRA-native `ItemSave` dump to `/v2/etims/item`
  (singular); the middleware's item registry lives at `POST /v2/etims/items` and expects
  `sku`/`itemNm`/`itemClsCd`/`taxTyCd`/`qty`/`unitPrice`. `save_item` now transmits
  `models.to_middleware_item_payload(...)` — the same wire-contract repair `submit_sale`
  received in 0.4.0. No call-site changes required.

### Added
- **`ItemSave.pkgUnitCd` / `qtyUnitCd` / `bcd`** — optional KRA unit codes (spec §4.5/§4.6)
  and barcode, passed through to the middleware registry (e.g. `qtyUnitCd="LTR"` for fuel
  sold by the litre). Middleware defaults both unit codes to `"U"` when omitted.
- **`pmtTyCd` transmitted on sales** — `SaleInvoice.pmtTyCd` (KRA spec §4.7: `01` cash,
  `05` card, `06` mobile money, …) now reaches the middleware and the signed VSCU
  receipt. Previously the middleware hardcoded `01` (cash) on every receipt.

### Changed
- **Mixed-band invoices are now supported (previously rejected client-side)** — the
  middleware books per-line tax bands as of V14, so `to_middleware_sale_payload` no longer
  raises `ValueError` on a mixed ticket. For a mixed invoice it emits the `items` array
  (each line under its own band) and omits the receipt-level `taxBand`; single-band
  invoices keep the flat payload unchanged. `ItemDetail` gains an optional `itemClsCd`
  (UN/CEFACT commodity code) that is **required on every line of a mixed-band invoice**.
  Verified live against the KRA sandbox (exempt bread + standard soda on one receipt).
- **Credit notes: over-reversal is now `422 CREDIT_NOTE_EXCEEDS_ORIGINAL`, not `409`** —
  the middleware permits multiple credit notes per receipt (V15) up to the reversible
  balance. New `CreditNoteExceedsOriginalError` (HTTP 422; carries `remaining` /
  `already_reversed`) is raised on over-reversal and is exported from the package root.
  `CreditNoteConflictError` is retained as the generic 409 carrier (still used by the
  Z-report path). Verified live against the KRA sandbox.

## [0.4.0] — 2026-07-07

### Fixed
- **`submit_sale()` sent the wrong wire schema — HTTP 400 on every call** — both sync and
  async clients were POSTing the raw KRA-native `SaleInvoice` dump to `/v2/etims/sale`,
  which the TIaaS middleware rejects (it expects its flat schema: `supplierPin`, `amount`,
  `invoiceDate`, …). Verified live 2026-07-04 against the deployed middleware. `submit_sale`
  now transmits `models.to_middleware_sale_payload(...)`. No call-site changes required —
  the `SaleInvoice` you construct is unchanged; only the bytes on the wire differ. This is
  the fix that produced the first real KRA-signed receipts through the SDK
  (`KRACU0300003881` NS, KRA sandbox).
- **`StockAdjustmentLine.pkgUnitCd` / `qtyUnitCd`** — now default to `NT` / `U`
  (KRA spec §4.5/§4.6 standard codes). `None` was rejected by middleware validation,
  making stock adjustments impossible without explicitly setting both on every line.
- **`ZReportAlreadyIssuedError` importable from package root** — the README-documented
  `from kra_etims import ZReportAlreadyIssuedError` previously raised `ImportError`;
  it is now exported from `kra_etims.__init__`.

### Changed
- **`CreditNoteConflictError` carries the conflicting receipt** — on a middleware 409 the
  exception now exposes `existing_credit_note_id` and `existing_cu_invoice_no` parsed from
  the response body, so callers can reference the already-issued credit note instead of
  re-parsing the error message.

### Documentation
- **Error reference corrected against live VSDC 2.0.6 sandbox evidence** (now at
  https://docs.taxid.co.ke/): duplicate-invoice replay on the VSCU path returns **899**,
  not 994 (994 idempotent-success semantics are OSCU-scoped); the 902 "device installed"
  response body is `data: null` — it does **not** return the existing `cmcKey`; the `"00"`
  success variant is marked unconfirmed (live VSDC 2.0.6 emits `"000"`); new FAQs cover
  real ONLINE-mode signing latency (~2s — use a ≥15s timeout), HTTP-200 error envelopes,
  and the `vsdcRcptPbctDate` field-name mismatch.

## [0.3.0] — 2026-04-29

Consolidates changes shipped across 0.2.0 (2026-04-04) and 0.3.0 (2026-04-29);
these releases were published without cutting changelog sections at the time.

### Added
- **GavaConnect direct transport** and the **`etims` CLI**
  (`pip install "taxid-etims[cli]"`, entry point `etims`) — the 0.3.0 headline features.

### Fixed
- **`examples/basic_invoice.py` tax band inversion** — all three example items had wrong
  bands and completely inverted comments. MacBook Pro was Band A (0% Exempt) with a comment
  claiming "16% VAT"; Diesel was Band B (16%) with a comment claiming "0% Zero-Rated"; Maize
  Flour was Band D (Non-VAT) with a comment claiming "0% Exempt". Corrected to Band B (laptop),
  Band A (maize flour), Band E (diesel). An ERP integrator copy-pasting this example would have
  submitted invoices with incorrect VAT band classifications to KRA.
- **`_KRA_SUCCESS_CODES` incomplete** — live KRA GavaConnect responses emit `resultCd="0"` and
  `"0000"` which were absent from the frozenset. Legitimate signed receipts were raising
  `KRAeTIMSError` on production traffic. Also added `"001"` (empty-list — no records match
  query) which is not an error but was previously raised as one, breaking day-one `syncData()`
  calls for newly onboarded devices.
- **`init_device.py` auth bypass removed** — a `DUMMY_INIT_TOKEN` injected directly into
  `client._access_token` was left in the initialization helper script from before API key auth
  was wired into the middleware. Replaced with a hard failure if neither `TAXID_API_KEY` nor
  OAuth2 credentials are set.

### Changed
- **`submit_sale()` idempotency key auto-generation** — if `idempotency_key` is omitted,
  both the sync and async clients now auto-generate `"{tin}:{invcNo}"` and emit a `UserWarning`
  at the call site (`stacklevel=2`). This ensures middleware deduplication is always active and
  prompts integrators to supply explicit keys. Callers passing an explicit key are unaffected.
- **Tax Band E advisory** — Band E documentation updated to flag that the Finance Act 2023
  (Kenya) may have changed the 8% petroleum rate. Use `ETIMS_TAX_RATE_E` env var to override;
  confirm the current rate with KRA at timsupport@kra.go.ke before using Band E on new items.

### Added
- **New result codes in `KRA_ERROR_MAP`:**
  - `"994"` → `KRADuplicateInvoiceError` (`is_idempotent_success=True`) — invoice already
    processed on a prior retry; receipt exists on KRA; do not resubmit with a new number.
  - `"901"` → `KRAeTIMSError` — device serial not approved; contact timsupport@kra.go.ke.
  - `"902"` → `KRADuplicateInvoiceError` — device already initialized; existing cmcKey valid;
    do not re-initialize.
  - `"921"` → `KRAeTIMSError` — VSCU sequence error; saveSales must precede saveInvoice.

### Fixed (Test Infrastructure)
- **`sys.modules.clear()` bomb removed from `test_phase2.py`** — `TestRenderKraQrString.test_generate_qr_bytes_raises_import_error_without_qrcode` previously called `sys.modules.clear()` in its `finally` block, destroying the entire module registry for the process. This caused `ModuleNotFoundError` in any test running in parallel (pytest-xdist, concurrent fixtures). Replaced with `monkeypatch.setitem(sys.modules, "qrcode", None)` — pytest restores the original value on test teardown with zero process-wide impact.
- **Float literals in financial test data replaced with `Decimal`** — `test_vscu_resilience.py`, `test_async.py`, and `test_schema.py` were constructing `SaleInvoice` objects with `totTaxblAmt=0.0`, `totTaxAmt=0.0`, `totAmt=0.0`. Float literals in financial fields silently validated the float ingestion path instead of the `Decimal("0.00")` path required by the SDK's own contract. All instances replaced with `Decimal("0.00")`.

### Added (Test Infrastructure)
- **`tests/conftest.py`** — shared pytest fixtures providing realistic KRA response envelopes. Replaces the `json={}` and `json={"status":"success"}` stubs that left response parsing untested. Includes `kra_success_response()`, `kra_empty_response()`, `kra_error_response()`, `kra_vscu_signing_response()` (with §6.23.8 QR format: `ddMMyyyy#HHmmss#cuNumber#cuReceiptNumber#internalData#signature`), and named fixtures for common scenarios.
- **`tests/test_schrodinger.py`** — Schrödinger's Invoice test suite covering the split-brain scenario where the VSCU JAR signs a receipt but the middleware DB commit fails: `ReadTimeout` on POST → `TIaaSAmbiguousStateError`; HTTP 500 on POST → `TIaaSAmbiguousStateError`; `ChunkedEncodingError` → `TIaaSAmbiguousStateError`; GET 500 → `TIaaSUnavailableError` (read-only, no signing side-effect); retry with same idempotency key sends `X-TIaaS-Idempotency-Key` for server-side deduplication.

### CI/CD (contributor-facing)
- **Python 3.13 added to test matrix** — `ci.yml` and `publish.yml` now test against
  3.10, 3.11, 3.12, and 3.13. The `pyproject.toml` classifier already claimed 3.13
  support; it is now verified by CI before every push and release.
- **Version consistency gate in publish workflow** — creating a GitHub release with a
  tag that does not exactly match the `version` field in `pyproject.toml` now fails the
  pipeline immediately, before any build or PyPI upload step. Bump `version` in
  `pyproject.toml` and retag the release if this check fails.
- **GitHub Actions pinned to commit SHA** — all workflow actions (`actions/checkout`,
  `actions/setup-python`, `actions/upload-artifact`, `actions/download-artifact`,
  `pypa/gh-action-pypi-publish`) are now pinned to their verified commit SHA rather than
  a mutable version tag. This closes the supply chain attack surface demonstrated by the
  March 2025 tj-actions incident.
- **Security scanning added to CI and publish gates** — `pip-audit` (dependency CVE
  check) and `bandit -r src/ -ll` (SAST, medium/high severity) now run on every push to
  `main` and as a required gate before the PyPI publish build step.

### Added
- **`ZReportAlreadyIssuedError`** — HTTP 409 on `get_daily_z()` now raises
  `ZReportAlreadyIssuedError` (subclass of `KRAeTIMSError`) instead of the generic
  `CreditNoteConflictError`. The VSCU day-reset command is irreversible (KRA TIS v2.0
  §21.6.1); callers can now distinguish a Z-report 409 (safe — already done, do not
  retry) from a credit note 409 (already reversed) without parsing the error message.
  `ZReportAlreadyIssuedError` carries a `report_date` attribute.

### Fixed
- **Band label inversion in `XReport` and `ZReport`** — `band_a` through `band_e`
  field comments were inverted vs KRA TIS v2.0 §4.1. `band_a` was labeled "16%
  Standard VAT"; the correct label is "Exempt (0%)". `band_b` is Standard VAT (16%).
  Confirmed from the official KRA TIS for OSCU/VSCU v2.0 (April 2023), p.8 receipt
  sample and p.10 credit note ("TOTAL B-16.00%"). **Field names are unchanged** —
  only comments corrected. Callers reading `band_b` for standard VAT were already
  correct; callers reading `band_a` expecting 16% figures were receiving exempt (0%)
  amounts.

### Removed
- **`sanitize_kra_url` decorator removed from `middleware.py`** — this decorator stripped
  whitespace from all string arguments to any decorated function, including business data
  fields (`buyer_name`, `item_description`). It was solving the wrong tier's problem: the
  KRA GavaConnect trailing-space URL bug is handled server-side by the TIaaS middleware's
  `TrailingSpaceInterceptor`. The decorator was never applied in `gateway.py` (dead code).
  If your code imports `sanitize_kra_url` from `kra_etims.middleware`, remove that import —
  no replacement is needed.

### Added
- **`ItemDetail.splyAmt`, `dcRt`, `dcAmt`** — supply amount, discount rate, and discount
  amount fields now present on `ItemDetail` with defaults of `Decimal("0.00")`. These
  mirror `ResolvedItemDto` in the TIaaS middleware and are required by the VSCU JAR's
  `salesList` contract for discounted line items. Non-discounted item construction is
  unchanged — all three fields default to zero.
- **`DataSyncRequest.lastReqDt` format validation** — Pydantic `@field_validator` now
  enforces the `YYYYMMDDHHmmss` (14-digit) format at the SDK boundary. The VSCU JAR
  returns error E31 on malformed `lastReqDt`; this catches it before the network call.

### Changed
- **`InvoiceBase.custNm` default** — changed from required (no default) to `"N/A"`.
  B2C (retail) invoices have no identifiable customer; previously callers got a Pydantic
  validation error instead of a usable default. Supply the actual name for B2B sales.
  Community implementations and the KRA eTIMS Lite UI use `"N/A"` as the de-facto
  standard for anonymous retail customers.
- **PyPI package renamed from `kra-etims-sdk` to `taxid-etims`** — the name
  `kra-etims-sdk` was registered by a third party before this project published.
  Install command is now `pip install taxid-etims`. The Python import is
  unchanged: `from kra_etims import ...` still works exactly as before.

### Added
- **Optional OpenTelemetry instrumentation** (`pip install "kra-etims-sdk[otel]"`) — adds
  `opentelemetry-api` as an optional dep. When installed, `submit_sale`,
  `issue_credit_note`, `flush_offline_queue`, and the core `_request` dispatcher emit
  named spans (`kra_etims.*`) compatible with any OTLP-capable backend (Jaeger, Tempo,
  Honeycomb, etc.). Without the extra the SDK is unchanged — every span call is a no-op
  context manager. Follows the [OTel library instrumentation spec](https://opentelemetry.io/docs/specs/otel/library-guidelines/):
  libraries depend only on the API, never the SDK.
- CI workflow (`.github/workflows/ci.yml`) — runs `pytest` across Python 3.10, 3.11, and
  3.12 on every push and PR to `main`
- PR template (`.github/pull_request_template.md`)
- `issue_credit_note()` — Category 7 credit note submission with `CreditNoteConflictError`
  raised on HTTP 409 (KRA prohibits duplicate credit notes per original invoice)
- `submit_stock_adjustment()` — Category 8 stock adjustment with typed
  `StockAdjustmentItem` payload
- `submit_reverse_invoice()` deprecated in favour of `issue_credit_note()`
- Error code reference site: https://linkd-taxid.github.io/kra-etims-sdk/
  Covers all 30 resultCd values including production codes absent from the official
  KRA OSCU Specification v2.0, with per-error HTML pages, JSON endpoint, FAQ, and
  sitemap for AI crawler indexing

### Fixed
- **Tax band inversion** — all Javadoc, type hints, and README examples had A=16%,
  B=0% which is backwards. Correct mapping:
  A=0% Exempt, B=16% Standard VAT, C=0% Zero-Rated, D=0% Non-VAT, E=8% Special Rate
- `_is_kra_success()` — replaced `lstrip("0")` trick with explicit frozenset
  `{"00", "000"}`. The old approach silently accepted any all-zero string. VSCU
  emits `"00"` not `"000"`; GavaConnect emits `"0000"`. Both were misclassified as
  failures by any check that only tested `== "000"`
- `resultCd 001` handling — was raising `KRAeTIMSError` on empty result sets from
  `selectTrnsPurchaseSummary` and stock queries. 001 is not an error; treat as `[]`
- `resultCd 994` on retry — offline queue flush was re-raising instead of treating
  as idempotent success; fixed in `flush_offline_queue`

### Changed
- `exceptions.py` module docstring expanded with critical integration facts and
  link to full result code reference
- `KRAeTIMSError` base class docstring includes reference URL for discoverability
  in IDE hover and generated SDK documentation

## [0.1.0] — 2026-03-01

### Added
- Initial release
- Sync client (`KRAeTIMSClient`) and async client (`AsyncKRAeTIMSClient`)
- Durable offline queue with PostgreSQL-backed `flush_offline_queue`
- `_KRA_SUCCESS_CODES` frozenset and `_is_kra_success()` helper
- Full exception taxonomy mapping KRA result codes to typed Python exceptions
- Category support: sales (OSCU + VSCU paths), purchases, stock, item registry,
  customer registry, branch management, notices
