"""Workstream B contract-drift regressions (SDK <-> TaxID backend wire contract).

Every HTTP interaction is a synthetic httpx.MockTransport; no live endpoint is called.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest

from kra_etims import AsyncKRAeTIMSClient, KRAeTIMSClient

RECEIPT = {"purchaseId": 7, "status": "SIGNED", "cuInvoiceNumber": "SIM01/7 NS",
           "sdcId": "SIM01", "receiptSignature": "synthetic-signature"}


async def invoke(client, method, *args, **kwargs):
    result = getattr(client, method)(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def make_client(async_mode, handler):
    raw_type, sdk_type = (httpx.AsyncClient, AsyncKRAeTIMSClient) if async_mode else (httpx.Client, KRAeTIMSClient)
    raw = raw_type(transport=httpx.MockTransport(handler))
    return sdk_type(api_key="synthetic", base_url="https://synthetic.invalid", http_client=raw), raw


def respond(status, body, headers=None):
    def handler(request):
        return httpx.Response(status, json=body, headers=headers or {})
    return handler


# ---------------------------------------------------------------------------
# B1 — CLI offline queue must classify by the body's fiscal status
# ---------------------------------------------------------------------------

_QUEUED = {
    "tin": "A000123456B", "bhfId": "00", "invcNo": "INV-Q1", "confirmDt": "20260426142211",
    "totItemCnt": 0, "totTaxblAmt": "0.00", "totTaxAmt": "0.00", "totAmt": "0.00", "itemList": [],
}


@pytest.mark.parametrize("http_status,body,expected", [
    (200, RECEIPT, "success"),
    (202, {"purchaseId": 7, "status": "PENDING_SYNC"}, "pending"),
    (200, {"purchaseId": 7, "status": "OUTCOME_UNKNOWN"}, "outcome_unknown"),
    (200, {"purchaseId": 7, "status": "RECONCILIATION_REQUIRED"}, "outcome_unknown"),
    (200, {"purchaseId": 7, "status": "NOT_SENT"}, "failed"),
    (200, {"purchaseId": 7, "status": "FAILED"}, "failed"),
    (200, {"purchaseId": 7, "status": "QUARANTINED"}, "outcome_unknown"),
])
def test_queue_flush_uses_body_status(tmp_path: Path, http_status, body, expected):
    from kra_etims.cli.queue import Queue
    q = Queue(path=tmp_path / "queue.db")
    q.enqueue(_QUEUED)
    client, raw = make_client(False, respond(http_status, body))
    try:
        result = q.flush_one(client, q.pending()[0])
    finally:
        raw.close()
    assert result["status"] == expected
    with q._connect() as conn:
        assert conn.execute("SELECT status FROM queue").fetchone()[0] == expected


def test_queue_flush_never_counts_unvalidated_return_as_success(tmp_path: Path):
    """A client double that returns an unknown status must not be counted as success."""
    from unittest.mock import MagicMock
    from kra_etims.cli.queue import Queue
    q = Queue(path=tmp_path / "queue.db")
    q.enqueue(_QUEUED)
    client = MagicMock()
    client.submit_sale.return_value = {"purchaseId": 7, "status": "SOMETHING_NEW"}
    assert q.flush_one(client, q.pending()[0])["status"] != "success"
    q2 = Queue(path=tmp_path / "queue2.db")
    q2.enqueue(_QUEUED)
    client.submit_sale.return_value = {"purchaseId": 7, "status": "SIGNED"}  # no evidence
    assert q2.flush_one(client, q2.pending()[0])["status"] != "success"


# ---------------------------------------------------------------------------
# B2 — IdempotencyFilter 409 codes are ambiguous outcomes, not plain conflicts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("code", ["IDEMPOTENT_RESULT_LOOKUP_REQUIRED", "IDEMPOTENT_IDENTITY_UNAVAILABLE",
                                  "IDEMPOTENCY_SCOPE_UNBOUND"])
@pytest.mark.parametrize("operation", ["sale", "credit_note"])
async def test_idempotency_409_codes_raise_ambiguous_state(async_mode, code, operation, minimal_invoice):
    from kra_etims.exceptions import KRAConflictError, TIaaSAmbiguousStateError
    body = {"status": 409, "code": code, "message": "synthetic"}
    client, raw = make_client(async_mode, respond(409, body))
    try:
        with pytest.raises(TIaaSAmbiguousStateError) as caught:
            if operation == "sale":
                await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original-key")
            else:
                await invoke(client, "issue_credit_note", 7, idempotency_key="original-key")
        assert caught.value.idempotency_key == "original-key"
        assert caught.value.code == code
        assert caught.value.http_status == 409
        assert isinstance(caught.value, KRAConflictError)  # existing except-clauses keep matching
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_in_flight_425_keeps_code(async_mode, minimal_invoice):
    from kra_etims.exceptions import TIaaSAmbiguousStateError
    body = {"status": 425, "code": "IDEMPOTENT_RESULT_LOOKUP_REQUIRED", "message": "synthetic"}
    client, raw = make_client(async_mode, respond(425, body))
    try:
        with pytest.raises(TIaaSAmbiguousStateError) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original-key")
        assert caught.value.code == "IDEMPOTENT_RESULT_LOOKUP_REQUIRED"
        assert caught.value.idempotency_key == "original-key"
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_other_409_codes_stay_plain_conflicts(async_mode, minimal_invoice):
    from kra_etims.exceptions import KRAConflictError, TIaaSAmbiguousStateError
    client, raw = make_client(async_mode, respond(409, {"status": 409, "code": "FISCAL_DAY_CLOSED", "message": "x"}))
    try:
        with pytest.raises(KRAConflictError) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="k")
        assert not isinstance(caught.value, TIaaSAmbiguousStateError)
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


# ---------------------------------------------------------------------------
# B3 — request body bytes are SDK-owned, not httpx-version-dependent
# ---------------------------------------------------------------------------

_PINNED_PAYLOAD = {"supplierPin": "A000111222B", "customerName": "Café Ndovu", "amount": 116.0,
                   "items": [{"itemName": "Chai", "qty": 1}]}
_PINNED_BYTES = ('{"supplierPin":"A000111222B","customerName":"Café Ndovu","amount":116.0,'
                 '"items":[{"itemName":"Chai","qty":1}]}').encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_request_body_bytes_are_pinned(async_mode):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=RECEIPT)

    client, raw = make_client(async_mode, handler)
    try:
        await invoke(client, "_request", "POST", "/v2/etims/sale", json=_PINNED_PAYLOAD, idempotency_key="k")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert seen[0].content == _PINNED_BYTES
    assert seen[0].headers["Content-Type"] == "application/json"
    assert seen[0].headers["Content-Length"] == str(len(_PINNED_BYTES))


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_submit_sale_body_is_compact_utf8(async_mode, minimal_invoice):
    from kra_etims.models import to_middleware_sale_payload
    seen = []

    def handler(request):
        seen.append(request.content)
        return httpx.Response(200, json=RECEIPT)

    client, raw = make_client(async_mode, handler)
    try:
        await invoke(client, "submit_sale", minimal_invoice, idempotency_key="k")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    expected = json.dumps(to_middleware_sale_payload(minimal_invoice), ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert seen == [expected]


def test_body_encoder_rejects_nan():
    from kra_etims._base_client import _encode_json_body
    with pytest.raises(ValueError):
        _encode_json_body({"amount": float("nan")})


# ---------------------------------------------------------------------------
# B4 — outcome validation + response metadata on the other fiscal routes
# ---------------------------------------------------------------------------

META = {"X-Request-Id": "synthetic-request", "X-TaxID-Environment": "SIMULATED"}
CREDIT_NOTE = {"creditNoteId": 11, "originalPurchaseId": 7, "status": "SIGNED",
               "cuInvoiceNumber": "SIM01/11 NC", "sdcId": "SIM01", "receiptSignature": "sig"}
STOCK = {"operationKey": "op-1", "movementId": 1, "sarNo": 1, "status": "SYNCED",
         "payloadSha256": "a" * 64, "totAmt": 10, "totTaxAmt": 0}
STOCK_MASTER = {"operationKey": "m-1", "originalIoKey": "op-1", "itemCd": "KE1", "status": "SYNCED",
                "payloadSha256": "b" * 64}


def _stock_line():
    from kra_etims.models import StockAdjustmentLine
    from decimal import Decimal
    return StockAdjustmentLine(itemCd="KE1NTXU0000001", itemNm="Chai", ioType="M",
                               qty=Decimal("1"), prc=Decimal("10"), taxTyCd="A")


def _stock_master():
    from kra_etims.models import StockMasterRequest
    return StockMasterRequest(originalIoKey="op-1", itemCd="KE1NTXU0000001", rsdQty="1")


_READS = [
    ("find_sale", (), {"client_reference": "ref-1"}),
    ("get_sale_original", (7,), {}),
]
_MUTATIONS = [
    ("issue_credit_note", (7,), {"idempotency_key": "cn-key"}, "cn-key"),
    ("submit_stock_adjustment", (), {"idempotency_key": "st-key"}, "st-key"),
    ("submit_stock_master", (), {"idempotency_key": "sm-key"}, "sm-key"),
]


def _mutation_args(method, args):
    if method == "submit_stock_adjustment":
        return ([_stock_line()],)
    if method == "submit_stock_master":
        return (_stock_master(),)
    return args


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("body", [{"purchaseId": 7, "status": "SIGNED"}, {"purchaseId": 7, "status": "NEW_UNKNOWN"},
                                  {"status": "SIGNED"}, []])
async def test_find_sale_rejects_incomplete_outcomes(async_mode, body):
    from kra_etims.exceptions import KRAeTIMSError, TIaaSAmbiguousStateError
    client, raw = make_client(async_mode, respond(200, body, META))
    try:
        with pytest.raises(KRAeTIMSError) as caught:
            await invoke(client, "find_sale", client_reference="ref-1")
        assert not isinstance(caught.value, TIaaSAmbiguousStateError)
        assert caught.value.request_id == "synthetic-request"
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_reads_return_dicts_with_metadata(async_mode):
    original = {"purchaseId": 7, "payload": "{}", "payloadKind": "BUSINESS_SNAPSHOT_BEFORE_PROTOCOL_ADAPTATION"}
    for method, args, kwargs in _READS:
        body = {**RECEIPT, "status": "PENDING_SYNC"} if method == "find_sale" else original
        client, raw = make_client(async_mode, respond(200, body, META))
        try:
            result = await invoke(client, method, *args, **kwargs)
        finally:
            await invoke(raw, "aclose" if async_mode else "close")
        assert isinstance(result, dict) and result == body
        assert result.response_metadata.request_id == "synthetic-request", method


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("method,args,kwargs,key", _MUTATIONS)
async def test_fiscal_mutations_raise_ambiguous_on_malformed_body(async_mode, method, args, kwargs, key):
    from pydantic import ValidationError
    from kra_etims.exceptions import TIaaSAmbiguousStateError
    malformed = {"creditNoteId": 11, "originalPurchaseId": 7, "status": "SIGNED"} \
        if method == "issue_credit_note" else {"status": "SYNCED"}
    client, raw = make_client(async_mode, respond(200, malformed, META))
    try:
        with pytest.raises(TIaaSAmbiguousStateError) as caught:
            await invoke(client, method, *_mutation_args(method, args), **kwargs)
        assert not isinstance(caught.value, ValidationError)
        assert caught.value.idempotency_key == key
        assert caught.value.request_id == "synthetic-request"
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("method,args,kwargs,key", _MUTATIONS)
async def test_fiscal_mutations_keep_return_types_and_metadata(async_mode, method, args, kwargs, key):
    body = {"issue_credit_note": CREDIT_NOTE, "submit_stock_adjustment": STOCK,
            "submit_stock_master": STOCK_MASTER}[method]
    client, raw = make_client(async_mode, respond(200, body, META))
    try:
        result = await invoke(client, method, *_mutation_args(method, args), **kwargs)
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    if method == "issue_credit_note":
        assert isinstance(result, dict) and result == CREDIT_NOTE
    else:
        assert result.operationKey == body["operationKey"]
        assert "response_metadata" not in result.model_dump()
    assert result.response_metadata.request_id == "synthetic-request"


# ---------------------------------------------------------------------------
# B5 — worker refusals are retryable; 503 is not always the 24-hour ceiling;
#      Z-report 409 code is read from "code" or legacy "error"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,code,error_type", [
    (403, "WORKER_DEFERRED", "WorkerDeferredError"),
    (503, "WORKER_STOPPING", "WorkerStoppingError"),
])
async def test_worker_refusals_are_retryable(async_mode, status, code, error_type, minimal_invoice):
    import kra_etims.exceptions as exc_mod
    from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
    body = {"status": status, "code": code, "message": "synthetic", "retryable": True}
    client, raw = make_client(async_mode, respond(status, body))
    try:
        with pytest.raises(getattr(exc_mod, error_type)) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="k")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    err = caught.value
    assert isinstance(err, TIaaSUnavailableError) and isinstance(err, exc_mod.FiscalWorkerUnavailableError)
    assert not isinstance(err, TIaaSAmbiguousStateError)
    assert err.code == code and err.retryable is True
    assert not getattr(err, "requires_reconciliation", False)
    assert err.idempotency_key == "k" and err.http_status == status
    if status == 403:
        assert isinstance(err, exc_mod.KRAAuthorizationError)  # existing 403 handlers still match


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("body", [{}, {"status": 503, "code": "SOMETHING_NEW", "message": "x"},
                                  {"title": "VSCU Temporarily Unavailable", "status": 503, "vscu_code": "96"}])
async def test_unknown_503_is_not_the_24_hour_ceiling(async_mode, body):
    from kra_etims.exceptions import KRAConnectivityTimeoutError, TIaaSUnavailableError
    client, raw = make_client(async_mode, respond(503, body))
    try:
        with pytest.raises(TIaaSUnavailableError) as caught:
            await invoke(client, "get_sale_status", 7)
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert not isinstance(caught.value, KRAConnectivityTimeoutError)
    assert caught.value.http_status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_suspended_503_is_the_24_hour_ceiling(async_mode, minimal_invoice):
    from kra_etims.exceptions import KRAConnectivityTimeoutError
    body = {"status": 503, "code": "KRA_CONNECTIVITY_TIMEOUT", "message": "System suspended"}
    client, raw = make_client(async_mode, respond(503, body))
    try:
        with pytest.raises(KRAConnectivityTimeoutError) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="k")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert caught.value.code == "KRA_CONNECTIVITY_TIMEOUT"
    assert caught.value.requires_reconciliation is True


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("body", [
    {"error": "Z_REPORT_ALREADY_ISSUED", "message": "already issued"},                       # older backends
    {"status": 409, "code": "Z_REPORT_ALREADY_ISSUED", "error": "Z_REPORT_ALREADY_ISSUED", "message": "x"},
    {"status": 409, "code": "Z_REPORT_ALREADY_ISSUED", "message": "x"},
])
async def test_z_report_conflict_raises_typed_error(async_mode, body):
    from kra_etims.exceptions import ZReportAlreadyIssuedError
    client, raw = make_client(async_mode, respond(409, body))
    try:
        with pytest.raises(ZReportAlreadyIssuedError):
            await invoke(client.reports, "get_daily_z", "2026-11-20")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_other_z_report_conflicts_propagate(async_mode):
    from kra_etims.exceptions import KRAConflictError, ZReportAlreadyIssuedError
    client, raw = make_client(async_mode, respond(409, {"error": "Conflict", "message": "x"}))
    try:
        with pytest.raises(KRAConflictError) as caught:
            await invoke(client.reports, "get_daily_z", "2026-11-20")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert not isinstance(caught.value, ZReportAlreadyIssuedError)


# ---------------------------------------------------------------------------
# B6 — routes that do not exist on the backend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_lookup_pin_uses_customer_lookup_route(async_mode):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"pin": "A000123456B", "status": "FOUND"})

    client, raw = make_client(async_mode, handler)
    try:
        result = await invoke(client, "lookup_pin", "A000123456B")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v2/etims/customers/lookup"
    assert seen[0].url.params["pin"] == "A000123456B"
    assert result["status"] == "FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_check_compliance_fails_clearly_without_a_request(async_mode):
    from kra_etims.exceptions import KRAeTIMSError, UnsupportedOperationError
    seen = []
    client, raw = make_client(async_mode, lambda request: seen.append(request) or httpx.Response(404))
    try:
        with pytest.warns(DeprecationWarning), pytest.raises(UnsupportedOperationError) as caught:
            await invoke(client, "check_compliance", "A000123456B")
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert isinstance(caught.value, KRAeTIMSError) and isinstance(caught.value, NotImplementedError)
    assert "device_health" in str(caught.value)
    assert seen == []


def test_cli_device_status_reports_unsupported_route():
    from unittest.mock import patch
    from typer.testing import CliRunner
    from kra_etims.cli.main import app
    client, raw = make_client(False, lambda request: httpx.Response(404))
    try:
        with patch("kra_etims.cli._client.get_client", return_value=client), \
                pytest.warns(DeprecationWarning):
            result = CliRunner().invoke(app, ["device", "status", "--pin", "A000123456B", "--api-key", "k"])
    finally:
        raw.close()
    assert result.exit_code == 1
    assert "not supported" in result.output


# ---------------------------------------------------------------------------
# B7 — typed 429 with parsed Retry-After; .code on generic errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("header,expected", [
    ("30", 30.0), ("0", 0.0), (None, None), ("soon", None), ("-5", None),
])
async def test_rate_limit_error_parses_retry_after_seconds(async_mode, header, expected):
    from kra_etims.exceptions import KRAeTIMSError, RateLimitError
    headers = {"Retry-After": header} if header is not None else {}
    body = {"status": 429, "code": "RATE_LIMITED", "message": "Too many requests"}
    client, raw = make_client(async_mode, respond(429, body, headers))
    try:
        with pytest.raises(RateLimitError) as caught:
            await invoke(client, "get_sale_status", 7)
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    err = caught.value
    assert isinstance(err, KRAeTIMSError)
    assert err.code == "RATE_LIMITED" and err.http_status == 429
    assert err.retry_after == header  # raw header kept for compatibility
    assert err.retry_after_seconds == expected


def test_rate_limit_error_parses_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    from kra_etims.exceptions import RateLimitError
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    client, raw = make_client(False, respond(429, {"code": "RATE_LIMITED", "message": "x"},
                                             {"Retry-After": format_datetime(future, usegmt=True)}))
    try:
        with pytest.raises(RateLimitError) as caught:
            client.get_sale_status(7)
    finally:
        raw.close()
    assert 100 <= caught.value.retry_after_seconds <= 121
    past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=60), usegmt=True)
    assert RateLimitError.parse_retry_after(past) == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_generic_errors_expose_code(async_mode):
    client, raw = make_client(async_mode, respond(400, {"status": 400, "code": "INVALID_REQUEST", "message": "bad"}))
    try:
        from kra_etims.exceptions import KRAeTIMSError
        with pytest.raises(KRAeTIMSError) as caught:
            await invoke(client, "get_sale_status", 7)
    finally:
        await invoke(raw, "aclose" if async_mode else "close")
    assert caught.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------

def test_gateway_does_not_resend_mutation_after_server_503(monkeypatch):
    import kra_etims.gateway as gw
    from kra_etims.exceptions import TIaaSUnavailableError
    monkeypatch.setattr(gw, "_backoff", lambda attempt: 0)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={})

    client, raw = make_client(False, handler)
    try:
        with pytest.raises(TIaaSUnavailableError):
            client.gateway._execute("POST", "/v2/gateway/supplier-onboarding/single", json={"a": 1})
    finally:
        raw.close()
    assert len(calls) == 1


def test_gateway_retries_worker_refusal(monkeypatch):
    import kra_etims.gateway as gw
    monkeypatch.setattr(gw, "_backoff", lambda attempt: 0)
    replies = [httpx.Response(503, json={"code": "WORKER_STOPPING", "message": "x", "retryable": True}),
               httpx.Response(200, json={"ok": True})]
    client, raw = make_client(False, lambda request: replies.pop(0))
    try:
        assert client.gateway._execute("POST", "/v2/gateway/supplier-onboarding/single", json={"a": 1}) == {"ok": True}
    finally:
        raw.close()


def test_flush_outcome_marks_rate_limit_retryable():
    from kra_etims._base_client import _BaseKRAeTIMSClient
    from kra_etims.exceptions import RateLimitError
    row = _BaseKRAeTIMSClient._flush_outcome("1", "k", RateLimitError("limited"))
    assert row["retryable"] is True and row["ambiguous"] is False


def test_retry_after_ignores_non_ascii_digits():
    from kra_etims.exceptions import RateLimitError
    assert RateLimitError.parse_retry_after("²") is None


def test_cli_pin_validate_reads_customer_lookup_shape():
    from unittest.mock import MagicMock, patch
    from typer.testing import CliRunner
    from kra_etims.cli.main import app
    client = MagicMock()
    client.lookup_pin.return_value = {"pin": "A000123456B", "status": "FOUND", "name": "Synthetic Co",
                                      "taxpayerStatus": "A"}
    with patch("kra_etims.cli._client.get_client", return_value=client), \
            patch("kra_etims.cli._client.resolve_gavaconnect_creds", return_value=None):
        result = CliRunner().invoke(app, ["pin", "validate", "A000123456B", "--api-key", "k"])
    assert result.exit_code == 0, result.output
    assert "Synthetic Co" in result.output and "FOUND" in result.output
    assert "Active" not in result.output or "No" not in result.output.split("Active")[1].splitlines()[0]
