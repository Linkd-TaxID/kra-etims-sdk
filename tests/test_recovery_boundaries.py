"""Transport uncertainty remains visible to queue and gateway callers."""
import threading
from decimal import Decimal

import httpx
import pytest

from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.client import KRAeTIMSClient
from kra_etims.exceptions import TIaaSUnavailableError
from kra_etims.gateway import SupplierEntry


@pytest.mark.asyncio
async def test_offline_queue_preserves_unknown_operation(httpx_mock, minimal_invoice):
    httpx_mock.add_exception(httpx.ReadTimeout("response lost"), method="POST",
                             url="https://api.taxid.co.ke/v2/etims/sale")
    async with AsyncKRAeTIMSClient(api_key="test") as client:
        results = await client.flush_offline_queue([minimal_invoice])
    assert results[0]["status"] == "error"
    assert results[0]["error_type"] == "TIaaSAmbiguousStateError"
    assert results[0]["ambiguous"] is True
    assert results[0]["retryable"] is False
    assert results[0]["idempotency_key"] == f"{minimal_invoice.tin}:{minimal_invoice.invcNo}"
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_gateway_connect_failure_retries_original_identity(httpx_mock, monkeypatch):
    monkeypatch.setattr("kra_etims.gateway._backoff", lambda attempt: 0)
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), method="POST",
                             url="https://api.taxid.co.ke/v2/gateway/supplier-onboarding/single", is_reusable=True)
    async with AsyncKRAeTIMSClient(api_key="test") as client:
        with pytest.raises(TIaaSUnavailableError):
            await client.gateway.onboard_supplier(phone="+254700000000", amount=5000,
                                                   buyer_pin="A000123456B", buyer_name="Example",
                                                   idempotency_key="supplier-connect-failure")
    requests = httpx_mock.get_requests()
    assert len(requests) == 4
    assert {request.headers["Idempotency-Key"] for request in requests} == {"supplier-connect-failure"}


def test_gateway_initialization_independent_of_authentication_lock():
    with KRAeTIMSClient(api_key="test") as client:
        initialized = threading.Event()
        gateways = []
        errors = []

        def initialize_gateway():
            try:
                gateways.append(client.gateway)
            except BaseException as exc:
                errors.append(exc)
            finally:
                initialized.set()

        with client._auth_lock:
            worker = threading.Thread(target=initialize_gateway, daemon=True)
            worker.start()
            completed_while_auth_locked = initialized.wait(timeout=5)
        worker.join(timeout=5)
        assert completed_while_auth_locked
        assert not worker.is_alive()
        assert errors == []
        assert gateways == [client.gateway]


def test_gateway_preserves_scientific_notation_decimal():
    entry = SupplierEntry(phone="+254700000000", amount="1.23e-4")
    from kra_etims.gateway import _money
    assert _money(entry.amount) == str(Decimal("0.000123"))
