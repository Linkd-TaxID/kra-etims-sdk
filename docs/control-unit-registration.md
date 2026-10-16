# Original control-unit registration and recovery

The immutable branch device selects VSCU or OSCU. `register_item(ItemDetail(...))`
uses the source `itemCd` as the SKU; the server retains the fiscal item identity.
Itemised baskets register their applicable items before signing. VSCU retains its buyer-registration prerequisite. OSCU accepts an optional buyer
PIN without inventing that prerequisite; `taxid.registration.oscu.customer-required`
can explicitly enable it as a local policy. `register_customer(BranchCustomerRegistration(...))`
is the separate branch master-data operation for either mode. Taxpayer lookup
is a separate read and does not confirm branch registration. B2C sales do not invent a buyer or perform
customer registration. OSCU requires a completed communication-key checkpoint,
its own item code format, string customer number and direct protocol endpoints.
OSCU flat invoices without registered item identities remain unsupported.

Keep the exact invoice and operation key. After a timeout use
`find_sale(operation_key=original_key)` or `find_sale(client_reference=reference)`.
Sync and async clients support both methods. `PENDING_SYNC` is queued work; an
OSCU pending operation is not an offline-signed fiscal invoice. A receipt is
accepted only when complete mode-specific receipt evidence is retained. Device
acknowledgement does not establish central KRA synchronization or launch approval.

Accountable operators inspect `client.prerequisites.inspect(...)` and
`inspect_sale_transport(queue_id)`. Inspection includes original device, protocol,
environment, endpoint, contract and payload digests. Communication keys and exact
authenticated outbound bytes remain encrypted and are not exposed. Use
`original_transport_evidence(snapshot, outcome=..., provenance=...)` to bind retained
outcome evidence to these digests. This is a supplied assertion, not verification.
Attach original receipt evidence to the reconciliation case before finalization.
A receipt from another control unit is refused. An encrypted filesystem receipt
spool retains returned receipts when database checkpointing fails; restore its
volume and encryption keys together with the original journal.

Prerequisite recovery may resume only the original sale while its persisted stage
establishes that invoice preparation/dispatch did not occur. Unknown invoice
outcomes cannot replay. The published protocols do not supply authoritative
remote cancellation; approval alone cannot make an already-dispatched request
safe to resend. Changed bodies conflict. Revoked or expired originating credentials
cannot dispatch queued work. Historical rows without original transport remain
conservative and cannot acquire a snapshot from today's device configuration.

Production, real-device acceptance and remote sandbox authorization remain separate
gates. Local integration examples permit only independent loopback protocol servers.

`lookup_customer(pin)` calls the branch's documented taxpayer lookup, distinct from
the compatibility `lookup_pin` alias. `get_sale_original(purchase_id)`
returns the retained business payload and safe original transport identity. Duplicate
submission can return the historical cached HTTP response; use `find_sale` to inspect
the current completion outcome after operator recovery.

Recovery inspection includes the original worker boot identity. Bind it in the proof.
An orphaned execution needs retained worker-quiescence evidence; a database epoch
cannot establish cancellation. Active local transport prevents resolution. New fiscal
HTTP calls recheck authorization and exact endpoint/bytes at transport execution and
cannot follow redirects or automatically repeat the request.

Without an authoritative outcome-resolution contract, live unknown registration and
lost-receipt outcomes remain restricted. Captured registration acknowledgements and
captured receipts can be finalized locally. Supplied remote outcome assertions are
accepted only inside explicitly synthetic loopback test scopes. The simulator-specific
VSCU compatibility scope is not OSCU acceptance and cannot authorize real taxpayers.
A taxpayer lookup never proves branch-customer registration.

`ItemRegistration` registers a catalogue entry without inventing invoice totals.
Existing `register_item(ItemDetail)` calls remain supported. The isolated
`examples/control_unit_registration.py` executes registration, sale and original
inspection for both native modes from an explicit private manifest.
