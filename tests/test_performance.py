import time
import pytest
import responses
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import StockItem

@responses.activate
def test_batch_update_stock_performance():
    """
    Scenario: Supermarket inventory spike with 10,000 items.
    Assertion: Exactly 20 requests (chunked at 500) and sub-1.5s execution.
    """
    client = KRAeTIMSClient(
        client_id="perf_id",
        client_secret="perf_secret",
        base_url="https://api.perf.test"
    )
    client._access_token = "mock_token"
    client._token_expiry = 9999999999

    # Mock success for all POST requests to the batch endpoint
    responses.add(
        responses.POST,
        "https://api.perf.test/v2/etims/stock/batch",
        json={"status": "success"},
        status=200
    )

    # Generate 10,000 distinct items
    items = [
        StockItem(
            tin="P000000000X",
            bhfId="00",
            itemCd=f"ITM-{i}",
            rsonCd="01",
            qty=100.0
        ) for i in range(10000)
    ]

    start_time = time.perf_counter()
    results = client.batch_update_stock(items)
    end_time = time.perf_counter()

    duration = end_time - start_time

    # Assertions
    assert len(results) == 20 # 20 chunks of 500
    # Verify exactly 20 HTTP requests were made
    batch_calls = [r for r in responses.calls if r.request.method == "POST" and "/v2/etims/stock/batch" in r.request.url]
    assert len(batch_calls) == 20
    
    print(f"\n⏱ Execution time for 10,000 items: {duration:.4f}s")
    assert duration < 1.5, f"Performance test failed: {duration:.4f}s >= 1.5s"
