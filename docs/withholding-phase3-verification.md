# Phase 3 SDK changes and verification

9 October 2026. Directory `/home/officialnyabuto/Desktop/kra-etims-sdk`;
branch `feat/branch-sdk-onboarding-docs`; base/final HEAD
`c1e600f147a5fdcdb1ba0ee424b83523c095e7ab`. No checkout, branch, commit,
release or deployment. Existing untracked `tests/test_audit.py` was preserved.
Read the supplied AGENTS.md instructions, repository CLAUDE.md and middleware
Phase 1/2 handoffs. The explicit Phase 3 test requirement governs the earlier
audit-only instruction not to write tests.

Changes: new `kra_etims.withholding` models and synchronous/asynchronous
interfaces; `client.withholding` properties; Decimal response parsing limited to
`/v2/withholding/`; synthetic sandbox example; README and ledger contract;
model/wire tests and an actual middleware HTTP receipt fixture. Fiscal transport,
existing invoice models and tax-band mappings were preserved. No version bump.

Compatibility target: TaxID HEAD `b4fd683056ceb990f4216f87601a39e2f4887f96`
plus its uncommitted Phase 3 V40 migration and `/v2/withholding` controller.
Models match Java record field names; monetary requests use decimal strings,
responses preserve exact Decimal values. Tenant/branch/environment are derived
from credentials. Delegated scopes are `withholding:read` / `withholding:write`.
The fixture was produced by the actual Java controller and transactional
PostgreSQL ledger through MockMvc, not hand-written as SDK evidence.

Executed:

```sh
.venv-test/bin/pytest -q -p no:cacheprovider \
  tests/test_withholding.py tests/test_item_wire_contract.py \
  tests/test_payload_integrity.py tests/test_error_contract_compat.py \
  tests/test_bulk_import_and_sale_status.py tests/test_idempotency.py
```

**32 passed, zero failures:** 14 ledger cases and 18 existing compatibility
checks. Covers all six sync/async routes, decimal-string requests, exact numeric
responses beyond binary float precision, Java HTTP fixture parsing, null
unconfigured calculations, and rejection of float/bool/nonfinite/negative/
overprecision money, invalid IDs/time zones and eTIMS tax-band fields.
`git diff --check` passed. The new module, example and test source compile.
No live SDK/API/KRA call or example execution was performed.

Production withholding treatment remains explicitly unconfigured/unsupported.
Configured arithmetic/deduction policies are sandbox-only. Remitted amounts
remain zero; remittance/certificate status is `NOT_IMPLEMENTED`. Refunds retain
withholding for review; there is no automatic tax release or remitted-tax
reversal. No actual deduction, remittance, certificate success or payout is
claimed. See [accounting contract](withholding-ledger.md) for basis and rounding.
