"""
SQLite offline invoice queue.

Invoices queued here when TIaaS is unreachable. Flushed via `etims queue flush`.

Schema:
  id           — autoincrement PK
  invoice_json — full SaleInvoice JSON
  queued_at    — ISO-8601 UTC timestamp
  status       — pending | success | failed | already_processed
  attempts     — number of flush attempts
  last_error   — last error message (nullable)

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

    def status(self) -> dict[str, Any]:
        """Return counts by status and the queue file path."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM queue GROUP BY status"
            ).fetchall()
        counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
        return {
            "pending":   counts.get("pending", 0),
            "failed":    counts.get("failed", 0),
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
        from kra_etims.exceptions import KRADuplicateInvoiceError

        row_id: int = row["id"]
        try:
            data = json.loads(row["invoice_json"])
            invoice = SaleInvoice(**data)
            idem_key = f"{invoice.tin}:{invoice.bhfId}:{invoice.invcNo}"
            client.submit_sale(invoice, idempotency_key=idem_key)
            self._update(row_id, "success")
            return {"id": row_id, "invoice_no": invoice.invcNo, "status": "success"}
        except KRADuplicateInvoiceError:
            self._update(row_id, "already_processed")
            return {"id": row_id, "status": "already_processed"}
        except Exception as exc:
            self._update(row_id, "failed", str(exc))
            return {"id": row_id, "status": "failed", "error": str(exc)}

    def _update(self, row_id: int, status: str, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET status=?, attempts=attempts+1, last_error=? WHERE id=?",
                (status, error, row_id),
            )
