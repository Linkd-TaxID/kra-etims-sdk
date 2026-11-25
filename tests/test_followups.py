"""Follow-up regressions for the SDK contract-drift campaign (P7-P13, D4-D7).

Every HTTP interaction is a synthetic httpx.MockTransport; no live endpoint is called.
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from kra_etims import AsyncKRAeTIMSClient, KRAeTIMSClient

ROOT = Path(__file__).resolve().parents[1]

RECEIPT = {"purchaseId": 7, "status": "SIGNED", "cuInvoiceNumber": "SIM01/7 NS",
           "sdcId": "SIM01", "receiptSignature": "synthetic-signature"}
STOCK = {"operationKey": "op-1", "movementId": 1, "sarNo": 1, "status": "SYNCED",
         "payloadSha256": "a" * 64, "totAmt": 10, "totTaxAmt": 0}
_QUEUED = {
    "tin": "A000123456B", "bhfId": "00", "invcNo": "INV-Q1", "confirmDt": "20260426142211",
    "totItemCnt": 0, "totTaxblAmt": "0.00", "totTaxAmt": "0.00", "totAmt": "0.00", "itemList": [],
}


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


async def close(raw, async_mode):
    await invoke(raw, "aclose" if async_mode else "close")


# ---------------------------------------------------------------------------
# D5 — httpx floor
# ---------------------------------------------------------------------------

def test_httpx_floor_is_0_28():
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 3.10
        import tomli as tomllib
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    httpx_dep = next(d for d in deps if re.match(r"httpx\b", d))
    assert re.fullmatch(r"httpx>=0\.28(\.\d+)?(,<\d+)?", httpx_dep.replace(" ", "")), httpx_dep


# ---------------------------------------------------------------------------
# P7 — an unrecognised 503 on a mutation may follow a commit: it must be
#      catchable as TIaaSAmbiguousStateError while staying a TIaaSUnavailableError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("body", [
    {},
    {"status": "ERROR", "code": "SOMETHING_NEW", "message": "x"},
    {"title": "VSCU Temporarily Unavailable", "status": 503, "vscu_code": "91"},
])
async def test_unknown_503_on_mutation_is_ambiguous(async_mode, body, minimal_invoice):
    from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
    client, raw = make_client(async_mode, respond(503, body))
    try:
        with pytest.raises(TIaaSUnavailableError) as caught:  # existing except-clauses keep matching
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="original-key")
    finally:
        await close(raw, async_mode)
    err = caught.value
    assert isinstance(err, TIaaSAmbiguousStateError)
    assert err.idempotency_key == "original-key"
    assert getattr(err, "retryable", False) is False
    assert err.requires_reconciliation is True


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_unknown_503_on_read_is_not_ambiguous(async_mode):
    from kra_etims.exceptions import TIaaSAmbiguousStateError, TIaaSUnavailableError
    client, raw = make_client(async_mode, respond(503, {}))
    try:
        with pytest.raises(TIaaSUnavailableError) as caught:
            await invoke(client, "get_sale_status", 7)
    finally:
        await close(raw, async_mode)
    assert not isinstance(caught.value, TIaaSAmbiguousStateError)


# ---------------------------------------------------------------------------
# P8 — a queued sale refused before dispatch must stay re-flushable
# ---------------------------------------------------------------------------

def _connect_refused(request):
    raise httpx.ConnectError("synthetic connect failure", request=request)


@pytest.mark.parametrize("handler", [
    respond(503, {"status": "ERROR", "code": "WORKER_STOPPING", "message": "x", "retryable": True},
            {"Retry-After": "5"}),
    respond(403, {"status": "ERROR", "code": "WORKER_DEFERRED", "message": "x", "retryable": True}),
    respond(429, {"status": "ERROR", "code": "RATE_LIMITED", "message": "x"}, {"Retry-After": "1"}),
    _connect_refused,
], ids=["worker_stopping", "worker_deferred", "rate_limited", "connect_error"])
def test_queue_retryable_refusal_stays_pending(tmp_path: Path, handler):
    from kra_etims.cli.queue import Queue
    q = Queue(path=tmp_path / "queue.db")
    q.enqueue(_QUEUED)
    client, raw = make_client(False, handler)
    try:
        result = q.flush_one(client, q.pending()[0])
    finally:
        raw.close()
    assert result["status"] == "pending", result
    assert len(q.pending()) == 1  # the next `etims queue flush` retries it
    assert q.status()["failed"] == 0


def test_queue_rejection_still_fails(tmp_path: Path):
    from kra_etims.cli.queue import Queue
    q = Queue(path=tmp_path / "queue.db")
    q.enqueue(_QUEUED)
    client, raw = make_client(False, respond(400, {"status": "ERROR", "code": "VALIDATION_FAILED", "message": "x"}))
    try:
        result = q.flush_one(client, q.pending()[0])
    finally:
        raw.close()
    assert result["status"] == "failed"
    assert q.pending() == []


# ---------------------------------------------------------------------------
# D4 — PENDING_SYNC rows become 'accepted' and are polled, never resubmitted
# ---------------------------------------------------------------------------

_runner = None


def _flush_cli(db_path: Path, handler):
    global _runner
    from typer.testing import CliRunner
    from kra_etims.cli.main import app
    _runner = _runner or CliRunner()
    client, raw = make_client(False, handler)
    try:
        with patch("kra_etims.cli.queue.data_dir", return_value=db_path.parent), \
                patch("kra_etims.cli._client.get_client", return_value=client):
            return _runner.invoke(app, ["queue", "flush", "--api-key", "dummy", "--json"])
    finally:
        raw.close()


def _row_status(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT status FROM queue").fetchone()[0]


@pytest.mark.parametrize("polled,expected", [
    ({**RECEIPT}, "success"),
    ({"purchaseId": 7, "status": "FAILED", "failureReason": "x"}, "failed"),
    ({"purchaseId": 7, "status": "OUTCOME_UNKNOWN"}, "outcome_unknown"),
    ({"purchaseId": 7, "status": "RECONCILIATION_REQUIRED"}, "outcome_unknown"),
    ({"purchaseId": 7, "status": "PENDING_SYNC"}, "accepted"),
])
def test_accepted_rows_are_polled_not_resubmitted(tmp_path: Path, polled, expected):
    from kra_etims.cli.queue import Queue
    db_path = tmp_path / "queue.db"
    Queue(path=db_path).enqueue(_QUEUED)

    first = _flush_cli(db_path, respond(202, {"purchaseId": 7, "status": "PENDING_SYNC"}))
    assert first.exit_code == 0, first.output
    assert _row_status(db_path) == "accepted"

    seen = []

    def poll(request):
        seen.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/v2/etims/sales/7/status":
            return httpx.Response(200, json=polled)
        return httpx.Response(500, json={})

    second = _flush_cli(db_path, poll)
    assert second.exit_code == 0, second.output
    assert seen == [("GET", "/v2/etims/sales/7/status")]  # never POST /sale again
    assert _row_status(db_path) == expected


def test_queue_db_from_older_sdk_keeps_working(tmp_path: Path):
    from kra_etims.cli.queue import Queue
    db_path = tmp_path / "queue.db"
    with sqlite3.connect(db_path) as conn:  # 0.8.1 schema
        conn.execute("""CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_json TEXT NOT NULL, queued_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")
        conn.execute("INSERT INTO queue (invoice_json, queued_at) VALUES (?, '2026-11-01T00:00:00+00:00')",
                     (json.dumps(_QUEUED),))
    Queue(path=db_path)
    result = _flush_cli(db_path, respond(202, {"purchaseId": 7, "status": "PENDING_SYNC"}))
    assert result.exit_code == 0, result.output
    assert _row_status(db_path) == "accepted"


