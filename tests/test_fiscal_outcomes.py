"""Public sync/async sale boundaries reject incomplete outcomes without replay."""
import inspect

import httpx
import pytest

from kra_etims import AsyncKRAeTIMSClient, KRAeTIMSClient, SaleResult, SaleStatusResult
from kra_etims.exceptions import KRAeTIMSError, TIaaSAmbiguousStateError, TIaaSUnavailableError

pytestmark = pytest.mark.asyncio
HEADERS = {"X-Request-Id": "synthetic-request", "X-TaxID-Environment": "SIMULATED", "Retry-After": "17"}
RECEIPT = {"purchaseId": 1, "status": "SIGNED", "cuInvoiceNumber": "SIM01/1 NS", "sdcId": "SIM01", "receiptSignature": "synthetic-signature"}


async def invoke(client, method, *args, **kwargs):
    result = getattr(client, method)(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def make_client(async_mode, handler, **kwargs):
    raw_type, sdk_type = (httpx.AsyncClient, AsyncKRAeTIMSClient) if async_mode else (httpx.Client, KRAeTIMSClient)
    raw = raw_type(transport=httpx.MockTransport(handler))
    return sdk_type(api_key="synthetic", base_url="https://synthetic.invalid", http_client=raw, **kwargs), raw


async def cleanup(client, raw):
    await invoke(client, "aclose" if isinstance(client, AsyncKRAeTIMSClient) else "close")
    assert not raw.is_closed
    await invoke(raw, "aclose" if isinstance(raw, httpx.AsyncClient) else "close")


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("mutation", [False, True])
@pytest.mark.parametrize("body", [{}, [], None, "text", {"status": "SIGNED"}, {"purchaseId": 1},
    {"purchaseId": 1, "status": "SIGNED"}, {**RECEIPT, "receiptSignature": " "},
    {**RECEIPT, "purchaseId": True}, {**RECEIPT, "purchaseId": "1"}, {**RECEIPT, "status": "success"}])
async def test_invalid_fiscal_results_never_look_successful(async_mode, mutation, body, minimal_invoice):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body, headers=HEADERS)
    client, raw = make_client(async_mode, handler)
    try:
        with pytest.raises(TIaaSAmbiguousStateError if mutation else KRAeTIMSError) as caught:
            if mutation:
                await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
            else:
                await invoke(client, "get_sale_status", 1)
        assert caught.value.request_id == "synthetic-request"
        assert caught.value.environment == "SIMULATED"
        assert caught.value.retry_after == "17"
        assert caught.value.http_status == 200
        assert len(calls) == 1
        if mutation: assert caught.value.idempotency_key == "original"
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("mutation", [False, True])
@pytest.mark.parametrize("wire_name,python_name", [
    ("purchaseId", "purchase_id"),
    ("cuInvoiceNumber", "cu_invoice_number"),
    ("sdcId", "sdc_id"),
    ("receiptSignature", "receipt_signature"),
])
async def test_fiscal_results_require_wire_field_names(async_mode, mutation, wire_name, python_name, minimal_invoice):
    body = RECEIPT.copy()
    body[python_name] = body.pop(wire_name)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body, headers=HEADERS)

    client, raw = make_client(async_mode, handler)
    try:
        with pytest.raises(TIaaSAmbiguousStateError if mutation else KRAeTIMSError) as caught:
            if mutation:
                await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
            else:
                await invoke(client, "get_sale_status", 1)
        assert caught.value.request_id == "synthetic-request"
        assert caught.value.environment == "SIMULATED"
        assert caught.value.http_status == 200
        assert len(calls) == 1
        if mutation:
            assert caught.value.idempotency_key == "original"
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("method", ["get_sale_status", "get_sale_status_typed"])
async def test_malformed_status_reason_preserves_sdk_error_metadata(async_mode, method):
    body = {"purchaseId": 1, "status": "FAILED", "failureReason": {"unexpected": "object"}}
    client, raw = make_client(async_mode, lambda request: httpx.Response(200, json=body, headers=HEADERS))
    try:
        with pytest.raises(KRAeTIMSError) as caught:
            await invoke(client, method, 1)
        assert not isinstance(caught.value, TIaaSAmbiguousStateError)
        assert caught.value.request_id == "synthetic-request"
        assert caught.value.environment == "SIMULATED"
        assert caught.value.retry_after == "17"
        assert caught.value.http_status == 200
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("reason", [None, "Synthetic prerequisite rejected"])
async def test_valid_status_reason_preserves_dictionary_and_typed_results(async_mode, reason):
    body = {"purchaseId": 1, "status": "FAILED", "failureReason": reason, "extension": {"future": True}}
    client, raw = make_client(async_mode, lambda request: httpx.Response(200, json=body, headers=HEADERS))
    try:
        result = await invoke(client, "get_sale_status", 1)
        assert result == body
        typed = await invoke(client, "get_sale_status_typed", 1)
        assert typed.failure_reason == reason
        assert typed.model_extra == {"extension": {"future": True}}
        assert typed.response_metadata.request_id == "synthetic-request"
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("state", ["SIGNED", "PENDING_SYNC", "OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED", "NOT_SENT", "FAILED", "SYNCED"])
async def test_valid_dictionary_and_typed_outcomes_preserve_body_and_metadata(async_mode, state, minimal_invoice):
    body = {**RECEIPT, "status": state, "extension": {"future": True}}
    client, raw = make_client(async_mode, lambda request: httpx.Response(200, json=body, headers=HEADERS))
    try:
        result = await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
        assert isinstance(result, dict) and result == body
        assert result.response_metadata.request_id == "synthetic-request"
        typed = await invoke(client, "submit_sale_typed", minimal_invoice, idempotency_key="second-operation")
        assert isinstance(typed, SaleResult) and typed.purchase_id == 1 and typed.status == state
        assert typed.response_metadata.environment == "SIMULATED"
        assert typed.model_extra == {"extension": {"future": True}}
        status = await invoke(client, "get_sale_status_typed", 1)
        assert isinstance(status, SaleStatusResult)
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
async def test_historical_metadata_is_unavailable_without_invention(async_mode):
    client, raw = make_client(async_mode, lambda request: httpx.Response(200, json=RECEIPT))
    try:
        result = await invoke(client, "get_sale_status_typed", 1)
        assert result.response_metadata.request_id is None
        assert result.response_metadata.environment is None
        assert result.response_metadata.retry_after is None
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
async def test_configured_api_prefix_cannot_bypass_fiscal_validation(async_mode, minimal_invoice):
    client, raw = make_client(async_mode, lambda request: httpx.Response(200, json={}))
    client.base_url += "/api-prefix"
    try:
        with pytest.raises(TIaaSAmbiguousStateError):
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
        with pytest.raises(KRAeTIMSError):
            await invoke(client, "get_sale_status", 1)
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,body", [(200, b"not-json"), (204, b""), (205, b""), (429, b'{}'), (502, b'{}'), (503, b'{}'), (504, b'{}')])
async def test_http_failures_preserve_headers_and_never_replay(async_mode, status, body, minimal_invoice):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=body, headers=HEADERS)
    client, raw = make_client(async_mode, handler)
    try:
        with pytest.raises(KRAeTIMSError) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
        assert caught.value.idempotency_key == "original"
        assert caught.value.retry_after == "17" and caught.value.request_id == "synthetic-request"
        assert caught.value.environment == "SIMULATED" and caught.value.http_status == status
        if status in {200, 204, 205, 502, 504}: assert isinstance(caught.value, TIaaSAmbiguousStateError)
        assert len(calls) == 1
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.WriteTimeout])
@pytest.mark.parametrize("mutation", [False, True])
async def test_transport_timeout_is_one_attempt_with_explicit_budget(async_mode, failure, mutation, minimal_invoice):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"] == {"connect": 1, "read": 2, "write": 3, "pool": 4}
        raise failure("synthetic lost response", request=request)
    client, raw = make_client(async_mode, handler, timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4))
    try:
        with pytest.raises(TIaaSAmbiguousStateError if mutation else TIaaSUnavailableError) as caught:
            if mutation:
                await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original")
            else:
                await invoke(client, "get_sale_status", 1)
        if mutation: assert caught.value.idempotency_key == "original"
        assert len(calls) == 1
    finally:
        await cleanup(client, raw)


@pytest.mark.parametrize("async_mode", [False, True])
async def test_context_manager_closes_only_owned_http_client(async_mode):
    if async_mode:
        async with AsyncKRAeTIMSClient(api_key="synthetic") as owned:
            assert not owned._http.is_closed
        assert owned._http.is_closed
        borrowed = httpx.AsyncClient()
        async with AsyncKRAeTIMSClient(api_key="synthetic", http_client=borrowed): pass
        assert not borrowed.is_closed
        await borrowed.aclose()
    else:
        with KRAeTIMSClient(api_key="synthetic") as owned:
            assert not owned._http.is_closed
        assert owned._http.is_closed
        with httpx.Client() as borrowed:
            with KRAeTIMSClient(api_key="synthetic", http_client=borrowed): pass
            assert not borrowed.is_closed
