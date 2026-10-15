"""Synthetic registration recovery via SDK -> HTTP. Run phase1-registration.py.
Uses test-only acknowledgements; never establishes KRA acceptance or tax classification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlparse, parse_qs
import httpx
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail
from kra_etims.exceptions import KRAConflictError
from kra_etims.prerequisites import original_transport_evidence


def invoice(tin, reference, lines, buyer=False):
    items=[]
    for sku, name, band, gross in lines:
        gross=Decimal(gross)
        tax=Decimal('16') if band=='B' else Decimal('0')
        items.append(ItemDetail(itemCd=sku,itemNm=name,itemClsCd='5059690800',
            qty=Decimal(1),uprc=gross,totAmt=gross,taxTyCd=band,taxAmt=tax,taxblAmt=gross-tax))
    tax=sum(i.taxAmt for i in items)
    gross=sum(i.totAmt for i in items)
    return SaleInvoice(tin=tin,bhfId='00',invcNo=reference,
        confirmDt=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),pmtTyCd='01',
        totItemCnt=len(items),totTaxAmt=tax,totTaxblAmt=gross-tax,totAmt=gross,itemList=items,
        custPin='A000123456B' if buyer else None,custNm='Synthetic buyer' if buyer else 'N/A')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['first','resume'],required=True)
    parser.add_argument('--private-manifest',type=Path,required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args()
    state=json.loads(args.private_manifest.read_text())
    origin=state['origin']
    assert urlparse(origin).hostname=='127.0.0.1' and state['isolatedSimulator'] is True
    checks=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    def call(path,body=None,token=None,browser=None):
        with httpx.Client(base_url=origin,timeout=30) as local:
            response=(browser or local).request('POST' if body is not None else 'GET',path,
                json=body,headers={'Authorization':'Bearer '+token} if token else {})
            response.raise_for_status()
            return response.json()
    def workspace(label):
        invitation=call('/v2/admin/preview/invitations',dict(email=label+'@example.test',
            persona='DEVELOPER',clientName='Synthetic registration recovery'),token=state['operator'])
        with httpx.Client(base_url=origin,headers={'Origin':origin,'X-Preview-Request':'1'},timeout=30) as browser:
            view=call('/v2/preview/redeem',{'token':parse_qs(urlparse(invitation['inviteUrl']).fragment)['token'][0]},browser=browser)
            credential=call('/v2/preview/credentials',{},browser=browser)
        check('workspace explicitly synthetic',view['kraSubmission'] is False)
        return view['tin'],credential['apiKey']
    def blocked(client, inv, key):
        try: client.submit_sale(inv,idempotency_key=key)
        except KRAConflictError: return
        raise AssertionError('Expected prerequisite conflict')
    with KRAeTIMSClient(api_key=state['operator'],base_url=origin) as operator:
        if args.phase=='first':
            tin,token=workspace('phase1-main')
            state.update(tin=tin,token=token)
            with KRAeTIMSClient(api_key=token,base_url=origin) as client:
                mixed=invoice(tin,'phase1-mixed',[(band,'Synthetic '+band,band,price)
                    for band,price in [('A','10'),('B','116'),('C','20'),('D','30')]],buyer=True)
                result=client.submit_sale(mixed,idempotency_key='phase1-mixed')
                check('mixed bands and buyer PIN sign in simulator',result['status']=='SIGNED')
                state['mixed']=result
                single=invoice(tin,'phase1-single',[('SINGLE','Synthetic single','B','116')])
                check('classified single-band invoice signs',client.submit_sale(single,idempotency_key='phase1-single')['status']=='SIGNED')
                inv=invoice(tin,'phase1-blocked',[('BLOCKED','Synthetic uncertain item','B','116')],buyer=True)
                blocked(client,inv,'phase1-blocked')
                blocked(client,inv,'phase1-blocked')
                case=operator.prerequisites.inspect(tin,'00','ITEM','BLOCKED')[0]
                check('unverified response retained without registration replay',sum(x['event_type']=='DISPATCH_INTENT' for x in case['observations'])==1 and any('999' in x['evidence'] for x in case['observations']))
                check('failed prerequisite stage is resumable',operator.prerequisites.inspect_sale(tin,'00','phase1-blocked')[0]['execution_stage']=='BLOCKED')
                # A tenant credential cannot exercise the operator recovery product.
                with httpx.Client(base_url=origin) as http:
                    denied=http.get('/v2/admin/prerequisites',params=dict(tin=tin,branch='00',kind='ITEM',key='BLOCKED'),headers={'X-API-Key':token})
                check('operator workflow rejects tenant credential',denied.status_code==403)
                rejected=invoice(tin,'phase1-rejected',[('REJECTED','Synthetic rejected item','B','116')])
                blocked(client,rejected,'phase1-rejected')
                blocked(client,rejected,'phase1-rejected')
                check('verified rejection remains terminal',operator.prerequisites.inspect(tin,'00','ITEM','REJECTED')[0]['state']=='REJECTED')
                state['blockedInvoice']=inv.model_dump(mode='json')
                crash=invoice(tin,'phase1-crash',[('CRASH','Synthetic crash item','B','116')])
                state['crashInvoice']=crash.model_dump(mode='json')
                # HTTP times out while the JVM is blocked after durable dispatch intent.
                def request_crash():
                    from kra_etims.models import to_middleware_sale_payload
                    try:
                        httpx.post(origin+'/v2/etims/sale',json=to_middleware_sale_payload(crash),
                            headers={'X-API-Key':token,'Idempotency-Key':'phase1-crash'},timeout=3)
                    except httpx.TimeoutException: pass
                thread=threading.Thread(target=request_crash)
                thread.start()
                marker=args.private_manifest.parent/'crash-marker'
                deadline=time.monotonic()+15
                while not marker.exists() and time.monotonic()<deadline: time.sleep(.1)
                check('crash boundary reached registration dispatch',marker.exists())
                check('crash request has not prepared invoice',operator.prerequisites.inspect_sale(tin,'00','phase1-crash')[0]['execution_stage']=='PREREQUISITES')
                thread.join(timeout=5)
            args.private_manifest.write_text(json.dumps(state))
        else:
            tin=state['tin']
            for key in ['BLOCKED','CRASH']:
                case=operator.prerequisites.inspect(tin,'00','ITEM',key)[0]
                operator.prerequisites.resolve(tin=tin,branch='00',kind='ITEM',key=key,
                    payload_sha256=case['payloadSha256'],outcome='REGISTERED',
                    evidence=original_transport_evidence(case['originalTransport'][0],outcome='REGISTERED',provenance='Synthetic fixture registration confirmation',quiescence_evidence='Original isolated JVM terminated by the runner; source execution owner retained') if case.get('originalTransport') else 'Synthetic fixture registration confirmation, bound to payload '+case['payloadSha256'],
                    reason='Synthetic operator recovery acceptance; no KRA verification claimed')
            crash=operator.prerequisites.inspect_sale(tin,'00','phase1-crash')[0]
            operator.prerequisites.release_sale(tin=tin,branch='00',key='phase1-crash',request_hash=crash['request_hash'],
                evidence='Fresh JVM after SIGKILL; stored stage precedes invoice preparation',reason='Synthetic restart recovery')
            with KRAeTIMSClient(api_key=state['token'],base_url=origin) as client:
                inv=SaleInvoice.model_validate(state['blockedInvoice'])
                # Exact original key/body across a fresh JVM; concurrent callers cannot prepare duplicates.
                def resume(_):
                    try: return client.submit_sale(inv,idempotency_key='phase1-blocked')
                    except KRAConflictError: return None
                    except Exception as e:
                        if '425' in str(e): return None
                        raise
                with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(resume,range(4)))
                result=client.submit_sale(inv,idempotency_key='phase1-blocked')
                check('same-key blocked sale resumes after restart',result['status']=='SIGNED')
                check('concurrent resumptions retain one purchase',all(x is None or x['purchaseId']==result['purchaseId'] for x in results))
                state['resumed']=result
                result=client.submit_sale(SaleInvoice.model_validate(state['crashInvoice']),idempotency_key='phase1-crash')
                check('pre-signing crash resumes original key after operator fence',result['status']=='SIGNED')
                check('original mixed receipt survives restart',client.get_sale_status(state['mixed']['purchaseId'])['cuInvoiceNumber']==state['mixed']['cuInvoiceNumber'])
                # Prepared requests cannot be released as never-signed work.
                sale=operator.prerequisites.inspect_sale(tin,'00','phase1-crash')[0]
                try:
                    operator.prerequisites.release_sale(tin=tin,branch='00',key='phase1-crash',request_hash=sale['request_hash'],evidence='synthetic',reason='must reject')
                except KRAConflictError: check('prepared invoice cannot enter pre-signing resume',True)
                else: raise AssertionError('Prepared invoice release must fail')
            args.private_manifest.write_text(json.dumps(state))
    (args.evidence_dir/(args.phase+'.json')).write_text(json.dumps(dict(environment='SIMULATED',realKraCalls=0,checks=checks),indent=2)+'\n')
    print('Registration recovery SDK '+args.phase+' checks passed')

if __name__=='__main__': main()