# ---------------------------------------------------------------------------
# P9 — the README stock example must run as written
# ---------------------------------------------------------------------------

def _readme_call(name: str) -> str:
    text = (ROOT / "README.md").read_text()
    start = text.index(f"client.{name}(")
    depth = 0
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0 and text[i] == ")":
            return text[start:i + 1]
    raise AssertionError("unbalanced snippet")


def test_readme_stock_example_runs():
    from kra_etims import StockAdjustmentLine
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json=STOCK)

    client, raw = make_client(False, handler)
    try:
        exec(_readme_call("submit_stock_adjustment"),
             {"client": client, "StockAdjustmentLine": StockAdjustmentLine})
    finally:
        raw.close()
    assert seen and seen[0].url.path == "/v2/etims/stock/adjustment"
    assert seen[0].headers.get("Idempotency-Key")


# ---------------------------------------------------------------------------
# P10 — legacy update_stock targets a route TaxID answers 501 for
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_update_stock_is_unsupported_without_a_request(async_mode):
    from kra_etims.exceptions import UnsupportedOperationError
    from kra_etims.models import StockItem
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"anything": "unvalidated"})

    client, raw = make_client(async_mode, handler)
    try:
        with pytest.raises(UnsupportedOperationError), pytest.warns(DeprecationWarning):
            await invoke(client, "update_stock",
                         StockItem(tin="A000123456B", bhfId="00", itemCd="KE1", rsonCd="01", qty="1"))
    finally:
        await close(raw, async_mode)
    assert seen == []


