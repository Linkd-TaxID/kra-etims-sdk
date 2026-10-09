# Authorised creator invoicing and operator evidence (Phase 4)

Requires additive TaxID V41/remittance routes. Ordinary sales must have `invoice.tin`
equal to the taxpayer on the selected creator grant. Use a separate Tihada grant/client
for fee invoices, and separate stable source/idempotency keys. Keep gross creator supply,
fee, withholding and payout distinct. Retain mandate evidence before an operator issues
a grant; the SDK never supplies a taxpayer identity to session acquisition.

```python
from kra_etims.platform_sessions import PlatformSessions

with PlatformSessions(platform_secret, base_url=taxid_url) as sessions:
    with sessions.branch_client(creator_grant, environment="SANDBOX",
                                scopes={"sale:create"}) as creator:
        creator.submit_sale(creator_invoice, idempotency_key=creator_source_key)
    with sessions.branch_client(tihada_grant, environment="SANDBOX",
                                scopes={"sale:create"}) as tihada:
        tihada.submit_sale(fee_invoice, idempotency_key=fee_source_key)
```

`AsyncPlatformSessions` has the same API with awaited methods and async context managers.
Session cache keys include grant, environment, exact scopes and the manager's URL.
Near expiry, calling session/branch_client acquires a fresh capability. A returned fiscal
client keeps its fixed capability: reacquire for later operations. There is no automatic
replay on revoked/expired permission or an ambiguous fiscal outcome. Cache state cannot
prove ongoing grant validity; server authentication checks every request.

Session `accessToken` is a SecretStr hidden from repr. It is never persisted by this
module. A platform credential goes only to `/v2/platform/branch-sessions` and revocation,
never into a fiscal client. Revoke a capability by its server ID; cache eviction precedes
the request, and a failed request requires retrying that same ID. Closing a manager does
not revoke remote sessions. Creator offboarding requires the existing operator grant
revocation route, which invalidates all capabilities on that grant without changing others.

## Assisted remittance

Use `client.withholding.prepare_remittance(receipt_id, RemittanceExport(...))`,
`get_remittance(receipt_id)` and `remittance_action(receipt_id, RemittanceTransition(...))`.
Async parity is available. Models live in `kra_etims.remittance`. Money uses exact Decimal
or decimal strings, never float. Operation IDs are stable retry identities; reading and
retrying identical content returns the current case, while changed content returns 409.

The immutable JSON export is an **internal review record**, not an official iTax template.
Supply withholdee PIN, externally assessed amount, actual deduction evidence/date,
assessment-decision reference and an operator-reviewed due date/holiday evidence.
Production calculation/deduction remains unconfigured; this does not authorise the SDK
to make a legal determination. Production evidence mutations require a provisioned,
tenant-bound ROLE_ADMIN credential with active operator subject. Automatically minted
SDK capabilities do not satisfy this operator requirement. Sandbox delegation uses
withholding:read/write; approval uses the separate withholding:approve scope.

Order: export → REVIEW → APPROVE → RECORD_EVIDENCE → RECONCILE → RECORD_CERTIFICATE.
The approver differs from preparer/reviewer. Approval is routed to `/approval`, other
actions to `/actions`. Evidence and reconciliation amounts must match the approved export.
Payment/certificate references cannot be reused for another case in the same branch and
environment. Optional attachmentReference and attachmentSha256 must be supplied together.
TaxID stores only these references and supplied hashes; it does not upload, fetch or
independently verify attachments. Reviewed exports do not execute payments or filings.

Responses retain `automaticKraRemittance=False`, `evidenceVerification=SUPPLIED_UNVERIFIED`
and `CERTIFICATE_RECORDED_UNVERIFIED`. `reconciledSuppliedEvidence` means internal matching
of operator-supplied evidence, not verified KRA remittance. The separate Phase 3 ledger
retains its original response contract and zero authoritative remitted amount.
Cancellation is terminal and only before payment evidence. Refunds block subsequent
progression until external adjustment handling; no tax release or netting is inferred.
One case per receipt; partial remittances and shared payment allocations are unsupported.

Reverse invoicing remains gated even when the feature flag is enabled: no verified
reverse contract or supplier secondary-device adapter exists. Official WHT filing,
payment and certificate contracts also remain unverified for this integration.
See the middleware Phase 4 external-verification record for the exact outstanding items.
