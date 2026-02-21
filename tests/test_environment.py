import pytest
from kra_etims.client import KRAeTIMSClient

def test_url_resolution_priority(monkeypatch):
    """
    Priority: TAXID_API_URL > constructor kwarg > hardcoded default.
    """
    # 1. Fallback to hardcoded default
    monkeypatch.delenv("TAXID_API_URL", raising=False)
    client_default = KRAeTIMSClient("id", "secret")
    assert client_default.base_url == "https://taxid-production.up.railway.app"

    # 2. Kwarg override
    client_kwarg = KRAeTIMSClient("id", "secret", base_url="https://sandbox.test.co.ke")
    assert client_kwarg.base_url == "https://sandbox.test.co.ke"

    # 3. Env var override (highest priority)
    monkeypatch.setenv("TAXID_API_URL", "https://env.api.test")
    client_env = KRAeTIMSClient("id", "secret", base_url="https://sandbox.test.co.ke")
    assert client_env.base_url == "https://env.api.test"

def test_url_sanitization():
    """
    Verify trailing slashes and whitespace are systematically stripped.
    """
    # Test whitespace stripping
    client_ws = KRAeTIMSClient("id", "secret", base_url="  https://api.test  ")
    assert client_ws.base_url == "https://api.test"

    # Test trailing slash stripping
    client_slash = KRAeTIMSClient("id", "secret", base_url="https://api.test/")
    assert client_slash.base_url == "https://api.test"

    # Test combined
    client_both = KRAeTIMSClient("id", "secret", base_url="  https://api.test/  ")
    assert client_both.base_url == "https://api.test"
