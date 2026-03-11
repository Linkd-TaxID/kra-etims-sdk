"""
KRA eTIMS SDK — Reporting Interface
=====================================
Exposes X (interim) and Z (daily end-of-day) reporting endpoints as strictly
typed Pydantic models so an accountant's ERP system can consume them without
parsing raw JSON.

Usage (sync):
    report = client.reports.get_daily_z("2026-03-11")
    print(report.total_vat_a)   # Decimal("12450.00")

Usage (async):
    report = await client.reports.get_daily_z("2026-03-11")
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
    """Per-band taxable and VAT amounts."""
    taxable_amount: Decimal = Field(Decimal("0"), description="Net taxable amount")
    tax_amount:     Decimal = Field(Decimal("0"), description="VAT amount")


class XReport(BaseModel):
    """
    X-Report — interim (mid-day) totals.
    Does NOT reset the VSCU counters.  Safe to pull at any time.
    """
    report_date:   str
    report_time:   str
    cu_serial:     str
    branch_id:     str
    tin:           str
    invoice_count: int
    band_a:        TaxBreakdown = Field(default_factory=TaxBreakdown)  # 16% VAT
    band_b:        TaxBreakdown = Field(default_factory=TaxBreakdown)  # 8%  VAT
    band_c:        TaxBreakdown = Field(default_factory=TaxBreakdown)  # Exempt
    band_d:        TaxBreakdown = Field(default_factory=TaxBreakdown)  # Zero-rated
    band_e:        TaxBreakdown = Field(default_factory=TaxBreakdown)  # Non-VAT
    total_taxable: Decimal
    total_vat:     Decimal
    total_amount:  Decimal
    raw:           Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "XReport":
        """Construct from raw middleware JSON."""
        payload = data.get("data", data)
        return cls(
            report_date=payload.get("reportDate", ""),
            report_time=payload.get("reportTime", ""),
            cu_serial=payload.get("cuSn", payload.get("cu_serial", "")),
            branch_id=payload.get("bhfId", ""),
            tin=payload.get("tin", ""),
            invoice_count=int(payload.get("invoiceCount", 0)),
            band_a=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtA", "0"))),
                tax_amount=Decimal(str(payload.get("taxAmtA", "0"))),
            ),
            band_b=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtB", "0"))),
                tax_amount=Decimal(str(payload.get("taxAmtB", "0"))),
            ),
            band_c=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtC", "0"))),
                tax_amount=Decimal("0"),
            ),
            band_d=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtD", "0"))),
                tax_amount=Decimal("0"),
            ),
            band_e=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtE", "0"))),
                tax_amount=Decimal("0"),
            ),
            total_taxable=Decimal(str(payload.get("totTaxblAmt", "0"))),
            total_vat=Decimal(str(payload.get("totTaxAmt", "0"))),
            total_amount=Decimal(str(payload.get("totAmt", "0"))),
            raw=payload,
        )


class ZReport(BaseModel):
    """
    Z-Report — daily end-of-day totals.
    Resets the VSCU period counters after generation.
    ERP systems should poll this once per business day after close of trade.
    """
    report_date:   str
    report_time:   str
    cu_serial:     str
    branch_id:     str
    tin:           str
    invoice_count: int
    band_a:        TaxBreakdown = Field(default_factory=TaxBreakdown)
    band_b:        TaxBreakdown = Field(default_factory=TaxBreakdown)
    band_c:        TaxBreakdown = Field(default_factory=TaxBreakdown)
    band_d:        TaxBreakdown = Field(default_factory=TaxBreakdown)
    band_e:        TaxBreakdown = Field(default_factory=TaxBreakdown)
    total_taxable: Decimal
    total_vat:     Decimal
    total_amount:  Decimal
    period_number: Optional[int] = None   # VSCU Z-counter (increments per close)
    raw:           Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "ZReport":
        payload = data.get("data", data)
        return cls(
            report_date=payload.get("reportDate", ""),
            report_time=payload.get("reportTime", ""),
            cu_serial=payload.get("cuSn", payload.get("cu_serial", "")),
            branch_id=payload.get("bhfId", ""),
            tin=payload.get("tin", ""),
            invoice_count=int(payload.get("invoiceCount", 0)),
            band_a=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtA", "0"))),
                tax_amount=Decimal(str(payload.get("taxAmtA", "0"))),
            ),
            band_b=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtB", "0"))),
                tax_amount=Decimal(str(payload.get("taxAmtB", "0"))),
            ),
            band_c=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtC", "0"))),
                tax_amount=Decimal("0"),
            ),
            band_d=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtD", "0"))),
                tax_amount=Decimal("0"),
            ),
            band_e=TaxBreakdown(
                taxable_amount=Decimal(str(payload.get("taxblAmtE", "0"))),
                tax_amount=Decimal("0"),
            ),
            total_taxable=Decimal(str(payload.get("totTaxblAmt", "0"))),
            total_vat=Decimal(str(payload.get("totTaxAmt", "0"))),
            total_amount=Decimal(str(payload.get("totAmt", "0"))),
            period_number=payload.get("zReportNo") or payload.get("periodNo"),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Sync Interface (attached to KRAeTIMSClient)
# ---------------------------------------------------------------------------

class ReportsInterface:
    """
    Reporting interface attached to ``KRAeTIMSClient.reports``.

    client.reports.get_x_report("2026-03-11")
    client.reports.get_daily_z("2026-03-11")
    """

    def __init__(self, client: "KRAeTIMSClient") -> None:
        self._client = client

    def get_x_report(self, date: str) -> XReport:
        """
        Fetch an interim X-report for the given date (YYYY-MM-DD).
        Safe to call at any point during the business day.
        """
        raw = self._client._request("GET", f"/v2/reports/x/{date}")
        return XReport.from_api(raw)

    def get_daily_z(self, date: str) -> ZReport:
        """
        Fetch (and close) the daily Z-report for the given date (YYYY-MM-DD).
        Triggers the VSCU period-close sequence on the middleware.
        Call once per day after close of trade.
        """
        raw = self._client._request("GET", f"/v2/reports/z/{date}")
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
        raw = await self._client._request("GET", f"/v2/reports/x/{date}")
        return XReport.from_api(raw)

    async def get_daily_z(self, date: str) -> ZReport:
        raw = await self._client._request("GET", f"/v2/reports/z/{date}")
        return ZReport.from_api(raw)
