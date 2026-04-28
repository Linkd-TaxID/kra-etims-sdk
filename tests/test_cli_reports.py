"""
Tests for step 6:
  etims report x  — interim X-report (read-only, no VSCU state change)
  etims report z  — daily Z-report   (IRREVERSIBLE — closes fiscal period)

Patching notes:
- get_client is imported lazily inside command bodies → patch kra_etims.cli._client.get_client
- client.reports is a MagicMock sub-attribute; get_x_report / get_daily_z return
  real XReport / ZReport instances so that model_dump() and field access work
  in the command body without further mocking.
- _date.today() is frozen via kra_etims.cli.main._date to make date-default
  assertions deterministic.
"""

from __future__ import annotations

import json
from datetime import date as real_date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app
from kra_etims.exceptions import ZReportAlreadyIssuedError
from kra_etims.reports import TaxBreakdown, XReport, ZReport

runner = CliRunner()

_CLIENT_MOD = "kra_etims.cli._client"
_MAIN_MOD   = "kra_etims.cli.main"

_DATE = "2026-04-26"

# ---------------------------------------------------------------------------
# Canonical report fixtures
# ---------------------------------------------------------------------------

_BAND_B = TaxBreakdown(
    taxable_amount=Decimal("43103.45"),
    tax_amount=Decimal("6896.55"),
)

_X = XReport(
    report_date=_DATE,
    tin="A000123456B",
    branch_id="00",
    invoice_count=10,
    band_b=_BAND_B,
    total_taxable=Decimal("43103.45"),
    total_vat=Decimal("6896.55"),
    total_amount=Decimal("50000.00"),
)

_Z = ZReport(
    report_date=_DATE,
    tin="A000123456B",
    branch_id="00",
    invoice_count=10,
    band_b=_BAND_B,
    total_taxable=Decimal("43103.45"),
    total_vat=Decimal("6896.55"),
    total_amount=Decimal("50000.00"),
    period_number=42,
    vscu_acknowledged=True,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.reports.get_x_report.return_value = _X
    client.reports.get_daily_z.return_value = _Z
    return client


# ---------------------------------------------------------------------------
# report x
# ---------------------------------------------------------------------------

class TestReportX:
    def test_success_human_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy",
            ])
        assert result.exit_code == 0, result.output
        assert "A000123456B" in result.output
        assert "50,000" in result.output or "50000" in result.output
        mock_client.reports.get_x_report.assert_called_once_with(_DATE)

    def test_success_json_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy", "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["report_date"] == _DATE
        assert payload["tin"] == "A000123456B"
        assert payload["invoice_count"] == 10
        assert str(Decimal(str(payload["total_amount"]))) == "50000.00"

    def test_json_excludes_raw_field(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy", "--json",
            ])
        payload = json.loads(result.output)
        assert "raw" not in payload

    def test_json_contains_band_breakdown(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy", "--json",
            ])
        payload = json.loads(result.output)
        assert "band_b" in payload
        assert Decimal(str(payload["band_b"]["tax_amount"])) == Decimal("6896.55")

    def test_default_date_is_today(self, mock_client: MagicMock) -> None:
        frozen = real_date(2026, 4, 26)
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            with patch(f"{_MAIN_MOD}._date") as mock_date:
                mock_date.today.return_value = frozen
                result = runner.invoke(app, ["report", "x", "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        mock_client.reports.get_x_report.assert_called_once_with("2026-04-26")

    def test_shows_vat_amount(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy",
            ])
        assert "6,896" in result.output or "6896" in result.output

    def test_shows_invoice_count(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "x", "--date", _DATE, "--api-key", "dummy",
            ])
        assert "10" in result.output


# ---------------------------------------------------------------------------
# report z
# ---------------------------------------------------------------------------

class TestReportZ:
    def test_requires_confirmation_without_yes_flag(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            # Answer "y" to the "Are you sure?" prompt
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--api-key", "dummy",
            ], input="y\n")
        assert result.exit_code == 0, result.output
        mock_client.reports.get_daily_z.assert_called_once_with(_DATE)

    def test_confirmation_abort_exits_nonzero(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--api-key", "dummy",
            ], input="n\n")
        assert result.exit_code != 0
        mock_client.reports.get_daily_z.assert_not_called()

    def test_yes_flag_skips_confirmation(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy",
            ])
        assert result.exit_code == 0, result.output
        mock_client.reports.get_daily_z.assert_called_once_with(_DATE)

    def test_short_y_flag_skips_confirmation(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "-y", "--api-key", "dummy",
            ])
        assert result.exit_code == 0, result.output

    def test_success_human_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy",
            ])
        assert result.exit_code == 0, result.output
        assert "A000123456B" in result.output
        assert "42" in result.output          # period_number
        assert "Yes" in result.output         # vscu_acknowledged

    def test_success_json_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy", "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["report_date"] == _DATE
        assert payload["period_number"] == 42
        assert payload["vscu_acknowledged"] is True

    def test_json_excludes_raw_field(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy", "--json",
            ])
        payload = json.loads(result.output)
        assert "raw" not in payload

    def test_already_issued_exits_zero_with_warning(self, mock_client: MagicMock) -> None:
        mock_client.reports.get_daily_z.side_effect = ZReportAlreadyIssuedError(
            "already issued"
        )
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy",
            ])
        # Already-issued is NOT an error — the day was closed successfully before.
        assert result.exit_code == 0, result.output
        assert "already" in result.output.lower()

    def test_already_issued_does_not_call_api_twice(self, mock_client: MagicMock) -> None:
        mock_client.reports.get_daily_z.side_effect = ZReportAlreadyIssuedError("dup")
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy",
            ])
        mock_client.reports.get_daily_z.assert_called_once()

    def test_warning_shown_before_confirmation(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--api-key", "dummy",
            ], input="y\n")
        assert "irreversible" in result.output.lower() or "VSCU" in result.output

    def test_default_date_is_today(self, mock_client: MagicMock) -> None:
        frozen = real_date(2026, 4, 26)
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            with patch(f"{_MAIN_MOD}._date") as mock_date:
                mock_date.today.return_value = frozen
                result = runner.invoke(app, [
                    "report", "z", "--yes", "--api-key", "dummy",
                ])
        assert result.exit_code == 0, result.output
        mock_client.reports.get_daily_z.assert_called_once_with("2026-04-26")

    def test_json_contains_band_breakdown(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "report", "z", "--date", _DATE, "--yes", "--api-key", "dummy", "--json",
            ])
        payload = json.loads(result.output)
        assert "band_b" in payload
        assert Decimal(str(payload["band_b"]["tax_amount"])) == Decimal("6896.55")
