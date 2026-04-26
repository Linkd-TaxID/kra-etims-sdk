# Changelog

All notable changes to kra-etims-sdk are documented here.

## [Unreleased]

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
