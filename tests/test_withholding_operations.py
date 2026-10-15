from datetime import datetime, date, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from kra_etims.withholding import WithholdingPolicy, WithholdingContract, WithholdingPolicyApproval, WithholdingRefundReview
from kra_etims.remittance import RemittanceCalendar, RemittanceBatch, RemittanceExport
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient


def contract(**changes):
    return WithholdingContract(**(dict(schemaVersion=1,residency='RESIDENT',rate='0.05',platformFeeVat='SUPPLIED_AMOUNT',waivers='NONE',gifts='UNSUPPORTED',refunds='REVIEW_NO_TAX_RELEASE',legalSources='Official source references and external assessment',assessmentEvidence='Supplied assessment')|changes))


def policy(**changes):
    return WithholdingPolicy(**(dict(policyId='p',version=1,treatment='ASSESSED',category='DIGITAL_CONTENT_RESIDENT',basis='GROSS_RECEIPTS',effectiveFrom=datetime(2026,10,1,tzinfo=timezone.utc),effectiveUntil=datetime(2026,11,1,tzinfo=timezone.utc),decisionEvidence='Supplied decision',contract=contract())|changes))


@pytest.mark.parametrize('changes',[{'contract':None},{'basis':'RECEIPTS_EXCLUDING_VAT'},{'category':'UNRESOLVED'},{'contract':contract(rate='0.20')},{'contract':contract(residency='NONRESIDENT_NO_PE')}])
def test_assessed_contract_is_explicit_and_bounded(changes):
    with pytest.raises(ValidationError): policy(**changes)


@pytest.mark.parametrize('changes',[{'rate':0.05},{'waivers':'AUTOMATIC'},{'gifts':'EXEMPT'},{'refunds':'RELEASE_TAX'},{'schemaVersion':2}])
def test_unresolved_treatments_and_float_rates_unavailable(changes):
    with pytest.raises(ValidationError): contract(**changes)


def calendar():
    return RemittanceCalendar(version='synthetic',validFrom=date(2026,10,1),validUntil=date(2026,10,31),holidays=[date(2026,10,20)],sourceEvidence='Synthetic public holiday review')


