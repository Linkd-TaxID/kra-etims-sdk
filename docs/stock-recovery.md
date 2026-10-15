# Stock recovery and local closure

Both protocols have distinct local native HTTP acceptance. This is not KRA acceptance.

VSCU stock I/O uses `/stock/saveStockItems`; OSCU uses authenticated `/insertStockIO`.
`submit_stock_adjustment` retains original SAR, intent, identity and wire hashes.
`inspect_stock_operation` reads its current journal state; a cached POST can be older.
`submit_stock_master(StockMasterRequest(...), idempotency_key=...)` supplies absolute
`rsdQty` after a confirmed original I/O operation for that item. It is not an increment.
VSCU uses `/stockMaster/saveStockMaster`; OSCU uses authenticated `/saveStockMaster`.
A single original I/O/item pair cannot be replaced with a new master key. The host
ERP supplies the current residual balance. The middleware does not calculate inventory.
No automatic sales/return stock request is generated; actual device stock effects
need installed-device evidence before composing those workflows.

Correction identities bind the signed original issuer/branch/device/solution, buyer,
receipt, original wire invoice number, rates, discounts and fiscal line mapping.
Unknown/deferred corrections reserve original quantities and amounts. Exact retries
inspect the original rather than creating another correction. Partial/repeated returns
are permitted only in explicit isolated synthetic fixtures. KRA's common TIS §6.16
requires prior cancellation and permits cancellation only once; the publications do
not establish arbitrary partial/repeated returns for either mode. Production corrections
remain blocked separately. Full original-reference native fixture acceptance does not
prove the installed cancellation sequence or grant approval.

Operator `retail.inspect_stock` returns safe transport hashes and worker identity.
`StockRecoveryEvidence` binds the original hashes, outcome and execution owner;
orphans also require original-worker quiescence evidence. Captured accepted/rejected
responses can finalize locally after database checkpoint failure/restart. Assertions,
approval, elapsed time and lost database locks cannot resolve live uncaptured outcomes.
No replacement keys, movements, NOT_SENT transitions or automatic fiscal retries exist.
Absolute stock recovery uses `/v2/admin/retail/stock-master` and its `/resolve` route
with the same evidence fields. All mutations need stable keys and branch authorization.

Local daily closure is an aggregate, not device fiscal closure or central KRA acknowledgement.
Admissions, registration, dispatch, correction reservations and unresolved stock fence it.
Use branch `/v2/etims/device/health` for its own read-only protocol probe and
`/v2/etims/device/monitoring` for recorded operational availability. An OSCU branch does
not use VSCU connectivity or offline-signing fallback. The 15-minute review indication
is internal policy and sends no notification. It does not suspend, reactivate or replay work.

Persist database journals, original encryption/HMAC keys, and encrypted receipt/stock
spool together. VSCU additionally requires paired JAR identity and state volumes;
OSCU requires retained device/communication-key state and central outcome evidence.
Freeze and prove old workers stopped before backup/restore/promotion. A database owner
fence is not cancellation of a remote request. Consult the middleware operational recovery runbook
for coordinated restore and exact remaining protocol/device/approval dependencies.

Original I/O/master inspection includes `originalBusiness` separately from safe transport identity/hashes; communication keys and authenticated bytes remain private. The backend and SDK enforce non-negative absolute quantity with at most 16 integer and two fractional digits. Full-return policy checks every original quantity, including free/fully discounted lines, rather than financial value alone. Explicit suspended/deregistered branch holds reject new stock admission/dispatch for both modes while original inspection remains available. Prepared business dates are retained on the wire. Configure application/device/reporting jobs with the reviewed Kenya business timezone and preserve stored dates during restore.
