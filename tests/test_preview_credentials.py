import pytest

from kra_etims import AsyncKRAeTIMSClient, KRAeTIMSClient


def test_two_branch_clients_override_process_defaults(monkeypatch, httpx_mock):
    monkeypatch.setenv("TAXID_API_KEY", "global-key")
    monkeypatch.setenv("TAXID_API_URL", "https://global.example")
    httpx_mock.add_response(url="https://first.example/v2/etims/sales/1/status", json={"status": "SIGNED"})
    httpx_mock.add_response(url="https://second.example/v2/etims/sales/2/status", json={"status": "SIGNED"})

    with KRAeTIMSClient(api_key="first-key", base_url="https://first.example") as first:
        first.get_sale_status(1)
    with KRAeTIMSClient(api_key="second-key", base_url="https://second.example") as second:
        second.get_sale_status(2)

    requests = httpx_mock.get_requests()
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer first-key", "Bearer second-key"
    ]
    assert all("/oauth/token" not in str(request.url) for request in requests)


@pytest.mark.asyncio
async def test_async_key_only_client_uses_explicit_branch(monkeypatch, httpx_mock):
    monkeypatch.setenv("TAXID_API_KEY", "global-key")
    monkeypatch.setenv("TAXID_API_URL", "https://global.example")
    httpx_mock.add_response(url="https://branch.example/v2/etims/sales/3/status", json={"status": "SIGNED"})
    async with AsyncKRAeTIMSClient(api_key="branch-key", base_url="https://branch.example") as client:
        await client.get_sale_status(3)
    assert httpx_mock.get_request().headers["Authorization"] == "Bearer branch-key"


def test_environment_remains_a_default_for_key_only_client(monkeypatch, httpx_mock):
    monkeypatch.setenv("TAXID_API_KEY", "default-key")
    monkeypatch.setenv("TAXID_API_URL", "https://default.example")
    httpx_mock.add_response(url="https://default.example/v2/etims/sales/4/status", json={"status": "SIGNED"})
    with KRAeTIMSClient() as client:
        client.get_sale_status(4)
    assert httpx_mock.get_request().headers["Authorization"] == "Bearer default-key"
