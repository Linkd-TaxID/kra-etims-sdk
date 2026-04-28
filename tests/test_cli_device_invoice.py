"""
Tests for step 3:
  etims device init / device status
  etims invoice submit (file, stdin, dry-run, --json)

Isolation strategy:
- `get_client` is imported lazily inside each command function body
  (`from ._client import get_client`), so it must be patched at
  `kra_etims.cli._client.get_client`.
- Auth is bypassed by patching `resolve_api_key` to return a dummy key.
- No real HTTP calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()

_CLIENT_MOD = "kra_etims.cli._client"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)


@pytest.fixture()
def mock_client() -> MagicMock:
    """A preconfigured mock KRAeTIMSClient."""
    client = MagicMock()
    client.initialize_device.return_value = {
        "resultCd": "000",
        "resultMsg": "Success",
        "data": {},
    }
    client.check_compliance.return_value = {
        "resultCd": "000",
        "status": "COMPLIANT",
        "resultMsg": "Device is compliant",
    }
    client.submit_sale.return_value = {
        "resultCd": "000",
        "resultMsg": "Signed",
        "data": {
            "rcptNo": "KRACU0100000001/001 NS",
            "confirmDt": "20260426142211",
            "intrlData": "SIG_TRUNCATED",
            "rcptSign": "QR_TRUNCATED",
        },
    }
    return client


# Band A (0% exempt) item — passes ItemDetail math validators:
#   totAmt = qty * uprc = 1 * 1000.00 = 1000.00
#   taxblAmt + taxAmt = 1000.00 + 0.00 = 1000.00 ✓
_VALID_ITEM = {
    "itemCd": "ITEM001",
    "itemNm": "Test Item",
    "qty": "1",
    "uprc": "1000.00",
    "totAmt": "1000.00",
    "taxTyCd": "A",
    "taxblAmt": "1000.00",
    "taxAmt": "0.00",
}

_VALID_INVOICE = {
    "tin": "A000123456B",
    "bhfId": "00",
    "invcNo": "INV-001",
    "confirmDt": "20260426142211",
    "totItemCnt": 1,
    "totTaxblAmt": "1000.00",
    "totTaxAmt": "0.00",
    "totAmt": "1000.00",
    "itemList": [_VALID_ITEM],
}


# ---------------------------------------------------------------------------
# device init
# ---------------------------------------------------------------------------

class TestDeviceInit:
    def test_init_success(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "device", "init",
                "--tin", "A000123456B",
                "--bhf-id", "00",
                "--serial", "VSCU001",
                "--api-key", "dummy-key",
            ])
        assert result.exit_code == 0, result.output
        assert "A000123456B" in result.output
        mock_client.initialize_device.assert_called_once()

    def test_init_json_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "device", "init",
                "--tin", "A000123456B",
                "--api-key", "dummy-key",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "resultCd" in payload

    def test_init_missing_tin_errors(self) -> None:
        result = runner.invoke(app, ["device", "init"])
        assert result.exit_code != 0

    def test_init_passes_serial_to_sdk(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            runner.invoke(app, [
                "device", "init",
                "--tin", "A000123456B",
                "--serial", "MY_SERIAL_XYZ",
                "--api-key", "dummy-key",
            ])
        call_args = mock_client.initialize_device.call_args[0][0]
        assert call_args.dvcSrlNo == "MY_SERIAL_XYZ"


# ---------------------------------------------------------------------------
# device status
# ---------------------------------------------------------------------------

class TestDeviceStatus:
    def test_status_success(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "device", "status",
                "--pin", "A000123456B",
                "--api-key", "dummy-key",
            ])
        assert result.exit_code == 0, result.output
        assert "A000123456B" in result.output
        mock_client.check_compliance.assert_called_once_with("A000123456B")

    def test_status_json_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "device", "status",
                "--pin", "A000123456B",
                "--api-key", "dummy-key",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "COMPLIANT"


# ---------------------------------------------------------------------------
# invoice submit
# ---------------------------------------------------------------------------

class TestInvoiceSubmit:
    def test_submit_from_file(self, tmp_path: Path, mock_client: MagicMock) -> None:
        invoice_file = tmp_path / "invoice.json"
        invoice_file.write_text(json.dumps(_VALID_INVOICE), encoding="utf-8")

        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "invoice", "submit", str(invoice_file),
                "--api-key", "dummy-key",
            ])
        assert result.exit_code == 0, result.output
        assert "Signed" in result.output or "KRACU" in result.output
        mock_client.submit_sale.assert_called_once()

    def test_submit_from_stdin(self, mock_client: MagicMock) -> None:
        raw = json.dumps(_VALID_INVOICE)
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "invoice", "submit", "-",
                "--api-key", "dummy-key",
            ], input=raw)
        assert result.exit_code == 0, result.output
        mock_client.submit_sale.assert_called_once()

    def test_submit_json_flag(self, tmp_path: Path, mock_client: MagicMock) -> None:
        invoice_file = tmp_path / "invoice.json"
        invoice_file.write_text(json.dumps(_VALID_INVOICE), encoding="utf-8")

        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "invoice", "submit", str(invoice_file),
                "--api-key", "dummy-key",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["data"]["rcptNo"] == "KRACU0100000001/001 NS"

    def test_submit_dry_run_skips_api(self, tmp_path: Path, mock_client: MagicMock) -> None:
        invoice_file = tmp_path / "invoice.json"
        invoice_file.write_text(json.dumps(_VALID_INVOICE), encoding="utf-8")

        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "invoice", "submit", str(invoice_file),
                "--dry-run",
                "--api-key", "dummy-key",
            ])
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output
        mock_client.submit_sale.assert_not_called()

    def test_submit_file_not_found(self) -> None:
        result = runner.invoke(app, [
            "invoice", "submit", "/nonexistent/path/invoice.json",
            "--api-key", "dummy-key",
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "no such" in result.output.lower()

    def test_submit_invalid_json_exits_1(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        result = runner.invoke(app, [
            "invoice", "submit", str(bad_file),
            "--api-key", "dummy-key",
        ])
        assert result.exit_code == 1
        assert "invalid json" in result.output.lower() or "json" in result.output.lower()

    def test_submit_pydantic_error_exits_1(self, tmp_path: Path) -> None:
        bad_invoice = tmp_path / "bad_invoice.json"
        bad_invoice.write_text(json.dumps({"tin": "INVALID"}), encoding="utf-8")
        result = runner.invoke(app, [
            "invoice", "submit", str(bad_invoice),
            "--api-key", "dummy-key",
        ])
        assert result.exit_code == 1
        assert "validation" in result.output.lower() or "failed" in result.output.lower()

    def test_submit_math_error_exits_1(self, tmp_path: Path) -> None:
        # totAmt deliberately wrong (1500 ≠ qty*uprc=1000)
        bad_math = {**_VALID_INVOICE, "itemList": [{**_VALID_ITEM, "totAmt": "1500.00"}]}
        bad_file = tmp_path / "bad_math.json"
        bad_file.write_text(json.dumps(bad_math), encoding="utf-8")
        result = runner.invoke(app, [
            "invoice", "submit", str(bad_file),
            "--api-key", "dummy-key",
        ])
        assert result.exit_code == 1
        assert "validation" in result.output.lower() or "math" in result.output.lower() or "failed" in result.output.lower()

    def test_submit_passes_idempotency_key(self, tmp_path: Path, mock_client: MagicMock) -> None:
        invoice_file = tmp_path / "invoice.json"
        invoice_file.write_text(json.dumps(_VALID_INVOICE), encoding="utf-8")

        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            runner.invoke(app, [
                "invoice", "submit", str(invoice_file),
                "--api-key", "dummy-key",
                "--idempotency-key", "idem-abc-123",
            ])
        mock_client.submit_sale.assert_called_once()
        _, kwargs = mock_client.submit_sale.call_args
        assert kwargs.get("idempotency_key") == "idem-abc-123"

    def test_submit_no_file_arg_exits_1(self) -> None:
        result = runner.invoke(app, ["invoice", "submit"])
        assert result.exit_code == 1
        assert "stdin" in result.output.lower() or "provide" in result.output.lower() or "file" in result.output.lower()
