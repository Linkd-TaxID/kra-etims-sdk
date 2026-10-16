"""Synthetic withholding SDK -> HTTP acceptance. Run taxID/scripts/tests/phase3-accounting.py.
Only the fresh isolated simulator is accepted; no real filing, remittance or payout.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlparse
from kra_etims.client import KRAeTIMSClient
from kra_etims.withholding import (WithholdingPolicy, WithholdingContract, WithholdingPolicyApproval,
    WithholdingReceipt, WithholdingDeduction, WithholdingRefund, WithholdingRefundReview)
from kra_etims.remittance import RemittanceExport, RemittanceTransition, RemittanceCalendar, RemittanceBatch
from kra_etims.platform_sessions import PlatformSessions


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['first','resume'],required=True)
    parser.add_argument('--private-manifest',type=Path,required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args();state=json.loads(args.private_manifest.read_text());origin=state['origin']
    assert urlparse(origin).hostname=='127.0.0.1' and state['isolatedSimulator'] is True
    checks=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    def contained(call):
        try:call()
        except Exception as e:
            check('unsupported/conflicting operation contained',any(c in str(e) for c in ['400','403','404','409','422']))
            return
        raise AssertionError('Expected containment')
    def client(token): return KRAeTIMSClient(api_key=token,base_url=origin)
    def transition(key,action,amount=None,reference=None):
        return RemittanceTransition(operationId=key,action=action,evidence='Synthetic operator evidence, not independent verification',amount=amount,reference=reference)
    if args.phase=='first':
        with client(state['operator']) as administrator:
            state['tin']='S000000001X';state['tokens']={}
            for branch in ['00','01']:
                device=administrator._request('POST','/v2/admin/tenants',json=dict(tin=state['tin'],bhfId=branch,dvcSrlNo='synthetic-accounting-'+branch,vscuBaseUrl=origin,environment='SANDBOX'))
                state['tokens'][branch]=[]
                for n in range(3 if branch=='00' else 1):
                    issued=administrator._request('POST',f"/v2/admin/tenants/{device['id']}/api-keys",json=dict(clientName='Synthetic accounting operator '+str(n),role='ROLE_ADMIN'))
                    state['tokens'][branch].append(issued['apiKey'])
            platform=administrator._request('POST','/v2/admin/platforms',json=dict(name='Synthetic platform',requestsPerMinute=1000))
            grant=administrator._request('POST',f"/v2/admin/platforms/{platform['platformId']}/grants",json=dict(tin=state['tin'],bhfId='00',environment='SANDBOX',scopes=['withholding:read','withholding:write'],requestsPerMinute=1000))
            state['platform']=platform['platformSecret']
            state['grant']=grant['grantId']
        now=datetime.now(timezone.utc)-timedelta(seconds=5);state['deductedAt']=now.isoformat()
        contract=WithholdingContract(schemaVersion=1,residency='RESIDENT',rate='0.05',platformFeeVat='SUPPLIED_AMOUNT',waivers='NONE',gifts='UNSUPPORTED',refunds='REVIEW_NO_TAX_RELEASE',legalSources='Synthetic assessment referencing enacted rules; not legal approval',assessmentEvidence='Synthetic assessed digital-content payment; taxpayer and basis review')
        policy=WithholdingPolicy(policyId='content',version=1,treatment='ASSESSED',category='DIGITAL_CONTENT_RESIDENT',basis='GROSS_RECEIPTS',effectiveFrom=now-timedelta(days=1),effectiveUntil=now+timedelta(days=1),decisionEvidence='Synthetic sandbox policy; production gated',contract=contract)
        receipt=WithholdingReceipt(eventId='receipt',creatorId='seller',policyId='content',policyVersion=1,occurredAt=now,grossReceipts='100.10',platformFee='3.00',receiptVat='0.00',platformFeeVat='0.48',sourceEvidence='Synthetic source payment')
        with client(state['tokens']['00'][0]) as preparer, client(state['tokens']['00'][1]) as reviewer, client(state['tokens']['00'][2]) as approver:
            preparer.withholding.create_policy(policy)
            contained(lambda:preparer.withholding.record_receipt(receipt))
            approval=WithholdingPolicyApproval(operationId='policy-approval',assessmentEvidence=contract.assessmentEvidence,approvalEvidence='Synthetic separate operator approval, not independent KRA approval')
            contained(lambda:preparer.withholding.approve_policy('content',1,approval))
            check('approver inspects exact immutable policy before approval',approver.withholding.get_policy('content',1)==policy)
            result=approver.withholding.approve_policy('content',1,approval)
            check('policy approval never activates production',result['productionEnabled'] is False)
            sale=preparer.withholding.record_receipt(receipt)
            check('decimal half-cent rounding and separate fee VAT',sale.calculatedWithholding==Decimal('5.01') and sale.platformFeeVat==Decimal('0.48') and sale.withheld==0)
            check('same event returns immutable snapshot',preparer.withholding.record_receipt(receipt)==sale)
            def deduct(n):
                with client(state['tokens']['00'][0]) as c:
                    try: return c.withholding.record_deduction('receipt',WithholdingDeduction(eventId='deduction-'+str(n),occurredAt=now,evidence='Synthetic actual deduction evidence',amount='5.01'))
                    except Exception as e:
                        if '409' not in str(e): raise
                        return None
            with ThreadPoolExecutor(max_workers=2) as pool: deductions=list(pool.map(deduct,range(2)))
            check('concurrent distinct deduction events post exactly once',sum(x is not None for x in deductions)==1)
            calendar=RemittanceCalendar(version='synthetic-window',validFrom=now.astimezone(timezone.utc).date()-timedelta(days=1),validUntil=now.date()+timedelta(days=20),holidays=[],sourceEvidence='Synthetic calendar only; operator must review Kenya gazetted holidays for live workflow')
            export=RemittanceExport(operationId='export',creatorPin='A000000001X',amount='5.01',deductedAt=now,dueDate=calendar.deadline(now),deadlineEvidence='Section 35(5), five working days, synthetic calendar',assessmentEvidence=contract.assessmentEvidence,deductionEvidence='Synthetic posted deduction',calendar=calendar)
            preparer.withholding.prepare_remittance('receipt',export)
            reviewer.withholding.remittance_action('receipt',transition('review','REVIEW'))
            contained(lambda:reviewer.withholding.remittance_action('receipt',transition('approve','APPROVE')))
            approver.withholding.remittance_action('receipt',transition('approve','APPROVE'))
            second=receipt.model_copy(update={'eventId':'second-receipt','grossReceipts':Decimal('200.00')})
            preparer.withholding.record_receipt(second)
            preparer.withholding.record_deduction('second-receipt',WithholdingDeduction(eventId='second-deduction',occurredAt=now,evidence='Synthetic second actual deduction',amount='10.00'))
            preparer.withholding.prepare_remittance('second-receipt',export.model_copy(update={'operationId':'second-export','amount':Decimal('10.00')}))
            reviewer.withholding.remittance_action('second-receipt',transition('second-review','REVIEW'))
            approver.withholding.remittance_action('second-receipt',transition('second-approve','APPROVE'))
            batch=preparer.withholding.prepare_batch(RemittanceBatch(operationId='batch',receiptIds=['receipt','second-receipt'],evidence='Synthetic operator work manifest'))
            check('batch is a work manifest without payment',batch['state']=='OPERATOR_WORK_MANIFEST' and batch['automaticKraRemittance'] is False)
            preparer.withholding.batch_action('batch',transition('payment','RECORD_EVIDENCE','15.01','synthetic-payment'))
            preparer.withholding.batch_action('batch',transition('reconcile','RECONCILE','15.01'))
            preparer.withholding.remittance_action('receipt',transition('certificate','RECORD_CERTIFICATE',reference='synthetic-certificate'))
            preparer.withholding.remittance_action('second-receipt',transition('second-certificate','RECORD_CERTIFICATE',reference='synthetic-second-certificate'))
            check('shared payment reference allocated across two cases',preparer.withholding.get_remittance('second-receipt').suppliedPaymentEvidence==Decimal('10.00'))
            preparer.withholding.record_refund('receipt',WithholdingRefund(eventId='refund',occurredAt=now+timedelta(seconds=1),grossRefund='100.10',feeRefund='3',receiptVatRefund='0',feeVatRefund='0.48',approvalEvidence='Synthetic financial refund evidence; tax release not authorised'))
            balances=preparer.withholding.reconcile('receipt');case=preparer.withholding.get_remittance('receipt')
            check('refund retains liability and completed evidence case',balances.withholdingReviewRequired and balances.withheld==Decimal('5.01') and balances.pendingRemittance==Decimal('5.01') and case.state=='CERTIFICATE_RECORDED_UNVERIFIED')
            assessment=WithholdingRefundReview(action='ASSESS',outcome='RETAIN_DEDUCTION',assessmentEvidence='Synthetic conservative retention assessment',evidence='No tax release or completed-case reopening')
            preparer.withholding.review_refund('refund',assessment)
            contained(lambda:preparer.withholding.review_refund('refund',assessment.model_copy(update={'action':'APPROVE'})))
            approver.withholding.review_refund('refund',assessment.model_copy(update={'action':'APPROVE','evidence':'Separate operator reviewed retention only'}))
            balances=preparer.withholding.reconcile('receipt');case=preparer.withholding.get_remittance('receipt')
            check('supported refund review resolves review without tax release',not balances.withholdingReviewRequired and balances.pendingRemittance==Decimal('5.01') and case.pendingRemittance==balances.pendingRemittance)
            check('supplied certificate and payment remain unverified',balances.verifiedRemittance==0 and case.verifiedRemittance==0 and case.evidenceVerification=='SUPPLIED_UNVERIFIED' and balances.payoutStatus=='PROJECTED_NOT_PAID')
            contained(lambda:preparer.withholding.remittance_action('receipt',transition('cancel','CANCEL')))
            args.evidence_dir.joinpath('accounting.json').write_text(balances.model_dump_json(indent=2))
            args.evidence_dir.joinpath('case.json').write_text(case.model_dump_json(indent=2))
        with PlatformSessions(state['platform'],base_url=origin) as platform:
            with platform.branch_client(state['grant'],environment='SANDBOX',scopes={'withholding:read'}) as delegated:
                check('delegated branch accounting reads supported',delegated.withholding.reconcile('receipt').withheld==Decimal('5.01'))
                contained(lambda:delegated.withholding.approve_policy('content',1,approval))
        with client(state['tokens']['01'][0]) as other:
            contained(lambda:other.withholding.get_event('receipt'))
            contained(lambda:other.withholding.get_remittance('receipt'))
            contained(lambda:other.withholding.get_batch('batch'))
        args.private_manifest.write_text(json.dumps(state))
    else:
        with client(state['tokens']['00'][0]) as preparer:
            balances=preparer.withholding.reconcile('receipt');case=preparer.withholding.get_remittance('receipt')
            check('restart retains deduction, refund review and unverified certificate',balances.withheld==Decimal('5.01') and not balances.withholdingReviewRequired and case.state=='CERTIFICATE_RECORDED_UNVERIFIED')
            check('restart does not infer payment or verification',balances.verifiedRemittance==0 and balances.pendingRemittance==Decimal('5.01') and case.verifiedRemittance==0)
            check('restart retains immutable batch',preparer.withholding.get_batch('batch')['total']=='15.01' or Decimal(str(preparer.withholding.get_batch('batch')['total']))==Decimal('15.01'))
    args.evidence_dir.joinpath(args.phase+'.json').write_text(json.dumps(dict(result='PASS',checks=checks),indent=2)+'\n')

if __name__=='__main__': main()
