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
