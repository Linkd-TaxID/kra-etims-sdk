# Connected creator and retail demonstrations

The companion TaxID landing has local connected examples using the bounded
`/v2/preview` simulator relay. These are browser preview routes, not SDK fiscal
API contracts. The static landing reports unavailable when a same-origin relay
is absent; it does not turn an illustration into an API response.

A creator sale of KES 1,160 and a separate fee invoice of KES 134.56 demonstrate
separate obligations. The public example uses one synthetic branch. Actual separate
taxpayers need separately authorised branch clients; see
[authorised creator example](../examples/authorised_creator_invoicing.py).
That example requires distinct creator/platform grants and PINs; it does not enable
buyer-issued reverse invoicing or production access.

The simulator ledger uses `SIMULATION`, `GROSS_RECEIPTS` and the explicit digital
content residency category. At the fixture resident rate, KES 1,160 produces a
KES 58 estimate and a KES 967.44 projected payout after KES 116 fee plus 18.56
fee VAT. Nonresident-without-PE arithmetic uses the supported 20% category.
These are synthetic arithmetic inputs, not transaction-specific legal policy,
recorded deductions, remittance or verified payment. Gifts remain unresolved and
excluded; payout is not paid. See [withholding contracts](withholding-operations.md).

The retail basket has two KES 116 notebooks and one KES 58 tote. A partial return
selects original line 1, quantity 1; it supplies no replacement prices/taxes.
The SDK equivalent is `issue_credit_note(original_purchase_id,
return_lines=[ReturnLine(originalItemSeq=1, qty=Decimal("1"))],
reason_code=CreditNoteReason.REFUND, client_reference=stable_reference,
idempotency_key=stable_reference)`. See [retail contracts](retail-operations.md).

`SIGNED`, `PENDING_SYNC` and uncertain states come from real simulator HTTP
responses. Original-reference lookup and exact duplicate recovery retain the same
transaction identity. Pending/unknown outcomes require lookup and investigation,
not a replacement submission. No simulated receipt proves central KRA acceptance.

Use [product integration](product-integration.md) for a distinct-issuer invited flow
against independent synthetic VSCU/OSCU adapters. The visitor journey has no protocol
selector. Local HTTP and browser checks establish simulator behavior only; they do
not establish official sandbox acceptance or authorize deployment.
