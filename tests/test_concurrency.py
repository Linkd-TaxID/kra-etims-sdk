import time
import pytest
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed
from kra_etims.client import KRAeTIMSClient

def test_authenticate_thread_safety():
    """
    Stress test: Simulate 100 concurrent threads hitting _authenticate.
    Mocks the authentication endpoint with a 0.5s delay.
    Asserts that the external endpoint is called exactly once.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke"
    )

    # Mock response data
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "mock_access_token",
        "expires_in": 3600
    }

    # Use a side_effect to simulate a network delay
    def delayed_post(*args, **kwargs):
        time.sleep(0.5)
        return mock_response

    with patch.object(client._session, 'post', side_effect=delayed_post) as mocked_post:
        # Run 100 threads simultaneously
        num_threads = 100
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            # Submit all tasks
            futures = [executor.submit(client._authenticate) for _ in range(num_threads)]
            
            # Wait for all to complete
            for future in as_completed(futures):
                future.result() # Will raise exception if any thread failed

        # Assertions
        # If thread-locking works, mocked_post should only be called once.
        # Without locking, multiple threads would see _access_token as None 
        # simultaneously and trigger multiple POST requests.
        assert mocked_post.call_count == 1, (
            f"Expected exactly 1 call to authentication endpoint, but got {mocked_post.call_count}. "
            "This suggests a thread-safety violation or missing lock."
        )
        assert client._access_token == "mock_access_token"
        print(f"\n✅ Success: 100 concurrent requests resulted in exactly {mocked_post.call_count} auth call.")
