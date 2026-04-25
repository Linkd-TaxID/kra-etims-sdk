"""
KRA eTIMS SDK — Reporting Interface
=====================================
Exposes X (interim) and Z (daily end-of-day) reporting endpoints as strictly
typed Pydantic models so an accountant's ERP system can consume them without
parsing raw JSON.

Middleware endpoints (ground truth):
  GET  /v2/reports/daily-x?date=YYYY-MM-DD   — X Report (read-only)
  POST /v2/reports/daily-z?date=YYYY-MM-DD   — Z Report (triggers VSCU period close)

The Z Report is deliberately POST because it mutates VSCU state. Calling it
twice for the same date returns HTTP 409 Conflict — the period-close is not
repeated. The SDK raises ``ZReportAlreadyIssuedError`` on 409, which callers
can catch to handle the already-submitted case cleanly.

Usage (sync):
    x = client.reports.get_x_report("2026-03-11")
    print(x.band_b.taxable_amount)   # Decimal("43103.45")  # Band B = Standard VAT 16%

    z = client.reports.get_daily_z("2026-03-11")  # mutates VSCU — call once
    print(z.vscu_acknowledged)       # True

Usage (async):
    x = await client.reports.get_x_report("2026-03-11")
    z = await client.reports.get_daily_z("2026-03-11")

KRA eTIMS Tax Band Mapping — KRA TIS for OSCU/VSCU v2.0 §4.1 (★ RESEARCH-VALIDATED):
    A = Exempt (0%)          — exempt supplies; no input credit allowed
    B = Standard VAT (16%)   — standard-rated goods and services ← B is the 16% band
    C = Zero-Rated (0%)      — exports, certain zero-rated supplies; input credit allowed
    D = Non-VAT (0%)         — supplies outside the VAT Act entirely
    E = Special Rate (8%)    — petroleum products, LPG per Kenya VAT Act
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from .exceptions import CreditNoteConflictError, ZReportAlreadyIssuedError

if TYPE_CHECKING:
    from .client import KRAeTIMSClient
    from .async_client import AsyncKRAeTIMSClient


# ---------------------------------------------------------------------------
# Strictly-Typed Report Models
# ---------------------------------------------------------------------------

class TaxBreakdown(BaseModel):
    """Per-band taxable (net) and VAT amounts for a single KRA tax band."""
    taxable_amount: Decimal = Field(Decimal("0"), description="Net taxable amount (excl. VAT)")
    tax_amount:     Decimal = Field(Decimal("0"), description="VAT / levy amount")


class XReport(BaseModel):
    """
    X-Report — interim (mid-day) totals.

    Safe to pull at any time during the business day.
    Does NOT reset VSCU counters or close the fiscal period.

    KRA eTIMS Tax Band Mapping (★ RESEARCH-VALIDATED — KRA TIS v2.0 §4.1):
        band_a = Exempt (0%)          band_b = Standard VAT (16%)
        band_c = Zero-Rated (0%)      band_d = Non-VAT (0%)
        band_e = Special Rate (8%)
    """
    report_date:   str
    report_time:   str = ""   # ISO-8601 timestamp of report generation (generatedAt)
    cu_serial:     str = ""   # VSCU serial number (not returned by current middleware)
    branch_id:     str
    tin:           str
    invoice_count: int
    band_a: TaxBreakdown = Field(default_factory=TaxBreakdown)  # A = Exempt (0%)
    band_b: TaxBreakdown = Field(default_factory=TaxBreakdown)  # B = Standard VAT (16%)
    band_c: TaxBreakdown = Field(default_factory=TaxBreakdown)  # C = Zero-Rated (0%)
    band_d: TaxBreakdown = Field(default_factory=TaxBreakdown)  # D = Non-VAT (0%)
    band_e: TaxBreakdown = Field(default_factory=TaxBreakdown)  # E = Special Rate (8%)
    total_taxable: Decimal
    total_vat:     Decimal
    total_amount:  Decimal
    raw:           Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "XReport":
        """
        Construct from the middleware JSON response.

        Actual middleware response shape (DailyReportService.generateXReport):
          {
            "reportType":  "X",
            "reportDate":  "2026-03-20",
            "tin":         "A000123456B",
            "branchId":    "00",
            "generatedAt": "2026-03-20T10:00:00Z",
            "summary": {
              "totalCount":     10,
              "totalAmount":    100000.00,
              "totalTaxAmount": 12000.00,
              "currency":       "KES",
              "taxBands": {
                "A": { "count": 5, "taxableAmount": 43103.45,
                       "taxAmount": 6896.55, "totalAmount": 50000.00 },
                ...
              }
            },
            "vscuSyncRequired": false
          }
        """
        payload = data.get("data", data)
        summary = payload.get("summary", {})
        bands   = summary.get("taxBands", {})

        def _band(code: str) -> TaxBreakdown:
            b = bands.get(code, {})
            return TaxBreakdown(
                taxable_amount=Decimal(str(b.get("taxableAmount", "0"))),
                tax_amount=Decimal(str(b.get("taxAmount", "0"))),
            )

        total_amt = Decimal(str(summary.get("totalAmount", "0")))
        total_tax = Decimal(str(summary.get("totalTaxAmount", "0")))

        return cls(
            report_date=payload.get("reportDate", ""),
            report_time=payload.get("generatedAt", ""),
            cu_serial=payload.get("cuSn", ""),
            branch_id=payload.get("branchId", payload.get("bhfId", "")),
            tin=payload.get("tin", ""),
            invoice_count=int(summary.get("totalCount", 0)),
            band_a=_band("A"),
            band_b=_band("B"),
            band_c=_band("C"),
            band_d=_band("D"),
            band_e=_band("E"),
            # totalTaxable is not a top-level field in the middleware response;
            # derived as totalAmount − totalTaxAmount (algebraically exact because
            # ∑taxableAmt + ∑taxAmt = ∑totalAmt across all transactions).
            total_taxable=total_amt - total_tax,
            total_vat=total_tax,
            total_amount=total_amt,
            raw=payload,
        )


class ZReport(BaseModel):
    """
    Z-Report — daily end-of-day totals.

    Triggers the mandatory VSCU period-close sequence on the middleware.
    The underlying endpoint is POST (not GET) because it mutates VSCU state
    (KRA TIS v2.0 §6.5 — ★ RESEARCH-VALIDATED: Z report covers full day
    00:00:00–23:59:59).

    Call once per day after close of trade; a second call for the same date
    raises ``ZReportAlreadyIssuedError`` (middleware returns HTTP 409 Conflict).

    KRA eTIMS Tax Band Mapping (★ RESEARCH-VALIDATED — KRA TIS v2.0 §4.1):
        band_a = Exempt (0%)          band_b = Standard VAT (16%)
        band_c = Zero-Rated (0%)      band_d = Non-VAT (0%)
        band_e = Special Rate (8%)
    """
    report_date:       str
    report_time:       str = ""   # ISO-8601 timestamp of report generation
    cu_serial:         str = ""   # VSCU serial number (not returned by current middleware)
    branch_id:         str
    tin:               str
    invoice_count:     int
    band_a: TaxBreakdown = Field(default_factory=TaxBreakdown)  # A = Exempt (0%)
    band_b: TaxBreakdown = Field(default_factory=TaxBreakdown)  # B = Standard VAT (16%)
    band_c: TaxBreakdown = Field(default_factory=TaxBreakdown)  # C = Zero-Rated (0%)
    band_d: TaxBreakdown = Field(default_factory=TaxBreakdown)  # D = Non-VAT (0%)
    band_e: TaxBreakdown = Field(default_factory=TaxBreakdown)  # E = Special Rate (8%)
    total_taxable:     Decimal
    total_vat:         Decimal
    total_amount:      Decimal
    period_number:     Optional[int]  = None   # VSCU Z-counter (increments each daily close)
    vscu_acknowledged: bool           = False  # True when VSCU day-reset completed
    raw:               Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "ZReport":
        payload = data.get("data", data)
        summary = payload.get("summary", {})
        bands   = summary.get("taxBands", {})

        def _band(code: str) -> TaxBreakdown:
            b = bands.get(code, {})
            return TaxBreakdown(
                taxable_amount=Decimal(str(b.get("taxableAmount", "0"))),
                tax_amount=Decimal(str(b.get("taxAmount", "0"))),
            )

        total_amt = Decimal(str(summary.get("totalAmount", "0")))
        total_tax = Decimal(str(summary.get("totalTaxAmount", "0")))

        return cls(
            report_date=payload.get("reportDate", ""),
            report_time=payload.get("generatedAt", ""),
            cu_serial=payload.get("cuSn", ""),
            branch_id=payload.get("branchId", payload.get("bhfId", "")),
            tin=payload.get("tin", ""),
            invoice_count=int(summary.get("totalCount", 0)),
            band_a=_band("A"),
            band_b=_band("B"),
            band_c=_band("C"),
            band_d=_band("D"),
            band_e=_band("E"),
            total_taxable=total_amt - total_tax,
            total_vat=total_tax,
            total_amount=total_amt,
            period_number=payload.get("zReportNo") or payload.get("periodNo"),
            vscu_acknowledged=bool(payload.get("vscuAcknowledged", False)),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Sync Interface (attached to KRAeTIMSClient)
# ---------------------------------------------------------------------------

class ReportsInterface:
    """
    Reporting interface attached to ``KRAeTIMSClient.reports``.

    client.reports.get_x_report("2026-03-11")  → GET  /v2/reports/daily-x?date=
    client.reports.get_daily_z("2026-03-11")   → POST /v2/reports/daily-z?date=
    """

    def __init__(self, client: "KRAeTIMSClient") -> None:
        self._client = client

    def get_x_report(self, date: str) -> XReport:
        """
        Fetch an interim X-report for the given date (YYYY-MM-DD).

        Safe to call at any point during the business day — does not
        reset VSCU counters or close the fiscal period.
        """
        raw = self._client._request("GET", f"/v2/reports/daily-x?date={date}")
        return XReport.from_api(raw)

    def get_daily_z(self, date: str) -> ZReport:
        """
        Close the fiscal day and fetch the Z-report for the given date (YYYY-MM-DD).

        Issues a POST to the middleware, triggering the mandatory VSCU
        period-close sequence (KRA TIS v2.0 §6.5 — full day 00:00:00–23:59:59).

        A second call for the same date raises ``ZReportAlreadyIssuedError``
        (middleware returns HTTP 409 Conflict). The VSCU day-reset is irreversible
        (KRA TIS v2.0 §21.6.1) — do NOT retry on this exception.

        Call once per business day, after the last transaction of the day.
        """
        try:
            raw = self._client._request("POST", f"/v2/reports/daily-z?date={date}")
        except CreditNoteConflictError as exc:
            # The base client maps ALL HTTP 409 responses to CreditNoteConflictError.
            # For Z-reports, 409 means the report was already issued — re-raise as the
            # correct, semantically precise exception type.
            raise ZReportAlreadyIssuedError(
                f"Z-Report already issued for date={date}: {exc}"
            ) from exc
        return ZReport.from_api(raw)


# ---------------------------------------------------------------------------
# Async Interface (attached to AsyncKRAeTIMSClient)
# ---------------------------------------------------------------------------

class AsyncReportsInterface:
    """
    Async reporting interface attached to ``AsyncKRAeTIMSClient.reports``.

    await client.reports.get_x_report("2026-03-11")
    await client.reports.get_daily_z("2026-03-11")
    """

    def __init__(self, client: "AsyncKRAeTIMSClient") -> None:
        self._client = client

    async def get_x_report(self, date: str) -> XReport:
        """Fetch an interim X-report (YYYY-MM-DD). Does not reset VSCU counters."""
        raw = await self._client._request("GET", f"/v2/reports/daily-x?date={date}")
        return XReport.from_api(raw)

    async def get_daily_z(self, date: str) -> ZReport:
        """
        Close the fiscal day and fetch the Z-report (YYYY-MM-DD).

        Issues a POST — triggers VSCU period close (KRA TIS v2.0 §6.5). Call once per day.
        Raises ``ZReportAlreadyIssuedError`` on HTTP 409 — the report was already submitted.
        """
        try:
            raw = await self._client._request("POST", f"/v2/reports/daily-z?date={date}")
        except CreditNoteConflictError as exc:
            raise ZReportAlreadyIssuedError(
                f"Z-Report already issued for date={date}: {exc}"
            ) from exc
        return ZReport.from_api(raw)
