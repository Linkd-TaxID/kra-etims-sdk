"""
Middleware / transport hygiene tests.

sanitize_kra_url was removed — trailing-space handling is done server-side
by TIaaS TrailingSpaceInterceptor. This file now covers the SDK-level
base_url normalization that replaced it.
"""
from kra_etims.client import KRAeTIMSClient


def test_base_url_strips_trailing_whitespace_and_slash():
    """Constructor must normalise base_url regardless of input formatting."""
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test.co.ke  /  ")
    assert client.base_url == "https://api.test.co.ke", (
        f"Expected 'https://api.test.co.ke', got {client.base_url!r}"
    )


def test_build_url_joins_path_correctly():
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test.co.ke")
    url = client._build_url("/v2/etims/compliance/P001")
    assert url == "https://api.test.co.ke/v2/etims/compliance/P001"


def test_build_url_handles_path_without_leading_slash():
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test.co.ke")
    url = client._build_url("v2/etims/compliance/P001")
    assert url == "https://api.test.co.ke/v2/etims/compliance/P001"
