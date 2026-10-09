# Withholding ledger — Phase 3

Requires TaxID's additive `/v2/withholding` routes (V40 migration). Use a branch
credential or delegated `withholding:read` / `withholding:write` capability.
Platform master credentials cannot access this ledger. The taxpayer, branch and
environment come from authentication, never from the request body.

Phase 1 established schedule rates, but did not establish Tihada-specific category,
basis, deduction trigger, PE/treaty treatment or refund recovery rules. Accordingly,
`SIMULATION` policies and deductions require sandbox credentials. Production
supports explicit `UNCONFIGURED` / `UNSUPPORTED` receipt accounting, with null
calculated withholding and net payable. No automatic zero-tax interpretation.
Gifts remain unresolved. These models have no eTIMS tax-band field.

`client.withholding` and `async_client.withholding` expose:

| Method | Endpoint |
| --- | --- |
| `create_policy(policy)` | POST `/v2/withholding/policies` |
| `record_receipt(receipt)` | POST `/v2/withholding/receipts` |
| `get_event(event_id)` | GET `/v2/withholding/events/{event_id}` |
| `record_refund(original_id, refund)` | POST `/v2/withholding/receipts/{original_id}/refunds` |
| `record_deduction(original_id, deduction)` | POST `/v2/withholding/receipts/{original_id}/deductions` |
| `reconcile(original_id)` | GET `/v2/withholding/receipts/{original_id}/reconciliation` |

Policies are immutable by ID/version, with a half-open effective interval and
supporting decision evidence. A new version never changes a prior snapshot.
Receipts freeze the policy and source evidence. Policy and event IDs use letters, digits, dot, underscore, colon or hyphen, beginning with a letter or digit. Event IDs have at most 200 characters, policy IDs at most 100. Stable event IDs are required:
retry identical content under the original ID; changed content returns 409.
Event IDs are unique across receipts, refunds and deductions within the branch and
environment. These routes deduplicate in the database without a separate HTTP
idempotency header. Unknown transport outcomes require retrieving/retrying the
same event, never generating another event ID.

Money inputs are KES, exact Decimal/integer/string, nonnegative and at most two
decimal places. Float inputs are rejected; numeric JSON responses are decoded
as Decimal for ledger routes. Calculated withholding uses HALF_UP to two places.
`grossReceipts` includes `receiptVat`. `platformFee` excludes `platformFeeVat`;
the fee VAT is an additional settlement deduction. The three explicit simulation
bases are gross receipts, receipts less receipt VAT, and receipts less receipt VAT
and both fee components. Their availability is an arithmetic fixture, not legal
approval of a basis. One category is selected; overlapping categories are never
stacked. Resident rate is 0.05; nonresident/no-PE fixture rate is 0.20.

A receipt snapshot's `netPayable` is the projected result after its calculated
withholding. `reconcile().netPayable` uses actual recorded deductions, not the
projection. `calculatedWithholding`, `withheld`, `pendingRemittance` and `remitted`
are distinct. A simulation deduction moves the calculated amount into withheld
and pending-remittance accounting; it performs no money movement. Remitted is
always zero, and remittance/certificate status is `NOT_IMPLEMENTED`. No payout,
remittance, certificate success, working-day deadline or provider integration is
claimed. No real deduction trigger or holiday calendar was established.

Refunds are append-only negative gross/fee/VAT entries linked to the original.
Each component is bounded by its original cumulative amount; money/fee/VAT
refund evidence must be supplied separately. They retain the original withholding
and mark review required. A refund before deduction blocks deduction until a
future established adjustment rule is implemented. A refund after deduction can
produce a negative payable, showing the retained liability rather than hiding it.
This phase has no tax-release or remitted-tax reversal route. Correct erroneous
receipt money through linked approved refund entries; use a new event/policy
version for a genuinely new obligation. There is no replacement sale, editing of
snapshots, automatic tax reclassification, invoice credit-note issuance or refund
payment execution.

See `examples/withholding_ledger.py` for a synthetic, opt-in sandbox example.
