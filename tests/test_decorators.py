import pytest
import responses
from kra_etims.client import KRAeTIMSClient

@responses.activate
def test_sanitize_kra_url_strips_parameter_whitespace():
    """
    Scenario: Pass a URL/Parameter with trailing spaces to a decorated method.
    Assertion: The outgoing HTTP request must have the spaces stripped.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test  " # Trailing spaces in base_url
    )
    client._access_token = "mock"
    client._token_expiry = 9999999999
    
    # Mock endpoint
    responses.add(
        responses.GET,
        "https://api.test/v2/etims/compliance/P001",
        status=200,
        json={"status": "compliant"}
    )
    
    # We pass " P001 " with spaces
    client.check_compliance("  P001  ")
    
    # Verify the intercepted request URL
    # It should be https://api.test/v2/etims/compliance/P001
    assert responses.calls[0].request.url == "https://api.test/v2/etims/compliance/P001"
    print("\n✅ Middleware: Systematic whitespace stripping verified.")
