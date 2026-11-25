"""
SQLite offline invoice queue.

Invoices queued here when TIaaS is unreachable. Flushed via `etims queue flush`.

Schema:
  id           — autoincrement PK
  invoice_json — full SaleInvoice JSON
  queued_at    — ISO-8601 UTC timestamp
  status       — pending | accepted | success | failed | already_processed (historical)
                 | outcome_unknown
                 success only for SIGNED with CU invoice number, SDC id and signature;
                 PENDING_SYNC is accepted: later flushes poll get_sale_status for it
                 and never resubmit; any other or unknown status is never success.
  attempts     — number of flush attempts
  last_error   — last error message (nullable)
  purchase_id  — TaxID purchaseId of an accepted sale (nullable; added in 0.8.2 —
                 older queue files gain the column on open)

SQLite WAL mode: ACID, safe against power loss mid-write, tolerates
concurrent CLI invocations without blocking.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SQLITE_VERSION = tuple(int(x) for x in sqlite3.sqlite_version.split("."))

from kra_etims.outcomes import has_signed_evidence

from .config import data_dir

_DB_NAME = "queue.db"
_DDL = """
CREATE TABLE IF NOT EXISTS queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_json TEXT    NOT NULL,
    queued_at    TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
"""


def _classify_sale_outcome(outcome: Any) -> tuple[str, str | None]:
    """Map a returned sale body to a queue status; only evidenced SIGNED is success."""
    sale_status = outcome.get("status") if isinstance(outcome, dict) else None
    purchase = outcome.get("purchaseId") if isinstance(outcome, dict) else None
    if has_signed_evidence(outcome):
        return "success", None
    detail = f"Sale status {sale_status or 'missing'} (purchaseId={purchase})"
    if sale_status == "PENDING_SYNC":
        if not isinstance(purchase, int) or isinstance(purchase, bool):
            return "outcome_unknown", f"{detail}; no purchaseId to poll, inspect with find_sale"
        return "accepted", f"{detail}; later flushes poll get_sale_status"
    if sale_status in {"NOT_SENT", "FAILED"}:
        return "failed", detail
    # OUTCOME_UNKNOWN, RECONCILIATION_REQUIRED, SIGNED without evidence, or a status
    # this SDK does not know: retain the original identity and investigate.
    return "outcome_unknown", f"{detail}; inspect the original before any resubmission"


def _is_retryable_refusal(exc: BaseException) -> bool:
    """Same rule as flush_offline_queue's ``retryable``: nothing was processed."""
    from kra_etims.exceptions import RateLimitError, TIaaSUnavailableError
    return isinstance(exc, RateLimitError) or (
        isinstance(exc, TIaaSUnavailableError) and not getattr(exc, "requires_reconciliation", False))


