# Phase 4 SDK revision and verification

9 October 2026. Repository `/home/officialnyabuto/Desktop/kra-etims-sdk` remains on
`feat/branch-sdk-onboarding-docs`, HEAD `c1e600f147a5fdcdb1ba0ee424b83523c095e7ab`.
No checkout, fetch, commit, release or deployment. Preserved all prior Phase 3 changes,
including README, transports, withholding models/examples/docs, fixtures/tests and the
pre-existing untracked audit test. Read supplied AGENTS.md, repository CLAUDE.md and
middleware Phase 2/3 handoffs; no repository filesystem AGENTS.md exists.

Added PlatformSessions/AsyncPlatformSessions and SecretStr BranchSession; typed remittance
export/action/audit/case models; three sync/async withholding methods (four HTTP routes,
including separate approval); two caller-controlled examples and operation docs/README. Corrected gateway reverse-invoicing
claims and exposed typed gate-reason/availability fields, preserving legacy unknowns.
Existing fiscal models, payloads, response parser, idempotency and transport behavior
remain compatible. Client accessor descriptions now include assisted evidence. No version
bump and no invented reverse-invoicing, filing, payment or certificate API.

Staged the changes in `/tmp/taxid-phase4-sdk` because the repository lies outside the
session's writable roots. Reviewed the exact changes, tested them, then applied only the
listed changed/new files using an escalated filesystem operation, with original-content
hash checks to prevent overwriting concurrent changes. Existing Git arrangement retained.

Verification uses the repository's existing `.venv-test/bin/pytest`, with `-p no:cacheprovider`
and the seven classes/modules selected below. **45 passed**: 13 Phase 4 and 32 existing
Phase 3/fiscal compatibility cases, zero failures. Tested against middleware HEAD above
plus the uncommitted Phase 4 implementation (V41), API version 1.44.

```sh
.venv-test/bin/pytest -q -p no:cacheprovider tests/test_phase4_operations.py \
  tests/test_withholding.py tests/test_item_wire_contract.py \
  tests/test_payload_integrity.py tests/test_error_contract_compat.py \
  tests/test_bulk_import_and_sale_status.py tests/test_idempotency.py
```

Phase 4 tests consume the **exact JSON from Java MockMvc and transactional PostgreSQL**
in `tests/fixtures/phase4-remittance-case.json`. Models preserve Decimal amounts and
unverified states. Transport tests cover export/get/actions/approval and decimal-string
payloads with sync/async parity. Session tests exercise concurrent acquisition, grant/
environment/scope cache isolation, refresh, revocation cache eviction, mismatched responses,
server denial, masked representations and separation of platform and fiscal credentials.
Invalid monetary/document fields are rejected. Source/example compilation and diff checks
are recorded in the final middleware Phase 4 verification record.

The first staged test run found a generation syntax error; corrected before installation.
The next caught Java's standalone HTTP mapper emitting a LocalDate array; the API now
explicitly returns dueDate as an ISO date string, and the final fixture/model checks pass.
No live SDK-to-KRA call, real creator invoice, real withholding, remittance or certificate
verification occurred. Production evidence requires named branch operators and an external
assessment. Automatically minted SDK capabilities do not substitute for production operator
credentials. No reverse contract, external WHT integration or production approval inferred.
