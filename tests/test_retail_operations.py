from decimal import Decimal
from unittest.mock import Mock, AsyncMock
import pytest
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.models import ReturnLine, CreditNoteReason, StockOperationResult
from kra_etims.reports import ZReport


def test_correction_transport_sends_only_original_selection():
    with KRAeTIMSClient(api_key="synthetic") as client:
        client._request = Mock(return_value={"status": "SIGNED"})
        client.issue_credit_note(7, return_lines=[ReturnLine(originalItemSeq=2, qty=Decimal("0.50"))], reason_code=CreditNoteReason.REFUND, client_reference="return-7", idempotency_key="return-7")
        args = client._request.call_args
        assert args.args == ("POST", "/v2/etims/sale/7/credit-note")
        assert args.kwargs["json"] == {"clientReference": "return-7", "returnLines": [{"originalItemSeq": 2, "qty": "0.50"}], "reasonCode": "06"}
        assert args.kwargs["idempotency_key"] == "return-7"


@pytest.mark.asyncio
async def test_async_original_selection_and_recovery_paths():
    async with AsyncKRAeTIMSClient(api_key="synthetic") as client:
        client._request = AsyncMock(return_value={"status":"PENDING_SYNC"})
        await client.issue_credit_note(7, return_lines=[ReturnLine(originalItemSeq=1,qty=Decimal("1"))],idempotency_key="same-key")
        assert client._request.call_args.kwargs["json"]["returnLines"][0]["originalItemSeq"]==1
        await client.retail.resolve_stock(tin="S000000001X",branch="01",key="original",payload_sha256="a"*64,outcome="SYNCED",evidence="fixture",reason="test")
        assert client._request.call_args.args==("POST","/v2/admin/retail/stock/resolve")


def test_local_close_does_not_claim_remote_acceptance():
    report=ZReport.from_api({"reportDate":"2026-10-09","tin":"S000000001X","branchId":"00","summary":{},"vscuAcknowledged":False,"kraAcknowledged":False,"closureScope":"LOCAL_AGGREGATE","deviceFiscalCloseSupported":False})
    assert not report.vscu_acknowledged and not report.kra_acknowledged
    assert report.closure_scope=="LOCAL_AGGREGATE" and not report.device_fiscal_close_supported


def test_return_quantity_precision_and_stock_outcome_model():
    with pytest.raises(ValueError): ReturnLine(originalItemSeq=1,qty=Decimal("0.001"))
    result=StockOperationResult(operationKey="original",movementId=1,sarNo=1,status="OUTCOME_UNKNOWN",payloadSha256="a"*64,totAmt="40",totTaxAmt="5.52")
    assert result["status"]=="OUTCOME_UNKNOWN" and not result.kraAcknowledged


def test_local_close_unresolved_conflict_preserves_recovery_code():
    from kra_etims.exceptions import KRAConflictError, ZReportAlreadyIssuedError
    with KRAeTIMSClient(api_key="synthetic") as client:
        client._request = Mock(side_effect=KRAConflictError("unresolved", code="DAY_CLOSE_UNRESOLVED"))
        with pytest.raises(KRAConflictError) as error:
            client.reports.get_daily_z("2026-10-09")
        assert error.value.code == "DAY_CLOSE_UNRESOLVED"
        client._request = Mock(side_effect=KRAConflictError("completed", code="Z_REPORT_ALREADY_ISSUED"))
        with pytest.raises(ZReportAlreadyIssuedError):
            client.reports.get_daily_z("2026-10-09")


@pytest.mark.asyncio
async def test_async_local_close_conflict_and_recovery_envelopes():
    from kra_etims.exceptions import KRAConflictError
    async with AsyncKRAeTIMSClient(api_key="synthetic") as client:
        client._request = AsyncMock(side_effect=KRAConflictError("unresolved", code="DAY_CLOSE_UNRESOLVED"))
        with pytest.raises(KRAConflictError) as error:
            await client.reports.get_daily_z("2026-10-09")
        assert error.value.code == "DAY_CLOSE_UNRESOLVED"
        client._request = AsyncMock(return_value={"cases": [{"state": "OUTCOME_UNKNOWN"}]})
        assert (await client.retail.inspect_stock(tin="S000000001X", branch="01", key="original"))[0]["state"] == "OUTCOME_UNKNOWN"
        assert (await client.retail.inspect_admission(tin="S000000001X", branch="01", key="original"))[0]["state"] == "OUTCOME_UNKNOWN"
