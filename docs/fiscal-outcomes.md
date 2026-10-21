# Validated fiscal outcomes

The dictionary-returning `submit_sale` and `get_sale_status` methods now reject
empty/non-object outcomes, missing purchase identity/state, and `SIGNED` results
without a nonblank CU invoice number, SDC identity and receipt signature. Validation
is shared by sync/async transports. `SYNCED` remains a recognized historical state;
it is not converted to `SIGNED`. Optional historical metadata remains unavailable.
Other endpoint parsers and KRA error-code mappings retain their behavior.
Configured API URL path prefixes retain the same fiscal validation.
Responses must use the `/v2` JSON field names (`purchaseId`, `cuInvoiceNumber`,
`sdcId`, `receiptSignature`); Python attribute names do not substitute for them.
Status responses also validate `failureReason` as a string or null. Malformed
responses raise SDK exceptions with response metadata in both dictionary and
typed methods.

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
