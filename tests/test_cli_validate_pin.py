"""
Tests for step 4:
  etims invoice validate  — offline Pydantic validation, no API key needed
  etims pin check         — local KRA PIN format check, no API key needed

Both commands are purely local. No mocking of get_client required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Invoice fixtures
# ---------------------------------------------------------------------------

# Band A (Exempt, 0%): totAmt = qty * uprc; taxblAmt + taxAmt = totAmt
_ITEM_BAND_A = {
    "itemCd": "ITEM001",
    "itemNm": "Test Item",
    "qty": "1",
    "uprc": "1000.00",
    "totAmt": "1000.00",
    "taxTyCd": "A",
    "taxblAmt": "1000.00",
    "taxAmt": "0.00",
}

# Band B (Standard VAT 16%, VAT-inclusive price 1160):
#   taxblAmt = 1160 / 1.16 = 1000.00; taxAmt = 160.00; totAmt = 1160.00
_ITEM_BAND_B = {
    "itemCd": "ITEM002",
    "itemNm": "VAT Item",
    "qty": "1",
    "uprc": "1160.00",
    "totAmt": "1160.00",
    "taxTyCd": "B",
    "taxblAmt": "1000.00",
    "taxAmt": "160.00",
}

_INVOICE_BASE = {
    "tin": "A000123456B",
    "bhfId": "00",
    "invcNo": "INV-VALIDATE-001",
    "confirmDt": "20260426142211",
    "totItemCnt": 1,
}

def _make_invoice(item: dict, **overrides) -> dict:
    return {
        **_INVOICE_BASE,
        "totTaxblAmt": item["taxblAmt"],
        "totTaxAmt": item["taxAmt"],
        "totAmt": item["totAmt"],
        "itemList": [item],
        **overrides,
    }


def _write_invoice(tmp_path: Path, data: dict, name: str = "invoice.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# invoice validate — happy paths
# ---------------------------------------------------------------------------

class TestInvoiceValidate:
    def test_valid_band_a_invoice(self, tmp_path: Path) -> None:
        f = _write_invoice(tmp_path, _make_invoice(_ITEM_BAND_A))
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output.lower()
        assert "INV-VALIDATE-001" in result.output

    def test_valid_band_b_invoice(self, tmp_path: Path) -> None:
        f = _write_invoice(tmp_path, _make_invoice(_ITEM_BAND_B))
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output.lower()

    def test_valid_multi_item_invoice(self, tmp_path: Path) -> None:
        item_a = _ITEM_BAND_A
        item_b = _ITEM_BAND_B
        invoice = {
            **_INVOICE_BASE,
            "totItemCnt": 2,
            "totTaxblAmt": "2000.00",  # 1000 + 1000
            "totTaxAmt": "160.00",
            "totAmt": "2160.00",       # 1000 + 1160
            "itemList": [item_a, item_b],
        }
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 0, result.output

    def test_json_flag_success(self, tmp_path: Path) -> None:
        f = _write_invoice(tmp_path, _make_invoice(_ITEM_BAND_A))
        result = runner.invoke(app, ["invoice", "validate", str(f), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["valid"] is True
        assert payload["invoice_no"] == "INV-VALIDATE-001"
        assert payload["items"] == 1
        assert payload["total_kes"] == "1000.00"
        assert payload["total_vat_kes"] == "0.00"

    def test_json_flag_shows_item_count(self, tmp_path: Path) -> None:
        item_a = _ITEM_BAND_A
        item_b = _ITEM_BAND_B
        invoice = {
            **_INVOICE_BASE,
            "totItemCnt": 2,
            "totTaxblAmt": "2000.00",
            "totTaxAmt": "160.00",
            "totAmt": "2160.00",
            "itemList": [item_a, item_b],
        }
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f), "--json"])
        payload = json.loads(result.output)
        assert payload["items"] == 2

    # --- error paths ---

    def test_file_not_found_exits_1(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["invoice", "validate", str(tmp_path / "ghost.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_malformed_json_exits_1(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        result = runner.invoke(app, ["invoice", "validate", str(bad)])
        assert result.exit_code == 1
        assert "json" in result.output.lower()

    def test_missing_required_field_exits_1(self, tmp_path: Path) -> None:
        # No itemList
        f = _write_invoice(tmp_path, {"tin": "A000123456B", "bhfId": "00"})
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 1
        assert "validation" in result.output.lower() or "failed" in result.output.lower()

    def test_math_error_totamt_mismatch_exits_1(self, tmp_path: Path) -> None:
        # totAmt on invoice does not match sum of items
        bad_item = {**_ITEM_BAND_A, "totAmt": "1000.00"}
        invoice = _make_invoice(bad_item, totAmt="9999.00")
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 1

    def test_item_math_error_qty_uprc_exits_1(self, tmp_path: Path) -> None:
        # totAmt on item ≠ qty * uprc
        bad_item = {**_ITEM_BAND_A, "totAmt": "1500.00", "taxblAmt": "1500.00"}
        invoice = _make_invoice(bad_item, totTaxblAmt="1500.00", totAmt="1500.00")
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 1

    def test_json_flag_on_failure_emits_valid_false(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"tin": "A000123456B"}), encoding="utf-8")
        result = runner.invoke(app, ["invoice", "validate", str(bad), "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["valid"] is False
        assert "error" in payload

    def test_invalid_cust_pin_exits_1(self, tmp_path: Path) -> None:
        # custPin must match KRA_TIN_PATTERN if provided
        invoice = _make_invoice(_ITEM_BAND_A, custPin="NOT_A_PIN")
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 1

    def test_valid_b2b_invoice_with_cust_pin(self, tmp_path: Path) -> None:
        invoice = _make_invoice(_ITEM_BAND_A, custPin="B000999888C", custNm="Acme Ltd")
        f = _write_invoice(tmp_path, invoice)
        result = runner.invoke(app, ["invoice", "validate", str(f)])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# pin check — happy paths
# ---------------------------------------------------------------------------

class TestPinCheck:
    # Valid PINs: 1 uppercase letter + 9 digits + 1 uppercase letter
    @pytest.mark.parametrize("pin", [
        "A000123456B",
        "P123456789Z",
        "Z000000000A",
    ])
    def test_valid_pins(self, pin: str) -> None:
        result = runner.invoke(app, ["pin", "check", pin])
        assert result.exit_code == 0, result.output
        assert pin in result.output

    # Invalid PINs
    @pytest.mark.parametrize("pin", [
        "INVALID",
        "a000123456B",   # lowercase first letter
        "A00012345B",    # only 8 digits
        "A0001234567B",  # 10 digits
        "A000123456b",   # lowercase last letter
        "1000123456B",   # digit instead of letter at start
        "",
        "A000123456",    # missing trailing letter
    ])
    def test_invalid_pins_exit_1(self, pin: str) -> None:
        result = runner.invoke(app, ["pin", "check", pin])
        assert result.exit_code == 1

    def test_invalid_pin_shows_expected_format(self) -> None:
        result = runner.invoke(app, ["pin", "check", "BADPIN"])
        assert result.exit_code == 1
        assert "A000000000B" in result.output or "format" in result.output.lower()

    def test_json_flag_valid(self) -> None:
        result = runner.invoke(app, ["pin", "check", "A000123456B", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["pin"] == "A000123456B"
        assert payload["valid"] is True

    def test_json_flag_invalid(self) -> None:
        result = runner.invoke(app, ["pin", "check", "NOTAPIN", "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["pin"] == "NOTAPIN"
        assert payload["valid"] is False

    def test_missing_pin_arg_errors(self) -> None:
        result = runner.invoke(app, ["pin", "check"])
        assert result.exit_code != 0
