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
