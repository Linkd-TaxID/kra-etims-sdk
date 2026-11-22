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
