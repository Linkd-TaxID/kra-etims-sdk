# Registration recovery contract

Classified sale lines now remain itemised even when every line uses one band. Supply `itemClsCd` on every itemised line. Unclassified, undiscounted single-band invoices retain the flat service contract. Mixed bands require classification on every line. Classification codes and effective rates still require taxpayer/device verification; simulator fixtures are not KRA approvals.

`client.prerequisites` and the asynchronous equivalent expose the middleware operator recovery product. `inspect(tin, branch, kind, key)` returns the immutable payload, its exact SHA-256, retained observations and operator audits. `resolve(...)` accepts only evidenced `REGISTERED` or `REJECTED` outcomes; it never dispatches registration. `inspect_sale(tin, branch, key)` returns the HTTP identity hash, execution epoch and stage. `release_sale(...)` fences a request still at the prerequisite stage; prepared or historical requests cannot use this path. An active administrator credential bound to a registered, active operator subject is required; branch-bound administrators remain branch-scoped.

After registration is confirmed, repeat the exact original `submit_sale(invoice, idempotency_key=original_key)`. Preserve every serialized field, including `confirmDt`, and the original reference. Only a durably blocked pre-signing request can be reclaimed atomically. Signing uncertainty requires the existing invoice-status/reconciliation workflow; a replacement key is unsafe. A rejected registration stays terminal. There is no automatic registration replay or invented KRA registration lookup.

Run the isolated example from the middleware repository:

```bash
mvn -q -DskipTests compile dependency:build-classpath -Dmdep.outputFile=/tmp/taxid-phase1-classpath
python3 scripts/tests/phase1-registration.py --sdk-root ../kra-etims-sdk --python ../kra-etims-sdk/.venv-test/bin/python --classpath-file /tmp/taxid-phase1-classpath --evidence-dir /tmp/taxid-phase1-new-run
```

The example uses PostgreSQL, loopback HTTP, the SDK, synthetic registrations, a JVM kill/restart, operator recovery and concurrent resumption. It prohibits non-simulator HTTP and disables schedules. `examples/registration_recovery.py` is driven by that runner; do not target a public service with its synthetic fixtures. Production item/customer registration remains restricted pending real-device evidence and launch approval. Operator evidence is a reviewed assertion retained for audit, not automated KRA confirmation.
