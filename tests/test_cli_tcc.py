"""
Tests for:
  etims tcc check     — GavaConnect TCC validation (no TIaaS needed)
  etims pin validate  — auto-routing: GavaConnect direct vs TIaaS fallback
  etims auth login    — GavaConnect credential storage
  etims auth status   — shows both TIaaS and GavaConnect credential state
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app
from kra_etims.gavaconnect import GavaConnectPINNotFoundError, GavaConnectTCCError

runner = CliRunner()

_MAIN   = "kra_etims.cli.main"
_CLIENT = "kra_etims.cli._client"
_CONFIG = "kra_etims.cli.config"

_PIN = "A000123456B"
_TCC = "TCC2026001234"
_KEY = "consumer-key-abc"
_SEC = "consumer-secret-xyz"

_PIN_RESPONSE = {
    "ResponseCode": "23000",
    "Status": "OK",
    "PINDATA": {
        "KRAPIN": "A***6B",
        "TypeOfTaxpayer": "Individual",
        "Name": "J**n D**",
        "StatusOfPIN": "Active",
    },
}

_TCC_RESPONSE = {
    "Status": "OK",
    "TCCData": {"KRAPIN": _PIN},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_gc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAVACONNECT_CONSUMER_KEY",    raising=False)
    monkeypatch.delenv("GAVACONNECT_CONSUMER_SECRET", raising=False)
    monkeypatch.delenv("TAXID_API_KEY",               raising=False)


@pytest.fixture()
def mock_gc_client() -> MagicMock:
    client = MagicMock()
    client.lookup_pin.return_value = _PIN_RESPONSE
    client.check_tcc.return_value  = _TCC_RESPONSE
    return client


# ===========================================================================
# etims tcc check
# ===========================================================================

class TestTccCheck:

    def test_valid_tcc_human_output(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
            result = runner.invoke(app, [
                "tcc", "check",
                "--pin", _PIN,
                "--tcc-number", _TCC,
                "--consumer-key", _KEY,
                "--consumer-secret", _SEC,
            ])
        assert result.exit_code == 0, result.output
        assert _TCC in result.output or "valid" in result.output.lower()

    def test_valid_tcc_json_output(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
            result = runner.invoke(app, [
                "tcc", "check",
                "--pin", _PIN,
                "--tcc-number", _TCC,
                "--consumer-key", _KEY,
                "--consumer-secret", _SEC,
                "--json",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["Status"] == "OK"
        assert "TCCData" in payload

    def test_invalid_tcc_exits_1(self, mock_gc_client: MagicMock) -> None:
        mock_gc_client.check_tcc.side_effect = GavaConnectTCCError("invalid or expired")
        with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
            result = runner.invoke(app, [
                "tcc", "check",
                "--pin", _PIN,
                "--tcc-number", _TCC,
                "--consumer-key", _KEY,
                "--consumer-secret", _SEC,
            ])
        assert result.exit_code == 1

    def test_invalid_pin_format_rejected_before_api_call(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
            result = runner.invoke(app, [
                "tcc", "check",
                "--pin", "BADPIN",
                "--tcc-number", _TCC,
                "--consumer-key", _KEY,
                "--consumer-secret", _SEC,
            ])
        assert result.exit_code == 1
        mock_gc_client.check_tcc.assert_not_called()

    def test_missing_credentials_exits_1(self) -> None:
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=None):
            result = runner.invoke(app, [
                "tcc", "check",
                "--pin", _PIN,
                "--tcc-number", _TCC,
            ])
        assert result.exit_code == 1
        assert "developer.go.ke" in result.output or "GavaConnect" in result.output

    def test_gc_client_called_with_correct_args(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
            runner.invoke(app, [
                "tcc", "check",
                "--pin", _PIN,
                "--tcc-number", _TCC,
                "--consumer-key", _KEY,
                "--consumer-secret", _SEC,
            ])
        mock_gc_client.check_tcc.assert_called_once_with(_PIN, _TCC)


# ===========================================================================
# etims pin validate — auto-routing
# ===========================================================================

class TestPinValidateRouting:

    def test_uses_gavaconnect_when_creds_available(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=(_KEY, _SEC)):
            with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
                result = runner.invoke(app, [
                    "pin", "validate", _PIN,
                    "--consumer-key", _KEY,
                    "--consumer-secret", _SEC,
                ])
        assert result.exit_code == 0, result.output
        mock_gc_client.lookup_pin.assert_called_once_with(_PIN)

    def test_gavaconnect_output_shows_source(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=(_KEY, _SEC)):
            with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
                result = runner.invoke(app, [
                    "pin", "validate", _PIN,
                    "--consumer-key", _KEY,
                    "--consumer-secret", _SEC,
                ])
        assert "GavaConnect" in result.output

    def test_falls_back_to_tiaas_when_no_gc_creds(self) -> None:
        tiaas_client = MagicMock()
        tiaas_client.lookup_pin.return_value = {"data": {"taxpayerName": "Test Co", "status": "Active"}}
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=None):
            with patch(f"{_CLIENT}.get_client", return_value=tiaas_client):
                result = runner.invoke(app, [
                    "pin", "validate", _PIN,
                    "--api-key", "tiaas-key",
                ])
        assert result.exit_code == 0, result.output
        tiaas_client.lookup_pin.assert_called_once_with(_PIN)

    def test_tiaas_fallback_output_shows_source(self) -> None:
        tiaas_client = MagicMock()
        tiaas_client.lookup_pin.return_value = {"data": {"status": "Active"}}
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=None):
            with patch(f"{_CLIENT}.get_client", return_value=tiaas_client):
                result = runner.invoke(app, [
                    "pin", "validate", _PIN,
                    "--api-key", "tiaas-key",
                ])
        assert "TIaaS" in result.output

    def test_pin_not_found_exits_1(self, mock_gc_client: MagicMock) -> None:
        mock_gc_client.lookup_pin.side_effect = GavaConnectPINNotFoundError("not found")
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=(_KEY, _SEC)):
            with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
                result = runner.invoke(app, ["pin", "validate", _PIN])
        assert result.exit_code == 1

    def test_json_output_gavaconnect(self, mock_gc_client: MagicMock) -> None:
        with patch(f"{_CLIENT}.resolve_gavaconnect_creds", return_value=(_KEY, _SEC)):
            with patch(f"{_CLIENT}.get_gavaconnect_client", return_value=mock_gc_client):
                result = runner.invoke(app, [
                    "pin", "validate", _PIN, "--json",
                ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["Status"] == "OK"
        assert "PINDATA" in payload


# ===========================================================================
# etims auth login — GavaConnect credential storage
# ===========================================================================

class TestAuthLoginGavaConnect:

    @pytest.fixture()
    def keyring_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(f"{_MAIN}.keyring_available", lambda: True)
        monkeypatch.setattr(f"{_MAIN}.set_api_key", lambda _: True)
        monkeypatch.setattr(f"{_MAIN}.set_consumer_secret", lambda _: True)

    def test_login_gavaconnect_only(self, keyring_ok: None, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
        result = runner.invoke(app, [
            "auth", "login",
            "--consumer-key", _KEY,
            "--consumer-secret", _SEC,
        ])
        assert result.exit_code == 0, result.output
        assert "GavaConnect" in result.output

    def test_login_both_tiaas_and_gavaconnect(self, keyring_ok: None, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
        result = runner.invoke(app, [
            "auth", "login",
            "--api-key", "tiaas-key-abc",
            "--consumer-key", _KEY,
            "--consumer-secret", _SEC,
        ])
        assert result.exit_code == 0, result.output
        assert "TIaaS" in result.output
        assert "GavaConnect" in result.output

    def test_consumer_key_stored_in_config(self, keyring_ok: None, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
        cfg = tmp_path / "config.toml"
        result = runner.invoke(app, [
            "auth", "login",
            "--consumer-key", _KEY,
            "--consumer-secret", _SEC,
        ])
        assert result.exit_code == 0, result.output
        assert cfg.exists()
        assert _KEY in cfg.read_text()

    def test_partial_gc_creds_rejected(self, keyring_ok: None, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
        result = runner.invoke(app, [
            "auth", "login",
            "--consumer-key", _KEY,
            # missing --consumer-secret
        ])
        assert result.exit_code == 1
        assert "together" in result.output.lower() or "secret" in result.output.lower()

    def test_sandbox_flag_stored_in_config(self, keyring_ok: None, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
        result = runner.invoke(app, [
            "auth", "login",
            "--consumer-key", _KEY,
            "--consumer-secret", _SEC,
            "--sandbox",
        ])
        assert result.exit_code == 0, result.output
        cfg_text = (tmp_path / "config.toml").read_text()
        assert "sandbox" in cfg_text or "true" in cfg_text


# ===========================================================================
# etims auth status — shows GavaConnect state
# ===========================================================================

class TestAuthStatusGavaConnect:

    def test_shows_gavaconnect_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAXID_API_KEY", "tiaas-key")
        monkeypatch.setenv("GAVACONNECT_CONSUMER_KEY", _KEY)
        monkeypatch.setenv("GAVACONNECT_CONSUMER_SECRET", _SEC)
        with patch(f"{_MAIN}.keyring_available", return_value=True):
            with patch(f"{_MAIN}.get_api_key", return_value=None):
                with patch(f"{_MAIN}.get_consumer_secret", return_value=_SEC):
                    result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
        assert "GavaConnect" in result.output

    def test_status_exits_1_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch(f"{_MAIN}.keyring_available", return_value=True):
            with patch(f"{_MAIN}.get_api_key", return_value=None):
                with patch(f"{_MAIN}.get_consumer_secret", return_value=None):
                    result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 1
        assert "developer.go.ke" in result.output or "GavaConnect" in result.output

    def test_status_ok_with_only_gavaconnect_creds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAVACONNECT_CONSUMER_KEY", _KEY)
        monkeypatch.setenv("GAVACONNECT_CONSUMER_SECRET", _SEC)
        with patch(f"{_MAIN}.keyring_available", return_value=True):
            with patch(f"{_MAIN}.get_api_key", return_value=None):
                with patch(f"{_MAIN}.get_consumer_secret", return_value=_SEC):
                    result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