# ---------------------------------------------------------------------------
# P11 — GavaConnect POST bodies are SDK-serialised (same bytes on every httpx)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_gavaconnect_body_bytes_are_sdk_owned(async_mode):
    from kra_etims.gavaconnect import AsyncGavaConnectClient, GavaConnectClient
    bodies = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        bodies.append(request.content)
        return httpx.Response(200, json={"ResponseCode": "23000", "PINDATA": {"KRAPIN": "A000123456B"}})

    gc = (AsyncGavaConnectClient if async_mode else GavaConnectClient)(consumer_key="k", consumer_secret="s")
    raw = (httpx.AsyncClient if async_mode else httpx.Client)(transport=httpx.MockTransport(handler))
    gc._http = raw
    try:
        try:
            await invoke(gc, "lookup_pin", "A000123456B")
        except Exception:
            pass  # only the request bytes matter here
    finally:
        await close(raw, async_mode)
    assert bodies == [b'{"KRAPIN":"A000123456B"}']


# ---------------------------------------------------------------------------
# P12 — the supplier gateway honours 429 Retry-After instead of giving up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_gateway_retries_rate_limit_after_retry_after(async_mode, monkeypatch):
    import asyncio
    import time
    import kra_etims.gateway as gw
    slept = []
    monkeypatch.setattr(gw, "_backoff", lambda attempt: 0)
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    async def fake_async_sleep(s):
        slept.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_async_sleep)
    replies = [httpx.Response(429, json={"status": "ERROR", "code": "RATE_LIMITED", "message": "x"},
                              headers={"Retry-After": "2"}),
               httpx.Response(200, json={"requestId": 5, "phone": "+254700000000", "status": "PENDING"})]
    client, raw = make_client(async_mode, lambda request: replies.pop(0))
    try:
        status = await invoke(client.gateway, "get_status", 5)
    finally:
        await close(raw, async_mode)
    assert status.request_id == 5
    assert 2.0 in slept


def test_gateway_caps_retry_after(monkeypatch):
    import kra_etims.gateway as gw
    from kra_etims.exceptions import RateLimitError
    monkeypatch.setattr(gw, "_backoff", lambda attempt: 0.1)
    err = RateLimitError("limited")
    err.retry_after = "3600"
    assert gw._retry_delay(err, 0) == gw._MAX_RETRY_AFTER
    err.retry_after = None
    assert gw._retry_delay(err, 0) == 0.1


# ---------------------------------------------------------------------------
# D6 — unresolved idempotency on a credit note is also a CreditNoteConflictError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,code", [
    (409, "IDEMPOTENT_RESULT_LOOKUP_REQUIRED"), (409, "IDEMPOTENT_IDENTITY_UNAVAILABLE"),
    (409, "IDEMPOTENCY_SCOPE_UNBOUND"), (425, "IDEMPOTENT_RESULT_LOOKUP_REQUIRED"),
])
async def test_credit_note_unresolved_outcome_error(async_mode, status, code):
    import kra_etims.exceptions as exc_mod
    client, raw = make_client(async_mode, respond(status, {"status": "ERROR", "code": code, "message": "x"}))
    try:
        with pytest.raises(exc_mod.IdempotentOutcomeUnresolvedError) as caught:
            await invoke(client, "issue_credit_note", 7, idempotency_key="cn-key")
    finally:
        await close(raw, async_mode)
    err = caught.value
    assert type(err).__name__ == "CreditNoteOutcomeUnresolvedError"
    assert isinstance(err, exc_mod.CreditNoteConflictError)
    assert isinstance(err, exc_mod.TIaaSAmbiguousStateError)
    assert err.code == code and err.idempotency_key == "cn-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_sale_unresolved_outcome_is_not_a_credit_note_error(async_mode, minimal_invoice):
    import kra_etims.exceptions as exc_mod
    body = {"status": "ERROR", "code": "IDEMPOTENT_RESULT_LOOKUP_REQUIRED", "message": "x"}
    client, raw = make_client(async_mode, respond(409, body))
    try:
        with pytest.raises(exc_mod.IdempotentOutcomeUnresolvedError) as caught:
            await invoke(client, "submit_sale", minimal_invoice, idempotency_key="k")
    finally:
        await close(raw, async_mode)
    assert not isinstance(caught.value, exc_mod.CreditNoteConflictError)


