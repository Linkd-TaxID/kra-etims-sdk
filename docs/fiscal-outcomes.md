# Validated fiscal outcomes

The dictionary-returning `submit_sale` and `get_sale_status` methods now reject
empty/non-object outcomes, missing purchase identity/state, and `SIGNED` results
without a nonblank CU invoice number, SDC identity and receipt signature. Validation
is shared by sync/async transports. `SYNCED` remains a recognized historical state;
it is not converted to `SIGNED`. Optional historical metadata remains unavailable.
Other endpoint parsers and KRA error-code mappings retain their behavior.
Configured API URL path prefixes retain the same fiscal validation.

```python
import httpx
from kra_etims import KRAeTIMSClient

with KRAeTIMSClient(
    api_key="branch-credential",
    timeout=httpx.Timeout(connect=2, read=20, write=5, pool=2),
) as client:
    result = client.get_sale_status_typed(123)
    print(result.status, result.response_metadata.request_id)
```

`submit_sale_typed(invoice, idempotency_key=...)` returns `SaleResult`;
`get_sale_status_typed(id)` returns `SaleStatusResult`. Both preserve unknown
extension fields and expose `response_metadata` with HTTP status, request ID,
environment and the unchanged Retry-After header. Existing dictionary methods
return a dict subclass with identical JSON keys and equality behavior; response
metadata lives on `.response_metadata` without adding wire fields. Exceptions
expose `.response_metadata`, `.request_id`, `.environment`, `.retry_after`,
`.http_status` and `.idempotency_key` when a response exists. Header metadata takes
precedence over body metadata; missing historical values are `None`.

Invalid successful mutation responses are ambiguous. Read/write loss and gateway
errors never cause SDK mutation replay. Preserve the original idempotency key and
inspect the original operation. A 429 exposes Retry-After without automatically
retrying. Timeouts are configurable independently for connect/read/write/pool and
apply to token acquisition as well as API calls.

Pass `http_client=httpx.Client(...)` or `httpx.AsyncClient(...)` to inject transport.
Injected clients remain caller-owned: SDK close/context exit leaves them open.
SDK-created clients close on SDK close/context exit. The SDK timeout (default 30
seconds, or the supplied `httpx.Timeout`) applies per request even for borrowed
clients; configure transport retries carefully outside the SDK. No SDK retry is
added by injection.

The CLI renders flat `/v2` receipt fields and only calls an invoice signed when
its state is `SIGNED`; pending/unknown/reconciliation states are displayed directly.

## Packet 2 task and evidence

Base: `b35d59fa4592064322782738115864c9ce800b7a`, freshly fetched 2026-10-20.
Isolated branch: `feat/fiscal-outcome-validation`. The original SDK checkout is
preserved. Allowed files: shared parser, sync/async transports, additive outcome
models/exports, affected tests/docs, and the CLI result renderer where a boundary
test exposed incorrect signing claims. Preserve all authentication, payload,
idempotency, recovery, CLI and KRA error taxonomy assertions. Replace only response
fixtures that did not match `/v2` sale/status contracts.

Reproduce with the SDK test environment:

```sh
PYTHONPATH=src /home/officialnyabuto/Desktop/kra-etims-sdk/.venv-test/bin/python -m pytest -q
```

The new matrix covers both public transports: malformed objects/scalars/arrays,
strict purchase identity, incomplete signed evidence, valid synthetic receipts,
pending/unknown/historical outcomes, malformed JSON and empty HTTP 204/205,
429/502/503/504, read/write timeouts, preserved metadata, one-attempt mutations,
typed methods and owned/borrowed context closure. Existing auth/header/body and
recovery assertions remain. A CLI regression covers unsettled outcomes.

Verification, 2026-10-20: **535 passed, 1 skipped** in 59.79 seconds. The skipped
test explicitly requires a live middleware; this packet used synthetic HTTP only.
The earlier suite exposed five invalid response fixtures and the CLI field/state
rendering bug. Those fixtures now reflect the middleware contract and their
original behavioral assertions remain. No live government request was made.
