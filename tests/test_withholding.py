import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from pydantic import ValidationError
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.withholding import WithholdingPolicy, WithholdingReceipt, WithholdingRefund, WithholdingDeduction, WithholdingSnapshot

TIME = datetime(2026, 10, 9, tzinfo=timezone.utc)


def policy():
    return WithholdingPolicy(policyId="test", version=1, treatment="SIMULATION",
        category="MARKETPLACE_RESIDENT", basis="GROSS_RECEIPTS", effectiveFrom=TIME,
        effectiveUntil=datetime(2026, 10, 10, tzinfo=timezone.utc), decisionEvidence="Synthetic test")


def receipt(**changes):
    values = dict(eventId="e", creatorId="creator", policyId="test", policyVersion=1,
        occurredAt=TIME, grossReceipts="1000.00", platformFee="75.00", receiptVat="0.00",
        platformFeeVat="0.00", sourceEvidence="Synthetic payment")
    values.update(changes)
    return WithholdingReceipt(**values)


def snapshot(kind="RECEIPT"):
    return dict(eventId="e", originalEventId=None, kind=kind, creatorId="creator",
        occurredAt=TIME.isoformat(), policy=policy().model_dump(mode="json"),
        status="SIMULATED_CALCULATION", grossReceipts="1000.00", platformFee="75.00",
        receiptVat="0.00", platformFeeVat="0.00", calculationBasis="1000.00", rate="0.05",
        calculatedWithholding="50.00", withheld="0.00", pendingRemittance="0.00",
        remitted="0.00", netPayable="875.00", evidence="Synthetic payment")


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "1.001", "-1"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValidationError):
        receipt(grossReceipts=value)


def test_no_tax_band_or_missing_treatment():
    with pytest.raises(ValidationError):
        receipt(taxTyCd="B")
    with pytest.raises(ValidationError):
        WithholdingPolicy(**(policy().model_dump() | {"category": "UNRESOLVED"}))


def test_unknown_tax_stays_null():
    data = snapshot()
    data.update(calculatedWithholding=None, calculationBasis=None, rate=None, netPayable=None, status="UNCONFIGURED")
    assert WithholdingSnapshot.model_validate(data).calculatedWithholding is None


def client_ready(client):
    client._access_token = "mock"
    client._token_expiry = 9999999999
    return client


def test_sync_transport_preserves_exact_numeric_response(httpx_mock):
    body = json.dumps(snapshot()).replace('"1000.00"', '90071992547409.91')
    httpx_mock.add_response(method="POST", url="https://api.test/v2/withholding/receipts", text=body)
    with client_ready(KRAeTIMSClient(base_url="https://api.test")) as client:
        response = client.withholding.record_receipt(receipt())
    assert response.grossReceipts == Decimal("90071992547409.91")
    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent["grossReceipts"] == "1000.00"
    assert "tin" not in sent and "bhfId" not in sent


@pytest.mark.asyncio
async def test_async_transport_parity(httpx_mock):
    httpx_mock.add_response(method="POST", url="https://api.test/v2/withholding/receipts", json=snapshot())
    async with client_ready(AsyncKRAeTIMSClient(base_url="https://api.test")) as client:
        response = await client.withholding.record_receipt(receipt())
    assert response.calculatedWithholding == Decimal("50")
    assert json.loads(httpx_mock.get_requests()[0].content)["platformFee"] == "75.00"


def test_all_sync_routes(httpx_mock):
    p = policy()
    refund = WithholdingRefund(eventId="r", occurredAt=TIME, grossRefund="10", feeRefund="0", receiptVatRefund="0", feeVatRefund="0", approvalEvidence="e")
    deduction = WithholdingDeduction(eventId="d", occurredAt=TIME, evidence="e")
    reconciliation = dict(original=snapshot(), entries=[], grossReceipts="1000", platformFee="75", receiptVat="0", platformFeeVat="0", calculatedWithholding="50", withheld="0", pendingRemittance="0", remitted="0", netPayable="925", withholdingReviewRequired=False, remittanceStatus="NOT_IMPLEMENTED", certificateStatus="NOT_IMPLEMENTED")
    cases=[("POST", "/policies", p.model_dump(mode="json")), ("GET", "/events/e", snapshot()), ("POST", "/receipts/e/refunds", snapshot("REFUND")), ("POST", "/receipts/e/deductions", snapshot("DEDUCTION")), ("GET", "/receipts/e/reconciliation", reconciliation)]
    for method,path,body in cases:
        httpx_mock.add_response(method=method,url="https://api.test/v2/withholding"+path,json=body)
    with client_ready(KRAeTIMSClient(base_url="https://api.test")) as client:
        client.withholding.create_policy(p)
        client.withholding.get_event("e")
        client.withholding.record_refund("e",refund)
        client.withholding.record_deduction("e",deduction)
        assert client.withholding.reconcile("e").remitted == 0


def test_real_java_wire_fixture(httpx_mock):
    fixture = Path(__file__).parent / "fixtures" / "withholding-receipt.json"
    httpx_mock.add_response(method="GET",url="https://api.test/v2/withholding/events/e",text=fixture.read_text())
    with client_ready(KRAeTIMSClient(base_url="https://api.test")) as client:
        result=client.withholding.get_event("e")
    assert result.calculatedWithholding == Decimal("50")
    assert result.netPayable == Decimal("863")
    assert result.remitted == 0


@pytest.mark.asyncio
async def test_all_async_routes_and_exact_numeric_response(httpx_mock):
    p=policy()
    r=WithholdingRefund(eventId="r", occurredAt=TIME, grossRefund="10", feeRefund="0", receiptVatRefund="0", feeVatRefund="0", approvalEvidence="e")
    d=WithholdingDeduction(eventId="d", occurredAt=TIME, evidence="e")
    balances=dict(original=snapshot(), entries=[], grossReceipts="1000", platformFee="75", receiptVat="0", platformFeeVat="0", calculatedWithholding="50", withheld="0", pendingRemittance="0", remitted="0", netPayable="925", withholdingReviewRequired=False, remittanceStatus="NOT_IMPLEMENTED", certificateStatus="NOT_IMPLEMENTED")
    for method,path,body in [("POST","/policies",p.model_dump(mode="json")),("GET","/events/e",snapshot()),("POST","/receipts/e/refunds",snapshot("REFUND")),("POST","/receipts/e/deductions",snapshot("DEDUCTION")),("GET","/receipts/e/reconciliation",balances)]:
        httpx_mock.add_response(method=method,url="https://api.test/v2/withholding"+path,text=json.dumps(body).replace('"1000.00"','90071992547409.91'))
    async with client_ready(AsyncKRAeTIMSClient(base_url="https://api.test")) as client:
        await client.withholding.create_policy(p)
        assert (await client.withholding.get_event("e")).grossReceipts == Decimal("90071992547409.91")
        await client.withholding.record_refund("e",r)
        await client.withholding.record_deduction("e",d)
        assert (await client.withholding.reconcile("e")).remitted == 0


def test_ids_and_dates_are_retrievable():
    with pytest.raises(ValidationError):
        receipt(eventId="event/with/slashes")
    with pytest.raises(ValidationError):
        receipt(occurredAt=datetime(2026,10,9))
