import time
import pytest
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed
from kra_etims.client import KRAeTIMSClient


def test_authenticate_thread_safety():
    """
    Stress test: 100 concurrent threads all hit _authenticate simultaneously
    with no token set. The double-checked lock must ensure the OAuth endpoint
    is called exactly once regardless of thread count.
    """
    client = KRAeTIMSClient(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api.test.co.ke",
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "mock_access_token",
        "expires_in": 3600,
    }

    mock_session = MagicMock()

    def delayed_post(*args, **kwargs):
        # 0.5s delay ensures all threads are inside _authenticate
        # before the first one completes, maximising contention.
        time.sleep(0.5)
        return mock_response

    mock_session.post.side_effect = delayed_post

    # Patch _get_session so every thread's session.post is our mock.
    with patch.object(client, "_get_session", return_value=mock_session):
        num_threads = 100
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(client._authenticate) for _ in range(num_threads)]
            for future in as_completed(futures):
                future.result()  # re-raises if any thread threw

        assert mock_session.post.call_count == 1, (
            f"Expected exactly 1 OAuth call, got {mock_session.post.call_count}. "
            "Thread-safety violation: double-checked lock is broken."
        )
        assert client._access_token == "mock_access_token"
