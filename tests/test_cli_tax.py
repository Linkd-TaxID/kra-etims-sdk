"""
Tests for step 5:
  etims tax calculate  — offline KRA-compliant tax splits (no auth needed)
  etims tax bands      — static band reference table (no auth needed)

Math reference — VSCU/OSCU Specification v2.0 §4.1:
  A  0%   Exempt
  B  16%  Standard VAT  ← B is the 16% band, NOT A
  C  0%   Zero-Rated
  D  0%   Non-VAT
  E  8%   Special Rate
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()


# ===========================================================================
# tax calculate
# ===========================================================================

class TestTaxCalculate:

    # -----------------------------------------------------------------------
    # Required-argument guard
    # -----------------------------------------------------------------------

    def test_missing_price_errors(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--band", "B"])
        assert result.exit_code != 0

    def test_missing_band_errors(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--price", "1160"])
        assert result.exit_code != 0

    # -----------------------------------------------------------------------
    # Band A — 0% Exempt (inclusive)
    # -----------------------------------------------------------------------

    def test_band_a_zero_tax(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--price", "1000", "--band", "A"])
        assert result.exit_code == 0, result.output
        assert "0% Exempt" in result.output or "Exempt" in result.output

    def test_band_a_json_zero_tax_amt(self) -> None:
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1000", "--band", "A", "--json",
        ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert Decimal(payload["taxAmt"]) == Decimal("0.00")
        assert Decimal(payload["taxblAmt"]) == Decimal("1000.00")
        assert Decimal(payload["totAmt"]) == Decimal("1000.00")

    # -----------------------------------------------------------------------
    # Band B — 16% Standard VAT (inclusive)
    # -----------------------------------------------------------------------

    def test_band_b_human_output(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--price", "5800", "--band", "B"])
        assert result.exit_code == 0, result.output
        assert "16%" in result.output or "Standard VAT" in result.output
        assert "5,800" in result.output or "5800" in result.output

    def test_band_b_json_math(self) -> None:
        # 5800 inclusive at 16%: taxblAmt = 5800/1.16 = 5000.00, taxAmt = 800.00
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "5800", "--band", "B", "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["totAmt"]) == Decimal("5800.00")
        assert Decimal(p["taxblAmt"]) == Decimal("5000.00")
        assert Decimal(p["taxAmt"]) == Decimal("800.00")

    def test_band_b_json_totals_balance(self) -> None:
        # taxblAmt + taxAmt must equal totAmt exactly
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1160", "--band", "B", "--json",
        ])
        p = json.loads(result.output)
        assert Decimal(p["taxblAmt"]) + Decimal(p["taxAmt"]) == Decimal(p["totAmt"])

    # -----------------------------------------------------------------------
    # Band E — 8% Special Rate (inclusive)
    # -----------------------------------------------------------------------

    def test_band_e_json_math(self) -> None:
        # 1080 inclusive at 8%: taxblAmt = 1080/1.08 = 1000.00, taxAmt = 80.00
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1080", "--band", "E", "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["totAmt"]) == Decimal("1080.00")
        assert Decimal(p["taxblAmt"]) == Decimal("1000.00")
        assert Decimal(p["taxAmt"]) == Decimal("80.00")

    # -----------------------------------------------------------------------
    # Exclusive pricing (--exclusive flag)
    # -----------------------------------------------------------------------

    def test_exclusive_band_b_json(self) -> None:
        # Net price 1000, Band B exclusive: taxAmt = 1000 * 0.16 = 160, totAmt = 1160
        result = runner.invoke(app, [
            "tax", "calculate",
            "--price", "1000",
            "--band", "B",
            "--exclusive",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["taxblAmt"]) == Decimal("1000.00")
        assert Decimal(p["taxAmt"]) == Decimal("160.00")
        assert Decimal(p["totAmt"]) == Decimal("1160.00")

    def test_exclusive_flag_shown_in_human_output(self) -> None:
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1000", "--band", "B", "--exclusive",
        ])
        assert result.exit_code == 0, result.output
        assert "exclusive" in result.output.lower()

    def test_inclusive_label_shown_in_human_output(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--price", "1160", "--band", "B"])
        assert result.exit_code == 0, result.output
        assert "inclusive" in result.output.lower()

    # -----------------------------------------------------------------------
    # Quantity
    # -----------------------------------------------------------------------

    def test_qty_scales_totals(self) -> None:
        # Band A, price=500, qty=3 → totAmt=1500, taxblAmt=1500, taxAmt=0
        result = runner.invoke(app, [
            "tax", "calculate",
            "--price", "500",
            "--band", "A",
            "--qty", "3",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["totAmt"]) == Decimal("1500.00")
        assert Decimal(p["taxblAmt"]) == Decimal("1500.00")
        assert Decimal(p["taxAmt"]) == Decimal("0.00")
        assert Decimal(p["qty"]) == Decimal("3.0000")

    def test_fractional_qty(self) -> None:
        # Band A, price=1000, qty=0.5 → totAmt=500
        result = runner.invoke(app, [
            "tax", "calculate",
            "--price", "1000",
            "--band", "A",
            "--qty", "0.5",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["totAmt"]) == Decimal("500.00")

    # -----------------------------------------------------------------------
    # Optional metadata (--name, --code)
    # -----------------------------------------------------------------------

    def test_custom_name_in_output(self) -> None:
        result = runner.invoke(app, [
            "tax", "calculate",
            "--price", "1000",
            "--band", "A",
            "--name", "Maize Flour",
        ])
        assert result.exit_code == 0, result.output
        assert "Maize Flour" in result.output

    def test_custom_code_in_json(self) -> None:
        result = runner.invoke(app, [
            "tax", "calculate",
            "--price", "1000",
            "--band", "A",
            "--code", "HS110100",
            "--json",
        ])
        p = json.loads(result.output)
        assert p["itemCd"] == "HS110100"

    # -----------------------------------------------------------------------
    # JSON output structure
    # -----------------------------------------------------------------------

    def test_json_contains_required_fields(self) -> None:
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1000", "--band", "B", "--json",
        ])
        p = json.loads(result.output)
        for field in ("itemCd", "itemNm", "qty", "uprc", "totAmt", "taxTyCd", "taxblAmt", "taxAmt"):
            assert field in p, f"Missing field: {field}"

    def test_json_tax_type_code_matches_band(self) -> None:
        for band in ("A", "B", "C", "D", "E"):
            result = runner.invoke(app, [
                "tax", "calculate", "--price", "1000", "--band", band, "--json",
            ])
            p = json.loads(result.output)
            assert p["taxTyCd"] == band

    # -----------------------------------------------------------------------
    # Error handling
    # -----------------------------------------------------------------------

    def test_invalid_band_exits_1(self) -> None:
        result = runner.invoke(app, ["tax", "calculate", "--price", "1000", "--band", "Z"])
        assert result.exit_code == 1
        assert "band" in result.output.lower() or "unknown" in result.output.lower()

    def test_lowercase_band_accepted(self) -> None:
        # calculate_item uppercases the band internally
        result = runner.invoke(app, ["tax", "calculate", "--price", "1000", "--band", "b", "--json"])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert p["taxTyCd"] == "B"

    @pytest.mark.parametrize("band", ["C", "D"])
    def test_zero_rate_bands_c_and_d(self, band: str) -> None:
        result = runner.invoke(app, [
            "tax", "calculate", "--price", "1000", "--band", band, "--json",
        ])
        assert result.exit_code == 0, result.output
        p = json.loads(result.output)
        assert Decimal(p["taxAmt"]) == Decimal("0.00")
        assert Decimal(p["totAmt"]) == Decimal("1000.00")


# ===========================================================================
# tax bands
# ===========================================================================

class TestTaxBands:

    def test_exits_zero(self) -> None:
        result = runner.invoke(app, ["tax", "bands"])
        assert result.exit_code == 0, result.output

    def test_shows_all_five_bands(self) -> None:
        result = runner.invoke(app, ["tax", "bands"])
        for band in ("A", "B", "C", "D", "E"):
            assert band in result.output

    def test_b_is_16_percent(self) -> None:
        # The spec note "B is NOT 16% = wrong. B IS 16%" must be surfaced clearly.
        result = runner.invoke(app, ["tax", "bands"])
        assert "16%" in result.output

    def test_correction_note_present(self) -> None:
        # CLAUDE.md mandates this note — guards against the A=16% inversion bug.
        result = runner.invoke(app, ["tax", "bands"])
        assert "A is NOT" in result.output or "NOT" in result.output

    def test_json_flag_exits_zero(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        assert result.exit_code == 0, result.output

    def test_json_returns_list_of_five(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert len(payload) == 5

    def test_json_band_keys_present(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        payload = json.loads(result.output)
        for entry in payload:
            assert "band" in entry
            assert "rate" in entry
            assert "description" in entry

    def test_json_band_codes_are_a_through_e(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        bands = {entry["band"] for entry in json.loads(result.output)}
        assert bands == {"A", "B", "C", "D", "E"}

    def test_json_b_rate_is_16_percent(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        payload = json.loads(result.output)
        band_b = next(e for e in payload if e["band"] == "B")
        assert band_b["rate"] == "16%"

    def test_json_a_rate_is_zero_percent(self) -> None:
        # Explicit guard: A must NOT be 16%
        result = runner.invoke(app, ["tax", "bands", "--json"])
        payload = json.loads(result.output)
        band_a = next(e for e in payload if e["band"] == "A")
        assert band_a["rate"] == "0%"

    def test_json_e_rate_is_8_percent(self) -> None:
        result = runner.invoke(app, ["tax", "bands", "--json"])
        payload = json.loads(result.output)
        band_e = next(e for e in payload if e["band"] == "E")
        assert band_e["rate"] == "8%"
