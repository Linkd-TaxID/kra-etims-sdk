# Retail corrections, stock and local closure

`issue_credit_note` accepts `return_lines=[ReturnLine(originalItemSeq=1, qty=Decimal("1"))]`
and `reason_code=CreditNoteReason.REFUND`. Keep the same client reference and key.
The server derives quantity ceilings, price, discount, identity and band taxes
from the immutable original. Omitted selections reverse remaining quantities.
Legacy SKU selections ignore caller replacement prices/classifications. Flat
historical receipts support full reversal only. Cumulative cent allocation
conserves the original. Deferred and unknown corrections keep reservations.

Stock requires `idempotency_key`; the typed result reports SYNCED, REJECTED or
OUTCOME_UNKNOWN. Only SYNCED proves device acknowledgement, never central KRA
acknowledgement. Use `inspect_stock_operation(key)` after timeout/restart.
The operator `retail.inspect_stock`/`resolve_stock` binds authoritative evidence
to the original payload hash. Resolution never replays a movement. A new key
must not bypass an uncertain operation. The service fences other stock and
invoice dispatch in that branch until its outcome is established.

Stock calls are separate manual mutations. They are never automatically called
by sale/correction signing. `A` means outgoing adjustment (code 16); `M` import
(01); `I` outgoing movement (13). One movement type per request. Original
registered classifications are required. Stock transport uses the stored native VSCU or OSCU adapter. Production dispatch
remains restricted pending installed-route outcome and recovery evidence. Sale-related inventory/device effects
need installed-device evidence before adding any separate stock call.

Local close is an aggregate of signed, settled local work. Admission before
registration, preparation, dispatch ownership, unknown registrations and stock
operations block final closure. Operator abandonment fences an exact unprepared
admission; unknown prerequisite outcomes still need resolution. Closure reports
`closureScope=LOCAL_AGGREGATE`, `deviceFiscalCloseSupported=false`, and both
`vscuAcknowledged=false` and `kraAcknowledged=false`. No remote close is sent.

Production itemised corrections, stock, registration and local close remain
restricted. KRA's TIS v2.0 section 6.16 limits original cancellation to once;
real-device evidence must establish partial-return versus cancellation semantics,
repeat corrections, original receipt reference, lost responses and recovery
before enabling dispatch. No status lookup or replay guarantee is assumed.
Official contracts: [VSCU v2.0](https://www.kra.go.ke/images/publications/VSCU_Specification_Document_v2.0.pdf),
[TIS v2.0](https://www.kra.go.ke/images/publications/TIS-for-OSCU--VSCU-Technical-Specifications-v2.0.pdf).

Run `taxID/scripts/tests/phase2-retail.py` with `examples/retail_operations.py` to reproduce synthetic HTTP and restart acceptance.

`originalItemSeq` is the 1-based original basket item sequence, before any
control-unit discount encoding split. A logical return can consume several
original fiscal lines. The original net prices and tax amounts are retained;
sourceReturns records the allocated economic discount even when the signed
wire folded discounts into net unit prices. Historical v1 mappings are
verified against their exact recorded encoding or remain restricted.
