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
repeated. The SDK method is named ``get_daily_z`` for readability, but
internally it issues a POST.

Usage (sync):
    x = client.reports.get_x_report("2026-03-11")
    print(x.band_a.taxable_amount)   # Decimal("43103.45")

    z = client.reports.get_daily_z("2026-03-11")  # mutates VSCU — call once
    print(z.vscu_acknowledged)       # True

Usage (async):
    x = await client.reports.get_x_report("2026-03-11")
    z = await client.reports.get_daily_z("2026-03-11")
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

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
    """
    report_date:   str
    report_time:   str = ""   # ISO-8601 timestamp of report generation (generatedAt)
    cu_serial:     str = ""   # VSCU serial number (not returned by current middleware)
    branch_id:     str
    tin:           str
    invoice_count: int
    band_a: TaxBreakdown = Field(default_factory=TaxBreakdown)  # 16% Standard VAT
    band_b: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  0% Zero-Rated (petroleum, exports)
    band_c: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  8% Special Rate
    band_d: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  0% Exempt (basic foodstuffs)
    band_e: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  8% Non-VAT scope levy
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
    The underlying endpoint is POST (not GET) because it mutates VSCU state.
    Call once per day after close of trade; a second call for the same date
    returns HTTP 409 Conflict.
    """
    report_date:       str
    report_time:       str = ""   # ISO-8601 timestamp of report generation
    cu_serial:         str = ""   # VSCU serial number (not returned by current middleware)
    branch_id:         str
    tin:               str
    invoice_count:     int
    band_a: TaxBreakdown = Field(default_factory=TaxBreakdown)  # 16% Standard VAT
    band_b: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  0% Zero-Rated
    band_c: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  8% Special Rate
    band_d: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  0% Exempt
    band_e: TaxBreakdown = Field(default_factory=TaxBreakdown)  #  8% Non-VAT scope levy
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
        period-close sequence. A second call for the same date raises
        ``KRAeTIMSError`` (middleware returns HTTP 409 Conflict).

        Call once per business day, after the last transaction of the day.
        """
        # Z Report is POST because it mutates VSCU state (period close).
        raw = self._client._request("POST", f"/v2/reports/daily-z?date={date}")
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

        Issues a POST — triggers VSCU period close. Call once per day.
        """
        raw = await self._client._request("POST", f"/v2/reports/daily-z?date={date}")
        return ZReport.from_api(raw)
