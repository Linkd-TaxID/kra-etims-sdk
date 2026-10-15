# Reproduce the synthetic marketplace demonstration

TaxID supports any authorised application; this marketplace is one example.
This example exercises this SDK against TaxID HTTP endpoints and an isolated PostgreSQL
17 database. All receipts, deductions, payment references and certificates are simulated.
No KRA service, payment provider or production environment is contacted.

From the companion TaxID checkout, with Java 21, Maven, PostgreSQL 17 binaries and a
Python environment containing this SDK's dependencies:

```sh
mvn -q -DskipTests compile dependency:build-classpath -Dmdep.outputFile=/tmp/marketplace-classpath
python3 scripts/tests/marketplace-demo.py \
  --sdk-root /path/to/kra-etims-sdk \
  --python /path/to/sdk-python-environment/bin/python \
  --classpath-file /tmp/marketplace-classpath \
  --evidence-dir /tmp/marketplace-demonstration-new-directory
```

Use a new private directory on every run. Override `--pg-bin` if necessary. The runner
creates and stops its own database, migrates V1–V41, runs the example twice around a
SIGKILL/restart, and checks persisted counts. Runtime logs and private-manifest.json
contain synthetic credentials: retain them privately. Only the `evidence/` JSON files
are designed for sharing. A successful run exits zero and writes result.json.

The SDK example cannot be run against a public service: it requires the runner's
loopback-only isolated-simulator manifest. Concurrent HTTP retries reuse already committed
identities; simultaneous new-operation races are covered by the middleware PostgreSQL tests.

The 5% withholding rate, 7.5% inclusive fee, exhausted waiver and dates are arithmetic
fixtures, not tax advice or an accepted business policy. Gift classification remains
UNRESOLVED and generates no fiscal invoice. Full flat corrections use stable
`client_reference`/`idempotency_key`; canonical itemised and partial corrections stay gated.
Refund retains withheld tax for operator review. Payout is projected, never paid.

Creator grant revocation blocks delegated capabilities, including cached sessions; it does
not revoke the creator's own branch credential. Approval in this sandbox uses distinct
credentials, not a verified production operator identity. Production needs verified,
active, distinct operator subjects and accepted external assessment. Certificate metadata
is supplied evidence, never independently verified proof. See the companion TaxID
`docs/SYNTHETIC_MARKETPLACE.md` for integration and external prerequisites.
