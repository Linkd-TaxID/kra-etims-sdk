import asyncio
import json
import httpx
import pytest
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient


@pytest.mark.parametrize("identity", ["reference", "key", "both"])
def test_sync_correction_identity_wire(identity):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(201,json={"creditNoteId":2,"originalPurchaseId":1,"status":"SIGNED"})
    with KRAeTIMSClient(api_key="synthetic",base_url="https://taxid.test") as client:
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        kwargs = {}
        if identity in {"reference","both"}:
            kwargs["client_reference"] = "refund-source"
        if identity in {"key","both"}:
            kwargs["idempotency_key"] = "refund-operation"
        client.issue_credit_note(1,reason="Synthetic refund",**kwargs)
    assert seen[0].url.path == "/v2/etims/sale/1/credit-note"
    assert json.loads(seen[0].content).get("clientReference") == kwargs.get("client_reference")
    assert seen[0].headers.get("Idempotency-Key") == kwargs.get("idempotency_key")


def test_async_correction_identity_wire():
    async def run():
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(201,json={"creditNoteId":2,"status":"SIGNED"})
        async with AsyncKRAeTIMSClient(api_key="synthetic",base_url="https://taxid.test") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            await client.issue_credit_note(1,reason="Synthetic refund",client_reference="refund-source",idempotency_key="refund-operation")
        assert json.loads(seen[0].content)["clientReference"] == "refund-source"
        assert seen[0].headers["Idempotency-Key"] == "refund-operation"
    asyncio.run(run())


def test_missing_correction_identity_fails_before_transport():
    with KRAeTIMSClient(api_key="synthetic") as client:
        with pytest.raises(ValueError,match="stable"):
            client.issue_credit_note(1)
    async def run():
        async with AsyncKRAeTIMSClient(api_key="synthetic") as client:
            with pytest.raises(ValueError,match="stable"):
                await client.issue_credit_note(1)
    asyncio.run(run())