class Queue:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / _DB_NAME)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        db_is_new = not self.path.exists()
        with self._connect() as conn:
            # WAL mode persists in the DB file header after the first set —
            # subsequent connections inherit it without re-issuing the PRAGMA.
            conn.execute("PRAGMA journal_mode=WAL")
            # busy_timeout collapses the concurrent-write race window that
            # triggers the WAL corruption bug in SQLite < 3.51.3.
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(_DDL)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(queue)")}
            if "purchase_id" not in columns:
                conn.execute("ALTER TABLE queue ADD COLUMN purchase_id INTEGER")
        if db_is_new and _SQLITE_VERSION < (3, 51, 3):
            from .output import err_console, warn_panel
            err_console.print(warn_panel(
                f"SQLite {sqlite3.sqlite_version} has a WAL-reset corruption bug "
                f"(fixed in 3.51.3). Concurrent writes to the offline queue carry "
                f"a small corruption risk.\n\n"
                f"Upgrade your system SQLite to 3.51.3+ or install apsw.",
                title="SQLite CVE Warning",
            ))

    def enqueue(self, invoice_json: dict) -> int:
        """Add an invoice to the queue. Returns the row id."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO queue (invoice_json, queued_at) VALUES (?, ?)",
                (json.dumps(invoice_json), now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def pending(self) -> list[sqlite3.Row]:
        """Return all rows with status='pending'."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' ORDER BY id"
            ).fetchall()

    def accepted(self) -> list[sqlite3.Row]:
        """Return rows TaxID accepted as PENDING_SYNC; flush polls these, never resubmits."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM queue WHERE status = 'accepted' ORDER BY id"
            ).fetchall()

    def status(self) -> dict[str, Any]:
        """Return counts by status and the queue file path."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM queue GROUP BY status"
            ).fetchall()
        counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
        return {
            "pending":   counts.get("pending", 0),
            "accepted":  counts.get("accepted", 0),
            "failed":    counts.get("failed", 0),
            "outcome_unknown": counts.get("outcome_unknown", 0),
            "completed": counts.get("success", 0) + counts.get("already_processed", 0),
            "total":     sum(counts.values()),
            "path":      str(self.path),
        }

    def flush_one(self, client: Any, row: sqlite3.Row) -> dict[str, Any]:
        """
        Submit a single queued invoice. Updates the row status in-place.
        Returns a result dict for reporting.
        """
        from kra_etims import SaleInvoice
        from kra_etims.exceptions import KRADuplicateInvoiceError, KRADeviceInstalledError, TIaaSAmbiguousStateError

        row_id: int = row["id"]
        try:
            data = json.loads(row["invoice_json"])
            invoice = SaleInvoice(**data)
            idem_key = f"{invoice.tin}:{invoice.bhfId}:{invoice.invcNo}"
            outcome = client.submit_sale(invoice, idempotency_key=idem_key)
            status, note = _classify_sale_outcome(outcome)
            self._update(row_id, status, note, purchase_id=outcome.get("purchaseId") if status == "accepted" else None)
            result = {"id": row_id, "invoice_no": invoice.invcNo, "status": status}
            if note:
                result["error"] = note
            return result
        except (KRADuplicateInvoiceError, KRADeviceInstalledError, TIaaSAmbiguousStateError) as exc:
            self._update(row_id, "outcome_unknown", str(exc))
            return {"id": row_id, "status": "outcome_unknown", "error": str(exc)}
        except Exception as exc:
            if getattr(exc, "requires_reconciliation", False):
                self._update(row_id, "outcome_unknown", str(exc))
                return {"id": row_id, "status": "outcome_unknown", "error": str(exc)}
            if _is_retryable_refusal(exc):
                # Refused before processing (worker refusal, 429, connect failure):
                # keep it pending so the next flush retries it under the same key.
                self._update(row_id, "pending", str(exc))
                return {"id": row_id, "status": "pending", "retryable": True, "error": str(exc)}
            self._update(row_id, "failed", str(exc))
            return {"id": row_id, "status": "failed", "error": str(exc)}

    def poll_one(self, client: Any, row: sqlite3.Row) -> dict[str, Any]:
        """
        Follow an accepted (PENDING_SYNC) row with get_sale_status. Never resubmits.
        A failed poll leaves the row accepted for the next flush, except a 404: no
        further poll can settle it, so it becomes outcome_unknown.
        """
        row_id: int = row["id"]
        purchase_id = row["purchase_id"]
        try:
            outcome = client.get_sale_status(purchase_id)
        except Exception as exc:
            if getattr(exc, "http_status", None) == 404:
                # Polling cannot settle a sale the server does not know for this branch.
                note = f"Status lookup found no sale {purchase_id} for this branch; inspect it: {exc}"
                self._update(row_id, "outcome_unknown", note)
                return {"id": row_id, "status": "outcome_unknown", "purchase_id": purchase_id, "error": note}
            self._update(row_id, "accepted", f"Status poll failed: {exc}")
            return {"id": row_id, "status": "accepted", "purchase_id": purchase_id, "error": str(exc)}
        if isinstance(outcome, dict) and outcome.get("status") == "PENDING_SYNC":
            status, note = "accepted", "Sale status PENDING_SYNC; later flushes poll get_sale_status"
        else:
            status, note = _classify_sale_outcome(outcome)
        self._update(row_id, status, note)
        result = {"id": row_id, "status": status, "purchase_id": purchase_id}
        if note:
            result["error"] = note
        return result

    def _update(self, row_id: int, status: str, error: str | None = None,
                purchase_id: int | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET status=?, attempts=attempts+1, last_error=?,"
                " purchase_id=COALESCE(?, purchase_id) WHERE id=?",
                (status, error, purchase_id, row_id),
            )