# ---------------------------------------------------------------------------
# D7 — ZReportAlreadyIssuedError is a KRAConflictError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_z_report_already_issued_is_a_conflict(async_mode):
    from kra_etims.exceptions import KRAConflictError, ZReportAlreadyIssuedError
    body = {"status": "ERROR", "code": "Z_REPORT_ALREADY_ISSUED", "message": "x"}
    client, raw = make_client(async_mode, respond(409, body))
    try:
        with pytest.raises(KRAConflictError) as caught:
            await invoke(client.reports, "get_daily_z", "2026-11-20")
    finally:
        await close(raw, async_mode)
    assert isinstance(caught.value, ZReportAlreadyIssuedError)
    assert caught.value.code == "Z_REPORT_ALREADY_ISSUED"
    assert caught.value.report_date == "2026-11-20"
    assert caught.value.http_status == 409


_KEYED_CALLS = {"submit_stock_adjustment", "submit_stock_master"}


def _calls_missing_idempotency_key(source: str, origin: str) -> list[str]:
    import ast
    missing = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _KEYED_CALLS
                and not any(kw.arg in ("idempotency_key", None) for kw in node.keywords)):
            missing.append(f"{origin}:{node.lineno}")
    return missing


def test_examples_pass_idempotency_key_to_stock_calls():
    import textwrap
    missing = []
    for path in sorted((ROOT / "examples").rglob("*.py")):
        missing += _calls_missing_idempotency_key(path.read_text(), path.relative_to(ROOT).as_posix())
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for i, block in enumerate(re.findall(r"```python\n(.*?)```", path.read_text(), re.S)):
            try:
                missing += _calls_missing_idempotency_key(textwrap.dedent(block), f"{path.name}#block{i}")
            except SyntaxError:
                continue  # illustrative fragments
    assert missing == [], f"stock calls without idempotency_key (raise ValueError): {missing}"


# ---------------------------------------------------------------------------
# Over-quantity credit notes are typed like over-amount ones
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_credit_note_quantity_exceeds_original_is_typed(async_mode):
    import kra_etims.exceptions as exc_mod
    client, raw = make_client(async_mode, respond(422, {
        "status": "ERROR", "code": "CREDIT_NOTE_QUANTITY_EXCEEDS_ORIGINAL",
        "message": "Too many", "originalItemSeq": 2, "remainingQuantity": 1.5}))
    try:
        with pytest.raises(exc_mod.CreditNoteExceedsOriginalError) as caught:
            await invoke(client, "issue_credit_note", 7, idempotency_key="cn-key")
    finally:
        await close(raw, async_mode)
    err = caught.value
    assert type(err).__name__ == "CreditNoteQuantityExceedsOriginalError"
    assert err.original_item_seq == 2
    assert str(err.remaining_quantity) == "1.5"
    assert err.remaining is None
    assert "Too many" in str(err)


# ---------------------------------------------------------------------------
# A transient OSCU 503 on a mutation is ambiguous; on a read it is not
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_oscu_503_on_a_mutation_is_ambiguous_and_keeps_the_key(async_mode):
    import kra_etims.exceptions as exc_mod
    client, raw = make_client(async_mode, respond(503, {"oscu_code": "894", "title": "OSCU Signing Error"}))
    try:
        with pytest.raises(exc_mod.OSCUUnavailableError) as caught:
            await invoke(client, "issue_credit_note", 7, idempotency_key="cn-key")
        with pytest.raises(exc_mod.OSCUUnavailableError) as read:
            await invoke(client, "get_sale_status", 7)
    finally:
        await close(raw, async_mode)
    assert isinstance(caught.value, exc_mod.TIaaSAmbiguousStateError)
    assert caught.value.oscu_code == "894"
    assert caught.value.idempotency_key == "cn-key"
    assert not isinstance(read.value, exc_mod.TIaaSAmbiguousStateError)


def test_accepted_row_the_server_does_not_know_is_not_polled_forever(tmp_path):
    from kra_etims.cli.queue import Queue
    from kra_etims.exceptions import KRAeTIMSError
    queue = Queue(tmp_path / "queue.db")
    row_id = queue.enqueue({"x": 1})
    queue._update(row_id, "accepted", None, purchase_id=7)

    class Client:
        def get_sale_status(self, purchase_id):
            error = KRAeTIMSError("No sale with invcNo=7")
            error.http_status = 404
            raise error

    result = queue.poll_one(Client(), queue.accepted()[0])
    assert result["status"] == "outcome_unknown"
    assert queue.accepted() == []
