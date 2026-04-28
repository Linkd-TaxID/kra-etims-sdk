"""
Tests for step 8: etims pin validate

Live GavaConnect PIN lookup via TIaaS — middleware endpoint pending.
These tests document the command's full contract so that when the
/v2/taxpayer/lookup endpoint ships, integration can be verified without
re-reading the source.

Patching:
- get_client patched at kra_etims.cli._client.get_client (lazy import site).
- client._request mocked directly on the returned MagicMock — the command
  calls client._request("GET", "/v2/taxpayer/lookup?pin=...") rather than
  a named SDK method.

Format validation happens BEFORE any API call, so bad-PIN tests need
no client mock at all.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()

_CLIENT_MOD = "kra_etims.cli._client"

_VALID_PIN   = "A000123456B"
_INVALID_PIN = "NOTAPIN"

# ---------------------------------------------------------------------------
# Canonical API response shapes from the middleware spec
# ---------------------------------------------------------------------------

_ACTIVE_RESPONSE = {
    "data": {
        "taxpayerName": "Acme Trading Ltd",
        "status": "ACTIVE",
        "registered": True,
    }
}

_INACTIVE_RESPONSE = {
    "data": {
        "taxpayerName": "Dormant Co",
        "status": "INACTIVE",
        "active": False,
    }
}

# Flat shape — no "data" wrapper (middleware may return either)
_FLAT_RESPONSE = {
    "taxpayerName": "Flat Response Co",
    "status": "ACTIVE",
    "active": True,
}

# Alternate field name: "name" instead of "taxpayerName"
_ALT_NAME_RESPONSE = {
    "data": {
        "name": "Alt Name Co",
        "status": "ACTIVE",
        "registered": True,
    }
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.lookup_pin.return_value = _ACTIVE_RESPONSE
    return client


# ---------------------------------------------------------------------------
# Format validation (no API call — format guard fires first)
# ---------------------------------------------------------------------------

class TestPinValidateFormatGuard:
    def test_invalid_format_exits_1(self) -> None:
        result = runner.invoke(app, ["pin", "validate", _INVALID_PIN, "--api-key", "dummy"])
        assert result.exit_code == 1

    def test_invalid_format_no_api_call(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            runner.invoke(app, ["pin", "validate", _INVALID_PIN, "--api-key", "dummy"])
        mock_client._request.assert_not_called()

    def test_invalid_format_shows_error_message(self) -> None:
        result = runner.invoke(app, ["pin", "validate", _INVALID_PIN, "--api-key", "dummy"])
        assert _INVALID_PIN in result.output
        assert "format" in result.output.lower() or "valid" in result.output.lower()

    @pytest.mark.parametrize("bad_pin", [
        "a000123456B",   # lowercase first
        "A00012345B",    # 8 digits
        "A0001234567B",  # 10 digits
        "1000123456B",   # digit at start
        "A000123456",    # missing trailing letter
        "",
    ])
    def test_various_invalid_formats_exit_1(self, bad_pin: str) -> None:
        result = runner.invoke(app, ["pin", "validate", bad_pin, "--api-key", "dummy"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# API call mechanics
# ---------------------------------------------------------------------------

class TestPinValidateAPICall:
    def test_valid_pin_calls_api(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        mock_client.lookup_pin.assert_called_once_with(_VALID_PIN)

    def test_correct_pin_passed_to_lookup(self, mock_client: MagicMock) -> None:
        pin = "P123456789Z"
        mock_client.lookup_pin.return_value = _ACTIVE_RESPONSE
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            runner.invoke(app, ["pin", "validate", pin, "--api-key", "dummy"])
        mock_client.lookup_pin.assert_called_once_with(pin)

    def test_api_error_propagates(self, mock_client: MagicMock) -> None:
        mock_client.lookup_pin.side_effect = RuntimeError("connection refused")
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Human output — field rendering
# ---------------------------------------------------------------------------

class TestPinValidateHumanOutput:
    def test_shows_pin_in_output(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert _VALID_PIN in result.output

    def test_shows_taxpayer_name(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "Acme Trading Ltd" in result.output

    def test_shows_status(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "ACTIVE" in result.output

    def test_active_registered_shows_yes(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "Yes" in result.output

    def test_inactive_shows_no(self, mock_client: MagicMock) -> None:
        mock_client.lookup_pin.return_value = _INACTIVE_RESPONSE
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "No" in result.output

    def test_flat_response_no_data_wrapper(self, mock_client: MagicMock) -> None:
        # Middleware may return flat JSON without a "data" key
        mock_client.lookup_pin.return_value = _FLAT_RESPONSE
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert result.exit_code == 0, result.output
        assert "Flat Response Co" in result.output

    def test_alternate_name_field(self, mock_client: MagicMock) -> None:
        # "name" is the fallback when "taxpayerName" is absent
        mock_client.lookup_pin.return_value = _ALT_NAME_RESPONSE
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "Alt Name Co" in result.output

    def test_missing_name_shows_dash(self, mock_client: MagicMock) -> None:
        mock_client.lookup_pin.return_value = {"data": {"status": "ACTIVE"}}
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, ["pin", "validate", _VALID_PIN, "--api-key", "dummy"])
        assert "—" in result.output


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

class TestPinValidateJsonOutput:
    def test_json_flag_emits_raw_response(self, mock_client: MagicMock) -> None:
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "pin", "validate", _VALID_PIN, "--api-key", "dummy", "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == _ACTIVE_RESPONSE

    def test_json_flag_flat_response(self, mock_client: MagicMock) -> None:
        mock_client.lookup_pin.return_value = _FLAT_RESPONSE
        with patch(f"{_CLIENT_MOD}.get_client", return_value=mock_client):
            result = runner.invoke(app, [
                "pin", "validate", _VALID_PIN, "--api-key", "dummy", "--json",
            ])
        payload = json.loads(result.output)
        assert payload["taxpayerName"] == "Flat Response Co"

    def test_invalid_format_with_json_flag_exits_1(self) -> None:
        # Format guard fires before --json is checked; exit code is still 1
        result = runner.invoke(app, [
            "pin", "validate", _INVALID_PIN, "--api-key", "dummy", "--json",
        ])
        assert result.exit_code == 1
