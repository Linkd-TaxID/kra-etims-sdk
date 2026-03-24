# Changelog

All notable changes to kra-etims-sdk are documented here.

## [Unreleased]

### Added
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
