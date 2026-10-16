import json
from decimal import Decimal
from unittest.mock import Mock, AsyncMock
import pytest
from kra_etims import KRAeTIMSClient, AsyncKRAeTIMSClient
from kra_etims.models import BranchCustomerRegistration, ItemDetail, OriginalTransportSnapshot
from kra_etims.prerequisites import original_transport_evidence


def item():
    return ItemDetail(itemCd="source/sku",itemNm="Fixture",itemClsCd="5059690800",qty=1,uprc=Decimal("116"),totAmt=Decimal("116"),taxTyCd="B",taxAmt=Decimal("16"),taxblAmt=Decimal("100"))


def test_registration_and_original_lookup():
    with KRAeTIMSClient(api_key="synthetic",base_url="http://127.0.0.1") as c:
        c._request=Mock(return_value={"registered":True})
        c.register_item(item())
        call=c._request.call_args
        assert call.args==("POST","/v2/etims/items") and call.kwargs["json"]["sku"]=="source/sku"
        c.register_customer(BranchCustomerRegistration(pin="A000123456B",name="Fixture"))
        assert c._request.call_args.kwargs["json"]=={"pin":"A000123456B","name":"Fixture"}
        c.find_sale(operation_key="original/key + space")
        assert c._request.call_args.args[1].endswith("operationKey=original%2Fkey+%2B+space")
        with pytest.raises(ValueError):c.find_sale(client_reference="a",operation_key="b")
        with pytest.raises(ValueError):c.find_sale()


@pytest.mark.asyncio
async def test_async_registration_and_inspection():
    async with AsyncKRAeTIMSClient(api_key="synthetic",base_url="http://127.0.0.1") as c:
        c._request=AsyncMock(return_value={"registered":True})
        await c.register_item(item())
        await c.register_customer(BranchCustomerRegistration(pin="A000123456B",name="Fixture"))
        await c.find_sale(client_reference="original")
        assert c._request.call_args.args==( "GET", "/v2/etims/sales/search?clientReference=original")
        await c.prerequisites.inspect_sale_transport(42)
        assert c._request.call_args.args==("GET","/v2/admin/reconciliation/42/transport")


def test_evidence_binds_transport_without_inventing_verification():
    snapshot=OriginalTransportSnapshot(identity='{"protocol":"OSCU"}',business_sha256="a"*64,wire_sha256="b"*64)
    evidence=json.loads(original_transport_evidence(snapshot.model_dump(),outcome="SIGNED",provenance="supplied synthetic protocol evidence"))
    assert evidence["businessSha256"]=="a"*64 and evidence["wireSha256"]=="b"*64
    assert evidence["outcome"]=="SIGNED" and "cmcKey" not in evidence
    with pytest.raises(ValueError):original_transport_evidence(snapshot.model_dump(),outcome="SIGNED",provenance="")


def test_list_inspection_preserves_application_errors():
    import httpx
    from kra_etims.exceptions import KRAeTIMSError
    with KRAeTIMSClient(api_key="synthetic") as client:
        request=httpx.Request("GET","http://127.0.0.1/v2/admin/reconciliation")
        assert client._parse_response(httpx.Response(200,json=[{"queueEntryId":1,"status":"OUTCOME_UNKNOWN"}],request=request),"GET",None)[0]["queueEntryId"]==1
        with pytest.raises(KRAeTIMSError):client._parse_response(httpx.Response(200,json=["invalid"],request=request),"GET",None)
        with pytest.raises(KRAeTIMSError):client._parse_response(httpx.Response(200,json=[{"resultCd":"999"}],request=request),"GET",None)
        with pytest.raises(KRAeTIMSError):client._handle_error_response([{"unrecognized":"sale array"}])
        assert client._parse_response(httpx.Response(204,request=request),"POST",None)=={}


def test_orphaned_execution_requires_bound_quiescence():
    snapshot={"identity":"{}","business_sha256":"a"*64,"wire_sha256":"b"*64,"executionOwner":"original-boot","transportReturned":False}
    with pytest.raises(ValueError):original_transport_evidence(snapshot,outcome="SIGNED",provenance="operator assertion")
    evidence=json.loads(original_transport_evidence(snapshot,outcome="SIGNED",provenance="supplied original server receipt",quiescence_evidence="Original process termination verified independently"))
    assert evidence["executionOwner"]=="original-boot" and evidence["quiescenceEvidence"]


def test_catalogue_model_has_no_fictitious_invoice_totals():
    from kra_etims.models import ItemRegistration
    item=ItemRegistration(sku="catalogue",itemNm="Fixture",itemClsCd="5059690800",taxTyCd="B",unitPrice=Decimal("116"))
    with KRAeTIMSClient(api_key="synthetic") as client:
        client._request=Mock(return_value={"registered":True})
        client.register_item(item)
        payload=client._request.call_args.kwargs["json"]
        assert payload["sku"]=="catalogue" and payload["unitPrice"]=="116" and "totAmt" not in payload
    with pytest.raises(ValueError):ItemRegistration(sku="catalogue",itemNm="Fixture",itemClsCd="5059690800",taxTyCd="B",unitPrice=116.0)
