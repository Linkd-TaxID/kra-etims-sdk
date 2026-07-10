"""
Wire-contract tests for ``bulk_import_items`` (Track C5) and ``get_sale_status``
(Track C4) — both client methods existed uncommitted with no test coverage
before this file. Mirrors ``test_item_wire_contract.py``'s pattern.
"""
import pytest

from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import KRAeTIMSError


def _make_csv(tmp_path):
    csv_path = tmp_path / "items.csv"
    csv_path.write_text(
        "sku,itemNm,itemClsCd,taxTyCd,qty,unitPrice\n"
        "PMS-SUPER,Super Petrol (PMS),15101506,E,1,214.03\n"
    )
    return str(csv_path)


# ---------------------------------------------------------------------------
# bulk_import_items — sync
# ---------------------------------------------------------------------------

def test_bulk_import_items_posts_multipart_to_bulk_import_endpoint(httpx_mock, tmp_path):
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/v2/etims/items/bulk-import",
        json={"totalRows": 1, "succeeded": 1, "failed": 0, "results": []},
        status_code=200,
    )

    result = client.bulk_import_items(_make_csv(tmp_path))

    assert result["failed"] == 0
    assert result["succeeded"] == 1

    sent = httpx_mock.get_requests()[0]
    assert sent.url.path == "/v2/etims/items/bulk-import"
    # Middleware endpoint requires a multipart body, not JSON.
    assert sent.headers["content-type"].startswith("multipart/form-data")


def test_bulk_import_items_partial_failure_does_not_raise(httpx_mock, tmp_path):
    """
    207-equivalent case: some rows failed. The docstring is explicit that this
    is NOT raised as an error -- callers must check `failed` themselves.
    """
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/v2/etims/items/bulk-import",
        json={
            "totalRows": 2, "succeeded": 1, "failed": 1,
            "results": [
                {"row": 1, "sku": "PMS-SUPER", "success": True},
                {"row": 2, "sku": "BAD-ROW", "success": False, "error": "unknown itemClsCd"},
            ],
        },
        status_code=200,
    )

    result = client.bulk_import_items(_make_csv(tmp_path))
    assert result["failed"] == 1
    assert result["results"][1]["success"] is False


def test_bulk_import_items_structural_rejection_raises(httpx_mock, tmp_path):
    """Empty file / missing column / row-count ceiling -> middleware 400, whole upload rejected."""
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/v2/etims/items/bulk-import",
        status_code=400,
    )

    with pytest.raises(KRAeTIMSError):
        client.bulk_import_items(_make_csv(tmp_path))


@pytest.mark.asyncio
async def test_async_bulk_import_items_posts_multipart(httpx_mock, tmp_path):
    async with AsyncKRAeTIMSClient("id", "secret", base_url="https://api.test") as client:
        client._access_token = "mock"
        client._token_expiry = 9999999999

        httpx_mock.add_response(
            method="POST",
            url="https://api.test/v2/etims/items/bulk-import",
            json={"totalRows": 1, "succeeded": 1, "failed": 0, "results": []},
            status_code=200,
        )

        result = await client.bulk_import_items(_make_csv(tmp_path))
        assert result["succeeded"] == 1

        sent = httpx_mock.get_requests()[0]
        assert sent.headers["content-type"].startswith("multipart/form-data")


# ---------------------------------------------------------------------------
# get_sale_status
# ---------------------------------------------------------------------------

def test_get_sale_status_returns_receipt_fields(httpx_mock):
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="GET",
        url="https://api.test/v2/etims/sales/283/status",
        json={
            "purchaseId": 283,
            "status": "SYNCED",
            "cuInvoiceNumber": "KRACU0100000001/1 NS",
            "sdcId": "KRACU0100000001",
            "receiptSignature": "V249-J39C-FJ48-HE2W",
            "kraQrPayload": "20042026#120000#KRACU0100000001#1#...#...",
            "vscuTimestamp": "20260420120000",
        },
        status_code=200,
    )

    result = client.get_sale_status(283)
    assert result["status"] == "SYNCED"
    assert result["purchaseId"] == 283

    sent = httpx_mock.get_requests()[0]
    assert sent.url.path == "/v2/etims/sales/283/status"
    assert sent.method == "GET"


def test_get_sale_status_includes_failure_reason_when_failed(httpx_mock):
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="GET",
        url="https://api.test/v2/etims/sales/284/status",
        json={
            "purchaseId": 284,
            "status": "FAILED",
            "failureReason": "Terminal VSCU error: 42 — Authentication failed",
        },
        status_code=200,
    )

    result = client.get_sale_status(284)
    assert result["status"] == "FAILED"
    assert "failureReason" in result


def test_get_sale_status_404_raises_kra_etims_error(httpx_mock):
    """No sale with this purchaseId for the authenticated tenant."""
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="GET",
        url="https://api.test/v2/etims/sales/99999/status",
        status_code=404,
    )

    with pytest.raises(KRAeTIMSError):
        client.get_sale_status(99999)


@pytest.mark.asyncio
async def test_async_get_sale_status_returns_receipt_fields(httpx_mock):
    async with AsyncKRAeTIMSClient("id", "secret", base_url="https://api.test") as client:
        client._access_token = "mock"
        client._token_expiry = 9999999999

        httpx_mock.add_response(
            method="GET",
            url="https://api.test/v2/etims/sales/283/status",
            json={"purchaseId": 283, "status": "SYNCED"},
            status_code=200,
        )

        result = await client.get_sale_status(283)
        assert result["status"] == "SYNCED"