def test_deadline_holidays_timezone_and_insufficient_coverage():
    assert calendar().deadline(datetime(2026,10,15,22,tzinfo=timezone.utc)) == date(2026,10,26)
    with pytest.raises(ValueError): calendar().deadline(datetime(2026,10,30,tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        RemittanceExport(operationId='e',creatorPin='A000000001X',amount='5',deductedAt=datetime(2026,10,16,tzinfo=timezone.utc),dueDate=date(2026,10,23),deadlineEvidence='e',assessmentEvidence='e',deductionEvidence='e',calendar=calendar())


def test_batches_and_refund_outcomes():
    with pytest.raises(ValidationError): RemittanceBatch(operationId='b',receiptIds=['r','r'],evidence='e')
    with pytest.raises(ValidationError): WithholdingRefundReview(action='APPROVE',outcome='RELEASE_TAX',assessmentEvidence='e',evidence='e')
    assert policy().contract.rate == Decimal('0.05')


def ready(client):
    client._access_token='mock';client._token_expiry=9999999999
    return client


def test_sync_approval_batch_and_refund_review_routes(httpx_mock):
    for route in ['policies/p/versions/1/approval','remittance-batches','refunds/r/approval']:
        httpx_mock.add_response(method='POST',url='https://api.test/v2/withholding/'+route,json={'evidenceVerification':'SUPPLIED_UNVERIFIED'})
    httpx_mock.add_response(method='GET',url='https://api.test/v2/withholding/remittance-batches/b',json={'state':'OPERATOR_WORK_MANIFEST'})
    with ready(KRAeTIMSClient(base_url='https://api.test')) as c:
        c.withholding.approve_policy('p',1,WithholdingPolicyApproval(operationId='a',assessmentEvidence='e',approvalEvidence='e'))
        c.withholding.prepare_batch(RemittanceBatch(operationId='b',receiptIds=['r'],evidence='e'))
        c.withholding.review_refund('r',WithholdingRefundReview(action='APPROVE',outcome='RETAIN_DEDUCTION',assessmentEvidence='e',evidence='e'))
        assert c.withholding.get_batch('b')['state']=='OPERATOR_WORK_MANIFEST'


@pytest.mark.asyncio
async def test_async_approval_batch_and_refund_review_parity(httpx_mock):
    for route in ['policies/p/versions/1/approval','remittance-batches','refunds/r/review']:
        httpx_mock.add_response(method='POST',url='https://api.test/v2/withholding/'+route,json={'evidenceVerification':'SUPPLIED_UNVERIFIED'})
    httpx_mock.add_response(method='GET',url='https://api.test/v2/withholding/remittance-batches/b',json={'state':'OPERATOR_WORK_MANIFEST'})
    async with ready(AsyncKRAeTIMSClient(base_url='https://api.test')) as c:
        await c.withholding.approve_policy('p',1,WithholdingPolicyApproval(operationId='a',assessmentEvidence='e',approvalEvidence='e'))
        await c.withholding.prepare_batch(RemittanceBatch(operationId='b',receiptIds=['r'],evidence='e'))
        await c.withholding.review_refund('r',WithholdingRefundReview(action='ASSESS',outcome='RETAIN_DEDUCTION',assessmentEvidence='e',evidence='e'))
        assert (await c.withholding.get_batch('b'))['state']=='OPERATOR_WORK_MANIFEST'


def test_sync_batch_payment_retains_aggregate_decimal_and_reference(httpx_mock):
    from kra_etims.remittance import RemittanceTransition
    httpx_mock.add_response(method='POST',url='https://api.test/v2/withholding/remittance-batches/b/actions',json={'evidenceVerification':'SUPPLIED_UNVERIFIED'})
    with ready(KRAeTIMSClient(base_url='https://api.test')) as c:
        result=c.withholding.batch_action('b',RemittanceTransition(operationId='payment',action='RECORD_EVIDENCE',reference='shared-prn',amount='15.01',evidence='Supplied aggregate payment evidence'))
        assert result['evidenceVerification']=='SUPPLIED_UNVERIFIED'
    import json
    request=json.loads(httpx_mock.get_requests()[0].content)
    assert request['amount']=='15.01' and request['reference']=='shared-prn'


@pytest.mark.asyncio
async def test_async_batch_reconciliation_and_unsupported_certificate(httpx_mock):
    from kra_etims.remittance import RemittanceTransition
    httpx_mock.add_response(method='POST',url='https://api.test/v2/withholding/remittance-batches/b/actions',json={'verifiedRemittance':0})
    async with ready(AsyncKRAeTIMSClient(base_url='https://api.test')) as c:
        result=await c.withholding.batch_action('b',RemittanceTransition(operationId='reconcile',action='RECONCILE',amount='15.01',evidence='Match supplied evidence'))
        assert result['verifiedRemittance']==0
        with pytest.raises(ValueError,match='per case'):
            await c.withholding.batch_action('b',RemittanceTransition(operationId='certificate',action='RECORD_CERTIFICATE',reference='supplied-certificate',evidence='Unverified document'))


def test_sync_policy_inspection_before_approval(httpx_mock):
    httpx_mock.add_response(method='GET',url='https://api.test/v2/withholding/policies/p/versions/1',json=policy().model_dump(mode='json'))
    with ready(KRAeTIMSClient(base_url='https://api.test')) as c:
        assert c.withholding.get_policy('p',1)==policy()


@pytest.mark.asyncio
async def test_async_policy_inspection_before_approval(httpx_mock):
    httpx_mock.add_response(method='GET',url='https://api.test/v2/withholding/policies/p/versions/1',json=policy().model_dump(mode='json'))
    async with ready(AsyncKRAeTIMSClient(base_url='https://api.test')) as c:
        assert await c.withholding.get_policy('p',1)==policy()
