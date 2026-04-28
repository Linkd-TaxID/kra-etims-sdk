"""
Tests for `etims auth login / logout / status`.

Isolation strategy:
- Config file is redirected to tmp_path via monkeypatching config_path() in main.
- keyring helper functions are patched at the `kra_etims.cli.main` import site
  (where they land after `from .config import ...`) — not at the source module,
  because Python's import mechanism binds the name at import time.
- TAXID_API_KEY env var is cleared before each test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()

_MAIN = "kra_etims.cli.main"
_CONFIG = "kra_etims.cli.config"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)


@pytest.fixture()
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config read/write to tmp_path."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(f"{_CONFIG}.user_config_dir", lambda _: str(tmp_path))
    monkeypatch.setattr(f"{_MAIN}.config_path", lambda: cfg)
    return cfg


class FakeKeyring:
    """Simple in-memory keyring store."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set(self, key: str) -> bool:
        self._store[("etims-cli", "api_key")] = key
        return True

    def get(self) -> str | None:
        return self._store.get(("etims-cli", "api_key"))

    def delete(self) -> bool:
        existed = ("etims-cli", "api_key") in self._store
        self._store.pop(("etims-cli", "api_key"), None)
        return existed


@pytest.fixture()
def keyring_ok(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    """Patch all keyring-facing functions in main with a real in-memory store."""
    kr = FakeKeyring()
    monkeypatch.setattr(f"{_MAIN}.keyring_available", lambda: True)
    monkeypatch.setattr(f"{_MAIN}.set_api_key", kr.set)
    monkeypatch.setattr(f"{_MAIN}.get_api_key", kr.get)
    monkeypatch.setattr(f"{_MAIN}.delete_api_key", kr.delete)
    return kr


@pytest.fixture()
def keyring_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MAIN}.keyring_available", lambda: False)
    monkeypatch.setattr(f"{_MAIN}.get_api_key", lambda: None)
    monkeypatch.setattr(f"{_MAIN}.set_api_key", lambda _: False)
    monkeypatch.setattr(f"{_MAIN}.delete_api_key", lambda: False)


# ---------------------------------------------------------------------------
# auth login
# ---------------------------------------------------------------------------

class TestAuthLogin:
    def test_login_via_flag_stores_to_keyring(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        result = runner.invoke(app, ["auth", "login", "--api-key", "test-key-123"])
        assert result.exit_code == 0, result.output
        assert "TIaaS API key" in result.output or "keyring" in result.output.lower()
        assert keyring_ok.get() == "test-key-123"

    def test_login_stores_config_options(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        result = runner.invoke(app, [
            "auth", "login",
            "--api-key", "k",
            "--tin", "A000123456B",
            "--bhf-id", "01",
            "--base-url", "https://custom.api",
        ])
        assert result.exit_code == 0, result.output
        assert tmp_config.exists(), "config.toml should be written"
        content = tmp_config.read_text()
        assert "A000123456B" in content
        assert "01" in content
        assert "custom.api" in content

    def test_login_blank_key_rejected(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        result = runner.invoke(app, ["auth", "login", "--api-key", "   "])
        assert result.exit_code == 1
        # Error message should mention blank or cannot
        assert "blank" in result.output.lower() or "cannot" in result.output.lower()

    def test_login_headless_exits_1(self, tmp_config: Path, keyring_unavailable: Any) -> None:
        # Headless login must exit 1 — the key is not stored anywhere, so
        # "success" would be a lie. The error message directs to TAXID_API_KEY.
        result = runner.invoke(app, ["auth", "login", "--api-key", "test-key"])
        assert result.exit_code == 1
        assert "TAXID_API_KEY" in result.output or "headless" in result.output.lower()

    def test_login_prompts_for_key_when_omitted(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        result = runner.invoke(app, ["auth", "login"], input="prompted-key-789\n")
        assert result.exit_code == 0, result.output
        assert keyring_ok.get() == "prompted-key-789"


# ---------------------------------------------------------------------------
# auth logout
# ---------------------------------------------------------------------------

class TestAuthLogout:
    def test_logout_removes_key(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        keyring_ok.set("existing-key")
        result = runner.invoke(app, ["auth", "logout"])
        assert result.exit_code == 0, result.output
        assert "removed" in result.output.lower()
        assert keyring_ok.get() is None

    def test_logout_no_key_shows_warning(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        # keyring empty — should warn, not crash
        result = runner.invoke(app, ["auth", "logout"])
        assert "TAXID_API_KEY" in result.output or "not found" in result.output.lower() or "unavailable" in result.output.lower()

    def test_logout_headless_warns(self, tmp_config: Path, keyring_unavailable: Any) -> None:
        result = runner.invoke(app, ["auth", "logout"])
        assert "not found" in result.output.lower() or "environment" in result.output.lower()


# ---------------------------------------------------------------------------
# auth status
# ---------------------------------------------------------------------------

class TestAuthStatus:
    def test_status_with_keyring_key(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        keyring_ok.set("stored-key")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
        assert "configured" in result.output.lower()
        assert "keyring" in result.output.lower()

    def test_status_with_env_var(self, tmp_config: Path, keyring_ok: FakeKeyring, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAXID_API_KEY", "env-key-abc")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
        assert "TAXID_API_KEY" in result.output

    def test_status_no_key_exits_1(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        # Empty keyring, no env var
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 1
        assert "No API key" in result.output or "not configured" in result.output.lower()

    def test_status_shows_config_values(self, tmp_config: Path, keyring_ok: FakeKeyring) -> None:
        keyring_ok.set("k")
        tmp_config.write_text(
            '[default]\ntin = "A000999999Z"\nbhf_id = "03"\nbase_url = ""\n',
            encoding="utf-8",
        )
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
        assert "A000999999Z" in result.output
        assert "03" in result.output

    def test_status_keyring_unavailable_row(self, tmp_config: Path, keyring_unavailable: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAXID_API_KEY", "env-key")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.output
        # The "Keyring" row should say "not available"
        assert "not available" in result.output.lower() or "env var" in result.output.lower()
