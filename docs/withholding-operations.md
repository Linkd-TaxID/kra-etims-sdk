# Withholding policy and assisted operations

This is an accounting and operator-evidence workflow. It does not file a return, remit tax, pay a seller, generate a KRA certificate or independently verify a document. Production withholding calculation/deduction remains disabled. Existing fiscal and launch approvals remain required.

## Verified sources and unresolved decisions

Reviewed on 10 October 2026:

- [Enacted Income Tax Act, Kenya Law, 1 January 2026](https://new.kenyalaw.org/akn/ke/act/1973/16/eng@2026-01-01/source.pdf), cross-checked against [KRA's consolidated Act, 1 July 2026](https://www.kra.go.ke/images/publications/Income-Tax-Act-2026.pdf). Section 10(4) addresses platform-facilitated income. Section 35(1)(q)/(s) and 35(3)(l)/(n) identify digital-content and marketplace payments. Third Schedule Head B paragraph 3(m)/(o) supplies resident 5% rates; paragraph 4(t)/(v) supplies nonresident 20% rates, with paragraph 4(t) expressly referring to gross digital-content amounts. Section 35(4) contains exemption/order exceptions. Section 35(5) requires remittance and the stipulated return/certificate within five working days after deduction. These provisions establish rules; they do not classify a particular platform's business.
- [KRA withholding guidance](https://www.kra.go.ke/individual/filing-paying/types-of-taxes/individual-withholding-tax) confirms the digital-content rate table, payer responsibility and assisted iTax payment-slip process. It does not supply an executable remittance API or establish an arbitrary platform fee, waiver or VAT classification.
- [KRA August 2023 iTax enhancements](https://www.kra.go.ke/images/publications/ITAX-ENHANCEMENTS---AUGUST-2023-External-Stakeholders-07082023.pdf), withholding section (iii)/(iv), describes same-supplier-payment-day PRN grouping and weekend/holiday handling. Income-tax deadlines were rechecked against the current Act; historical statements about other withholding taxes are outside this contract. A supported batch assumes the recorded deduction date is also the evidenced supplier payment date. Different payment/deduction dates require a separate reviewed workflow; do not relabel a projected payout as a supplier payment.
- [Tax Procedures Act, Kenya Law, 1 July 2025](https://new.kenyalaw.org/akn/ke/act/2015/29/eng@2025-07-01/source.pdf), section 23A(3A), describes purchaser invoices for small businesses/farmers with annual turnover not exceeding KES 5 million. [KRA buyer-initiated invoicing](https://www.kra.go.ke/business/etims-electronic-tax-invoice-management-system/learn-about-etims/buyer-initiated-invoicing) and [dedicated reverse invoicing](https://www.kra.go.ke/services/service/16) establish distinct eligibility, approval and consent requirements. Buyer-issued invoicing is separate from a seller refund/credit note.

Unresolved applicability includes whether each payment is content monetisation or marketplace income; payer/withholder responsibility; residence, permanent establishment, treaty or exemption eligibility; the gross amount versus VAT/commission exclusions for the particular transaction; gifts/tips; fee VAT classification, waivers and refunds. A generic merchant label or historical arithmetic fixture answers none of these questions. Fee VAT is recorded as a supplied amount, without classifying or calculating VAT. Zero fees do not establish a tax exemption. A refund-retention review is a conservative accounting decision preserving a recorded liability, not a ruling that every refund legally retains withholding.

## Versioned contract

`Policy` retains its immutable branch/environment, `policyId`, positive `version`, category, basis, half-open `[effectiveFrom,effectiveUntil)` interval and decision evidence. Its version-1 `contract` requires:

| Field | Supported value |
| --- | --- |
| `schemaVersion` | `1` |
| `residency` | `RESIDENT` or `NONRESIDENT_NO_PE`, matching the category |
| `rate` | Exact decimal `0.05` or `0.20`, matching residency/category |
| `platformFeeVat` | `SUPPLIED_AMOUNT` |
| `waivers` | `NONE`: automatic withholding exemptions/waivers unsupported |
| `gifts` | `UNSUPPORTED` |
| `refunds` | `REVIEW_NO_TAX_RELEASE` |
| `legalSources`, `assessmentEvidence` | Explicit transaction applicability/source assessment, supplied by an operator |

`ASSESSED` supports the four named digital-content/marketplace residency categories on `GROSS_RECEIPTS`, starting with the verified July 2026 legislative baseline. This is approved sandbox arithmetic, not acceptance that gross receipts always define the legal base. Other bases remain simulation-only; they cannot become assessed production treatments. A new legal baseline or supported treatment requires contract review, a new immutable policy version and a separately bound approval. Old `SIMULATION` policies retain historical arithmetic behavior and fixture rates; their definitions do not become accepted law or partner policy. `UNCONFIGURED` and `UNSUPPORTED` calculations remain null, not zero tax.

Create an assessed policy as an authenticated branch operator (`withholding:write`). Inspect its exact immutable definition with `GET /v2/withholding/policies/{id}/versions/{version}` (`withholding:read`) before approving it. The stored assessor's identity is immutable. `POST /v2/withholding/policies/{id}/versions/{version}/approval` requires `withholding:approve`, a different operator, a stable operation identity, the exact `assessmentEvidence` and `approvalEvidence`. Approval is idempotent for the same actor/content. The response explicitly says `SUPPLIED_UNVERIFIED` and `productionEnabled=false`. Neither the evidence string nor the separate operator grants independent KRA/legal verification. Named, active, provisioned administrator subjects are required for production operator evidence. The existing production withholding gate cannot be overridden by a policy or feature flag.

## Accounting facts and identities

Amounts use KES decimal arithmetic, cent inputs and HALF_UP cent rounding at the withholding calculation boundary. Cent/HALF_UP rounding is an internal sandbox accounting convention; authoritative iTax amount precision and aggregation/rounding acceptance remain external prerequisites. Fees and fee VAT are supplied separately. The estimate is gross times the assessed rate. Each receipt snapshots its entire policy; later versions cannot rewrite it. A stable event ID binds the exact request, kind and original event within taxpayer/branch/environment. Changing content under an identity conflicts. The authenticated branch supplies tenant scope; requests cannot select another tenant.

A receipt estimate does not record a deduction. Explicit `POST /receipts/{id}/deductions` records a deduction assertion/evidence, with its own identity and actual event time. An assessed policy requires an explicit positive `amount` matching the approved calculation; missing amounts, mismatches and future deductions are rejected. Legacy simulation requests can omit the amount, preserving their original retry identity. One original receipt can have one deduction. Receipt locks serialize deductions, refunds, reviews and assisted evidence, while database uniqueness handles duplicate identities. Immutable event snapshots are historical; use reconciliation for current balances.

| Fact | Meaning |
| --- | --- |
| `calculatedWithholding` | Original estimate, never proof of deduction |
| `withheld` | Sum of explicitly recorded deduction entries |
| `pendingRemittance` | Recorded deduction still without independent remittance verification |
| `suppliedPaymentEvidence` | Amount covered by a supplied payment document/reference |
| `reconciledSuppliedEvidence` | Supplied evidence matched to the export; still unverified |
| `remitted`, `verifiedRemittance` | Independently verified remittance; zero while no supported verification integration exists |
| `netPayable` | Accounting projection after fees/recorded deduction, `PROJECTED_NOT_PAID`; not a payout record |

Ledger and assisted views share the same locked receipt boundary and distinguish outstanding liability from outstanding evidence. Completing supplied-evidence reconciliation can make `pendingEvidence` zero while `pendingRemittance` remains positive. That is intentional: evidence completion does not verify tax payment.

## Refund adjustment and recovery

Refunds append negative gross/fee/VAT components and cannot cumulatively exceed any original component. They never release withholding. Unsupported money precision, changed identities or over-refunds fail atomically.

After a recorded deduction, the supported adjustment is **retention only**. `POST /v2/withholding/refunds/{refundId}/review` accepts `action=ASSESS`, `outcome=RETAIN_DEDUCTION`, `assessmentEvidence` and evidence. A different operator then submits the exact assessment with `action=APPROVE` to `/approval`. Both records are immutable, and repeated actor/content returns the same decision. Each refund needs its own review; an approval cannot cover a later refund. Approved retention clears that refund's review flag while preserving every cent of the original liability and all remittance evidence.

Before payment evidence, an unresolved refund blocks remittance approval/evidence recording; a retention review can resolve the supported case. After evidence was recorded, reconciliation and certificate-document recording may finish without changing liability or reopening the case. Completed cases cannot be cancelled or restarted. Tax release, offset, refund of already-remitted tax, and refunds before deduction require an externally supported adjustment workflow and remain unavailable. Historical exports without a ledger deduction stay readable; their supplied assessments cannot progress into new approvals/payment evidence. No migration fabricates an actual deduction or rewrites historical snapshots.

Recovery repeats the original event/action identity and exact request, then inspects reconciliation and the original case. No method dispatches a payment, so lost HTTP responses do not authorize a new payment or new identity. Stable original batch action identities also recover an atomic allocation after response loss or restart.

## Assisted export, deadlines and batching

Exports require the exact recorded deduction amount/time, taxpayer PIN, explicit assessment/deduction/deadline evidence and a versioned holiday calendar. An externally supplied export amount cannot create a deduction. A calendar has version, inclusive coverage interval, holidays and source evidence. The calculator uses Africa/Nairobi, excludes the deduction day, Saturday, Sunday and listed public holidays, counts five working days, and rejects inadequate coverage or a mismatching supplied due date. Calendars are operator assertions: completeness, gazetted/observed holidays and the actual iTax PRN due date must be checked by the operator. No calendar is preloaded from an old fixture.

Per-case lifecycle: `EXPORTED → REVIEWED → APPROVED → EVIDENCE_RECORDED → RECONCILED_SUPPLIED_EVIDENCE → CERTIFICATE_RECORDED_UNVERIFIED`. The approver must differ from both preparer and reviewer and possess approval scope. Cancellation is supported only before payment evidence. Payment and certificate references are supplied identifiers; optional attachment references require matching SHA256 metadata. An attachment hash preserves an assertion's identity, not its authenticity.

`POST /v2/withholding/remittance-batches` creates an immutable **operator work manifest**, up to 500 unique approved cases without unresolved refunds, within one branch/environment and one Kenya deduction/payment day. It groups cases by deadline and retains exact decimal totals, allocations and policy snapshots. The earliest deadline is visible. This is not an iTax return schema, PRN, submission acknowledgement or payment reservation. Read current cases before acting on a historical manifest. Overlapping manifests cannot record another evidence action for an already advanced case.

`POST /remittance-batches/{id}/actions` supports `RECORD_EVIDENCE` and `RECONCILE`. A supplied aggregate amount must equal the manifest total. All allocations commit together against sorted receipt locks; one case failure rolls back the whole action. A shared payment reference is stored once in the evidence registry and preserved verbatim in each case's supplied evidence, with stable derived allocation identities. The registry prevents reuse across single-case and batch workflows. Certificates remain per-case because an aggregate payment reference is not an individual taxpayer certificate. Partial payments, reallocations, revised batches/PRNs and automatic payment execution remain unsupported. Reconciliation records supplied evidence only and does not release pending tax.

## External interfaces and production prerequisites

Reviewed KRA iTax guidance, certificate-checker references, the GavaConnect catalogue access path and official OSCU/VSCU publication links. No official executable withholding remittance/certificate contract with supported authentication, request/response schema, original-identity acknowledgement, status recovery and taxpayer authorization was established. The catalogue was not accessible through this session's browser. That is a verification limitation, not proof that no KRA API exists. Third-party wrappers, procurement proposals and a payment provider's tax API are not substitutes; a payment-provider interface itself requires prior KRA declaration/PRN integration. Obtain the actual official contract and authorized sandbox access before integrating. No browser automation or guessed remittance endpoint was added.

Dedicated reverse-invoicing guidance requires KRA buyer KYC approval, seller PIN/eligibility validation, informed consent/authorization, contracts and a distinct seller secondary-device lifecycle. Buyer-initiated invoicing additionally describes non-VAT seller checks and seller acceptance/rejection within 30 days. The published OSCU v2 schema and sign-up guide linked from KRA's page did not establish the dedicated lifecycle/recovery contract. Existing supplier consent remains recorded with `fiscalSubmissionAvailable=false`; the dormant ordinary-sale fallback was removed. Setting the historical reverse-invoicing flag cannot enable it. SDK credit-note refunds explicitly do not implement buyer-issued invoicing.

Production acceptance requires precise applicability decisions, legal-source effective dates, payer/residency/treaty evidence, basis and fee VAT decisions, supported gift/waiver/refund decisions, reviewed holiday calendar and iTax date semantics, accountable operator provisioning, the official executable remittance/certificate contract and dedicated reverse-invoicing eligibility/schema/device/consent/authorization/recovery contracts. Production launch approval remains separate. Apply new migrations with matching middleware/SDK only through the existing approved release process.


## SDK usage and isolated acceptance

Import `WithholdingContract`, `WithholdingPolicy`, `WithholdingPolicyApproval`, `WithholdingReceipt`, `WithholdingDeduction`, `WithholdingRefund`, and `WithholdingRefundReview` from `kra_etims.withholding`. Import `RemittanceCalendar`, `RemittanceExport`, `RemittanceTransition`, and `RemittanceBatch` from `kra_etims.remittance`. Both sync and async `client.withholding` expose `create_policy`, `get_policy`, `approve_policy`, `record_receipt`, `record_deduction`, `record_refund`, `review_refund`, `reconcile`, `prepare_remittance`, `get_remittance`, `remittance_action`, `prepare_batch`, `get_batch`, and `batch_action`. Pass decimal strings or `Decimal`; float/bool amounts and rates are rejected.

`examples/withholding_operations.py` is executable only with the private manifest created by the middleware's isolated runner. It provisions synthetic branches/operators, approves a versioned policy, tests concurrent deductions, exports two cases with a computed deadline, allocates shared batch payment evidence, records unverified certificates and reviews a refund without releasing tax. A second run inspects the same original records after JVM restart. No taxpayer credentials, deployment or real payments are required.

```bash
# From taxID, using the adjacent SDK environment:
mvn -q -DskipTests compile dependency:build-classpath -Dmdep.outputFile=/tmp/taxid-phase3-classpath
python3 scripts/tests/phase3-accounting.py --sdk-root ../kra-etims-sdk --python ../kra-etims-sdk/.venv-test/bin/python --classpath-file /tmp/taxid-phase3-classpath --evidence-dir /tmp/taxid-phase3-new-run
```

Historical responses may omit `contract`, `calendar` and new evidence-balance fields; SDK models can still read them. New assessed policies require a complete contract, and the server requires a reviewed calendar on new exports. The old simulation examples remain arithmetic fixtures. A historical export without a recorded deduction cannot be promoted into a supported deduction by adding an evidence string. The SDK's `submit_reverse_invoice` name refers to a removed historical refund endpoint; it does not implement KRA buyer-issued invoicing.
