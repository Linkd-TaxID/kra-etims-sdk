"""
CLI configuration — two-layer storage.

Non-sensitive settings  → ~/.config/etims/config.toml   (via platformdirs)
API key                 → OS keyring (macOS Keychain / Windows Credential Manager /
                          Linux SecretService via keyring 25+)

Fallback chain for API key (enforced in _client.py):
  1. --api-key CLI flag
  2. TAXID_API_KEY environment variable  (Railway, Docker, CI)
  3. OS keyring entry
  4. Abort with clear instructions

Headless environments (no keyring backend): use TAXID_API_KEY env var.
"""

from __future__ import annotations

import sys
import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-reattr]
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_config_dir, user_data_dir

_APP = "etims"
_KEYRING_SERVICE = "etims-cli"
_KEYRING_USERNAME = "api_key"

# GavaConnect credentials — consumer_key is semi-public (stored in TOML,
# like an OAuth2 client_id). consumer_secret is sensitive (keyring only).
_GC_KEYRING_SERVICE  = "etims-gavaconnect"
_GC_KEYRING_USERNAME = "consumer_secret"

_DEFAULTS: dict[str, str] = {
    "base_url": "",
    "tin": "",
    "bhf_id": "00",
    "consumer_key": "",
    "gavaconnect_sandbox": "",
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def config_path() -> Path:
    return Path(user_config_dir(_APP)) / "config.toml"


def data_dir() -> Path:
    d = Path(user_data_dir(_APP))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# TOML config (non-sensitive)
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f).get("default", {})


def save_config(updates: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            existing = tomllib.load(f)

    section: dict[str, Any] = existing.get("default", {})
    section.update({k: v for k, v in updates.items() if v is not None})
    existing["default"] = section

    # Atomic write: write to a temp file beside the target, then rename.
    # rename() is atomic on POSIX — the file is either the old version or
    # the new one, never a partially-written hybrid on power loss.
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(existing, f)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Keyring (API key only)
# ---------------------------------------------------------------------------

def keyring_available() -> bool:
    """True when a real OS keyring backend is present.

    Checks the actual type rather than a substring of the class name.
    keyring.backends.fail.Keyring covers both FailKeyring and the null
    backend — it is the canonical "no backend available" sentinel.
    PlaintextKeyring (from keyrings.alt) is NOT a real secure backend;
    it writes to a world-readable file and is treated as unavailable here.
    """
    try:
        import keyring
        import keyring.backends.fail
        backend = keyring.get_keyring()
        if isinstance(backend, keyring.backends.fail.Keyring):
            return False
        # Reject keyrings.alt plaintext file backend — insecure on shared systems.
        module = type(backend).__module__
        if module.startswith("keyrings.alt"):
            return False
        return True
    except Exception:
        return False


def get_api_key() -> Optional[str]:
    """Read API key from OS keyring. Returns None if not set or unavailable."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        return None


def set_api_key(key: str) -> bool:
    """
    Store API key in OS keyring.
    Returns True on success, False if no keyring backend is available.
    """
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
        return True
    except Exception:
        return False


def delete_api_key() -> bool:
    """Remove API key from OS keyring. Returns True on success."""
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GavaConnect consumer secret (keyring only)
# ---------------------------------------------------------------------------

def get_consumer_secret() -> Optional[str]:
    """Read GavaConnect consumer secret from OS keyring."""
    try:
        import keyring
        return keyring.get_password(_GC_KEYRING_SERVICE, _GC_KEYRING_USERNAME)
    except Exception:
        return None


def set_consumer_secret(secret: str) -> bool:
    """Store GavaConnect consumer secret in OS keyring. Returns True on success."""
    try:
        import keyring
        keyring.set_password(_GC_KEYRING_SERVICE, _GC_KEYRING_USERNAME, secret)
        return True
    except Exception:
        return False


def delete_consumer_secret() -> bool:
    """Remove GavaConnect consumer secret from keyring. Returns True on success."""
    try:
        import keyring
        keyring.delete_password(_GC_KEYRING_SERVICE, _GC_KEYRING_USERNAME)
        return True
    except Exception:
        return False
