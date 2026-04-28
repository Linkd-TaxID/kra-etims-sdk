"""
Tests for step 7:
  Queue class  — SQLite offline queue (unit tests against a real DB via tmp_path)
  etims queue status / flush — CLI commands

Two layers:
1. Queue unit tests use real SQLite (Queue(path=tmp_path/"queue.db")).
   No mocking — exercises actual WAL, enqueue, flush_one, and status logic.

2. CLI tests redirect Queue to tmp_path by patching kra_etims.cli.queue.data_dir,
   so that Queue() instantiated inside the command body uses the temp DB.
   get_client is patched at kra_etims.cli._client.get_client (lazy import site).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app
from kra_etims.cli.queue import Queue
from kra_etims.exceptions import KRADuplicateInvoiceError

runner = CliRunner()

_CLIENT_MOD  = "kra_etims.cli._client"
_QUEUE_MOD   = "kra_etims.cli.queue"

# ---------------------------------------------------------------------------
# Minimal valid SaleInvoice payload (Band A, math correct)
# ---------------------------------------------------------------------------

_ITEM = {
    "itemCd": "ITEM001",
    "itemNm": "Test Item",
    "qty": "1",
    "uprc": "1000.00",
    "totAmt": "1000.00",
    "taxTyCd": "A",
    "taxblAmt": "1000.00",
    "taxAmt": "0.00",
}

def _invoice(invc_no: str = "INV-001") -> dict:
    return {
        "tin": "A000123456B",
        "bhfId": "00",
        "invcNo": invc_no,
        "confirmDt": "20260426142211",
        "totItemCnt": 1,
        "totTaxblAmt": "1000.00",
        "totTaxAmt": "0.00",
        "totAmt": "1000.00",
        "itemList": [_ITEM],
    }


# ===========================================================================
# Queue unit tests — real SQLite, no mocks
# ===========================================================================

class TestQueueUnit:

    @pytest.fixture()
    def q(self, tmp_path: Path) -> Queue:
        return Queue(path=tmp_path / "queue.db")

    # --- schema / init ---

    def test_wal_mode_enabled(self, q: Queue) -> None:
        with q._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_empty_queue_all_zeros(self, q: Queue) -> None:
        s = q.status()
        assert s["pending"] == 0
        assert s["failed"] == 0
        assert s["completed"] == 0
        assert s["total"] == 0

    def test_status_path_matches_db(self, q: Queue) -> None:
        s = q.status()
        assert Path(s["path"]) == q.path

    # --- enqueue ---

    def test_enqueue_returns_integer_id(self, q: Queue) -> None:
        row_id = q.enqueue(_invoice())
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_enqueue_sequential_ids(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice("INV-001"))
        id2 = q.enqueue(_invoice("INV-002"))
        assert id2 == id1 + 1

    def test_enqueue_appears_in_pending(self, q: Queue) -> None:
        q.enqueue(_invoice())
        assert len(q.pending()) == 1

    def test_enqueue_stores_json_correctly(self, q: Queue) -> None:
        inv = _invoice("INV-JSON")
        q.enqueue(inv)
        row = q.pending()[0]
        stored = json.loads(row["invoice_json"])
        assert stored["invcNo"] == "INV-JSON"

    def test_pending_returns_only_pending_rows(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        q._update(id1, "success")           # mark first as done
        pending = q.pending()
        assert len(pending) == 1
        assert json.loads(pending[0]["invoice_json"])["invcNo"] == "INV-002"

    def test_pending_order_is_fifo(self, q: Queue) -> None:
        q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        rows = q.pending()
        nos = [json.loads(r["invoice_json"])["invcNo"] for r in rows]
        assert nos == ["INV-001", "INV-002"]

    # --- status counts ---

    def test_status_counts_pending(self, q: Queue) -> None:
        q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        assert q.status()["pending"] == 2

    def test_status_counts_failed(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice())
        q._update(id1, "failed", "timeout")
        assert q.status()["failed"] == 1
        assert q.status()["pending"] == 0

    def test_status_completed_includes_success(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice("INV-001"))
        q._update(id1, "success")
        assert q.status()["completed"] == 1

    def test_status_completed_includes_already_processed(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice("INV-001"))
        id2 = q.enqueue(_invoice("INV-002"))
        q._update(id1, "success")
        q._update(id2, "already_processed")
        s = q.status()
        assert s["completed"] == 2
        assert s["total"] == 2

    def test_status_total_is_sum_of_all(self, q: Queue) -> None:
        id1 = q.enqueue(_invoice("INV-001"))
        id2 = q.enqueue(_invoice("INV-002"))
        q.enqueue(_invoice("INV-003"))
        q._update(id1, "success")
        q._update(id2, "failed")
        s = q.status()
        assert s["total"] == 3

    # --- flush_one ---

    def test_flush_one_success_calls_submit_sale(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        client.submit_sale.return_value = {"resultCd": "000"}
        row = q.pending()[0]
        result = q.flush_one(client, row)
        assert result["status"] == "success"
        client.submit_sale.assert_called_once()

    def test_flush_one_success_uses_idempotency_key(self, q: Queue) -> None:
        q.enqueue(_invoice("INV-IDEM"))
        client = MagicMock()
        row = q.pending()[0]
        q.flush_one(client, row)
        _, kwargs = client.submit_sale.call_args
        # Key includes bhfId to prevent collision across branches of the same TIN.
        assert kwargs["idempotency_key"] == "A000123456B:00:INV-IDEM"

    def test_flush_one_success_updates_db_status(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        row = q.pending()[0]
        q.flush_one(client, row)
        assert len(q.pending()) == 0     # no longer pending
        s = q.status()
        assert s["completed"] == 1

    def test_flush_one_duplicate_invoice_error(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        client.submit_sale.side_effect = KRADuplicateInvoiceError("dup")
        row = q.pending()[0]
        result = q.flush_one(client, row)
        assert result["status"] == "already_processed"
        assert q.status()["completed"] == 1

    def test_flush_one_generic_exception_marks_failed(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        client.submit_sale.side_effect = RuntimeError("network down")
        row = q.pending()[0]
        result = q.flush_one(client, row)
        assert result["status"] == "failed"
        assert "network down" in result["error"]
        assert q.status()["failed"] == 1

    def test_flush_one_failed_not_in_pending(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        client.submit_sale.side_effect = RuntimeError("err")
        row = q.pending()[0]
        q.flush_one(client, row)
        assert len(q.pending()) == 0

    def test_flush_one_increments_attempts(self, q: Queue) -> None:
        q.enqueue(_invoice())
        client = MagicMock()
        client.submit_sale.side_effect = RuntimeError("err")
        row = q.pending()[0]
        q.flush_one(client, row)
        with q._connect() as conn:
            attempts = conn.execute("SELECT attempts FROM queue").fetchone()[0]
        assert attempts == 1


# ===========================================================================
# CLI queue status — redirected Queue via patched data_dir
# ===========================================================================

class TestQueueStatusCLI:

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        return tmp_path / "queue.db"

    def _queue(self, db_path: Path) -> Queue:
        return Queue(path=db_path)

    def test_status_empty_queue(self, db_path: Path) -> None:
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            result = runner.invoke(app, ["queue", "status"])
        assert result.exit_code == 0, result.output
        assert "0" in result.output

    def test_status_shows_pending_count(self, db_path: Path) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            result = runner.invoke(app, ["queue", "status"])
        assert result.exit_code == 0, result.output
        assert "2" in result.output

    def test_status_json_structure(self, db_path: Path) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice())
        id1 = q.enqueue(_invoice("INV-002"))
        q._update(id1, "success")
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            result = runner.invoke(app, ["queue", "status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["pending"] == 1
        assert payload["completed"] == 1
        assert payload["total"] == 2
        assert "path" in payload

    def test_status_shows_queue_file_path(self, db_path: Path) -> None:
        # Pre-create the DB so _init_db sees db_is_new=False and the CVE warning
        # is not printed to stderr (which the CliRunner mixes into result.output,
        # breaking json.loads).
        Queue(path=db_path)
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            result = runner.invoke(app, ["queue", "status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "queue.db" in payload["path"]


# ===========================================================================
# CLI queue flush — redirected Queue + mocked get_client
# ===========================================================================

class TestQueueFlushCLI:

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        return tmp_path / "queue.db"

    def _queue(self, db_path: Path) -> Queue:
        return Queue(path=db_path)

    @pytest.fixture()
    def mock_client(self) -> MagicMock:
        client = MagicMock()
        client.submit_sale.return_value = {"resultCd": "000"}
        return client

    def test_flush_empty_queue_skips_api(self, db_path: Path, mock_client: MagicMock) -> None:
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        assert "empty" in result.output.lower() or "nothing" in result.output.lower()
        mock_client.submit_sale.assert_not_called()

    def test_flush_one_invoice_success(self, db_path: Path, mock_client: MagicMock) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice())
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        assert "1/1" in result.output or "1" in result.output
        mock_client.submit_sale.assert_called_once()

    def test_flush_updates_db_after_success(self, db_path: Path, mock_client: MagicMock) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice())
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                runner.invoke(app, ["queue", "flush", "--api-key", "dummy"])
        assert q.status()["completed"] == 1
        assert q.status()["pending"] == 0

    def test_flush_json_output(self, db_path: Path, mock_client: MagicMock) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice("INV-001"))
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["status"] == "success"

    def test_flush_mixed_results_json(self, db_path: Path, mock_client: MagicMock) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        # Second call raises a generic error
        mock_client.submit_sale.side_effect = [
            {"resultCd": "000"},
            RuntimeError("timeout"),
        ]
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy", "--json"])
        payload = json.loads(result.output)
        statuses = {r["status"] for r in payload}
        assert "success" in statuses
        assert "failed" in statuses

    def test_flush_partial_failure_human_output(self, db_path: Path, mock_client: MagicMock) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice("INV-001"))
        q.enqueue(_invoice("INV-002"))
        mock_client.submit_sale.side_effect = [
            {"resultCd": "000"},
            RuntimeError("timeout"),
        ]
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        assert "failed" in result.output.lower() or "retry" in result.output.lower()

    def test_flush_duplicate_invoice_counts_as_completed(
        self, db_path: Path, mock_client: MagicMock
    ) -> None:
        q = self._queue(db_path)
        q.enqueue(_invoice())
        mock_client.submit_sale.side_effect = KRADuplicateInvoiceError("dup")
        with patch(f"{_QUEUE_MOD}.data_dir", return_value=db_path.parent):
            with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
                result = runner.invoke(app, ["queue", "flush", "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        # already_processed is a success outcome — should NOT say "1 failed"
        assert "0" not in result.output.split("failed")[0].strip()[-3:] or "failed" not in result.output
