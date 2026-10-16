"""Synthetic SDK/HTTP retail acceptance. Run taxID/scripts/tests/phase2-retail.py.
No real device, taxpayer submission, payment or remote acceptance is asserted.
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date
from decimal import Decimal
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
import httpx
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail, ReturnLine, CreditNoteReason, StockAdjustmentLine


def invoice(tin, branch, reference, mixed=False, name="Synthetic retail"):
    rows=[("B","3","40","20","100","13.79")]
    if mixed: rows += [(b,"1",price,"0",price,tax) for b,price,tax in [("A","10","0"),("C","20","0"),("D","30","0"),("E","108","8")]]
    lines=[]
    for band,qty,price,discount,gross,tax in rows:
        gross,tax=Decimal(gross),Decimal(tax)
        lines.append(ItemDetail(itemCd="RETAIL-"+band,itemNm=name+" "+band,itemClsCd="5059690800",qty=Decimal(qty),uprc=Decimal(price),dcAmt=Decimal(discount),totAmt=gross,taxTyCd=band,taxAmt=tax,taxblAmt=gross-tax))
    gross=sum(x.totAmt for x in lines);tax=sum(x.taxAmt for x in lines)
    return SaleInvoice(tin=tin,bhfId=branch,invcNo=reference,confirmDt=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),pmtTyCd="01",totItemCnt=len(lines),totTaxAmt=tax,totTaxblAmt=gross-tax,totAmt=gross,itemList=lines,custPin="A000123456B",custNm="Original buyer")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",choices=["first","resume"],required=True)
    parser.add_argument("--private-manifest",type=Path,required=True)
    parser.add_argument("--evidence-dir",type=Path,required=True)
    args=parser.parse_args();state=json.loads(args.private_manifest.read_text());origin=state["origin"]
    assert urlparse(origin).hostname=="127.0.0.1" and state["isolatedSimulator"] is True
    checks=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    def http(method,path,token,body=None):
        return httpx.request(method,origin+path,json=body,headers={"X-API-Key":token},timeout=30)
    def denied(action):
        try: action()
        except Exception as e:
            check("contained request returns client error",any(code in str(e) for code in ["400","403","404","409","422","425"]))
            return
        raise AssertionError("Expected containment")
    def note(client,ref,qty,reason="Partial original return"):
        return client.issue_credit_note(state["mixed"]["purchaseId"],reason=reason,return_lines=[ReturnLine(originalItemSeq=1,qty=Decimal(qty))],reason_code=CreditNoteReason.REFUND,client_reference=ref,idempotency_key=ref)
    with KRAeTIMSClient(api_key=state["operator"],base_url=origin) as operator:
        if args.phase=="first":
            state["tin"]="S000000001X";state["tokens"]={}
            for branch in ["00","01","02","03"]:
                device=operator._request("POST","/v2/admin/tenants",json=dict(tin=state["tin"],bhfId=branch,dvcSrlNo="synthetic-retail-"+branch,vscuBaseUrl=origin,environment="SANDBOX"))
                issued=operator._request("POST",f"/v2/admin/tenants/{device['id']}/api-keys",json=dict(clientName="Synthetic retail "+branch,role="ROLE_ADMIN"))
                state["tokens"][branch]=issued["apiKey"]
                with KRAeTIMSClient(api_key=issued["apiKey"],base_url=origin) as client: client.initialize_device_handshake()
            with KRAeTIMSClient(api_key=state["tokens"]["00"],base_url=origin) as client:
                state["mixed"]=client.submit_sale(invoice(state["tin"],"00","phase2-mixed",mixed=True),idempotency_key="phase2-mixed")
                check("mixed five-band discounted basket signs",state["mixed"]["status"]=="SIGNED")
                first=note(client,"phase2-partial","1");check("partial item return signs",first["status"]=="SIGNED")
                check("same-key correction is the original",note(client,"phase2-partial","1")["creditNoteId"]==first["creditNoteId"])
                def race(i):
                    try: return note(client,"phase2-race-"+str(i),"1.50")
                    except Exception as e:
                        if "remaining quantity" not in str(e): raise
                        return None
                with ThreadPoolExecutor(max_workers=2) as pool: result=list(pool.map(race,range(2)))
                check("concurrent quantity over-reversal admits exactly one",sum(x is not None for x in result)==1)
            with KRAeTIMSClient(api_key=state["tokens"]["01"],base_url=origin) as client:
                client.submit_sale(invoice(state["tin"],"01","phase2-stock-source"),idempotency_key="phase2-stock-source")
                registered=operator.prerequisites.inspect(state["tin"],"01","ITEM","RETAIL-B")[0]
                item=json.loads(registered["payload"])["itemCd"]
                state["stockLine"]=StockAdjustmentLine(itemCd=item,itemNm="Synthetic stock",ioType="A",pkgUnitCd="NT",pkgQty=Decimal("1"),qtyUnitCd="U",qty=Decimal("1"),prc=Decimal("40"),totDcAmt=Decimal("0"),taxTyCd="B").model_dump(mode="json")
                line=StockAdjustmentLine.model_validate(state["stockLine"])
                success=client.submit_stock_adjustment([line],remark="Synthetic stock success",idempotency_key="phase2-stock-success")
                check("stock success typed and central acknowledgement absent",success.status=="SYNCED" and success.kraAcknowledged is False)
                repeat=client.submit_stock_adjustment([line],remark="Synthetic stock success",idempotency_key="phase2-stock-success")
                check("repeated stock keeps original SAR",success.sarNo==repeat.sarNo)
                denied(lambda:client.submit_stock_adjustment([line],remark="Synthetic stock rejection",idempotency_key="phase2-stock-reject"))
                check("stock rejection retained",client.inspect_stock_operation("phase2-stock-reject").status=="REJECTED")
                unknown=client.submit_stock_adjustment([line],remark="Synthetic stock unknown",idempotency_key="phase2-stock-unknown")
                state["unknownStock"]=unknown.model_dump(mode="json")
                check("stock lost response is unknown",unknown.status=="OUTCOME_UNKNOWN")
                denied(lambda:client.submit_stock_adjustment([line],remark="replacement",idempotency_key="phase2-stock-replacement"))
                blocked=http("POST","/v2/reports/daily-z",state["tokens"]["01"])
                check("unknown stock blocks local close",blocked.status_code==409)
            with KRAeTIMSClient(api_key=state["tokens"]["00"],base_url=origin) as client:
                denied(lambda:client.inspect_stock_operation("phase2-stock-unknown"))
            with KRAeTIMSClient(api_key=state["tokens"]["02"],base_url=origin) as client:
                inv=invoice(state["tin"],"02","phase2-admission-race",name="Synthetic admission race")
                def submit(): return client.submit_sale(inv,idempotency_key="phase2-admission-race")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending=pool.submit(submit);marker=args.private_manifest.parent/"admission-marker"
                    deadline=time.monotonic()+20
                    while not marker.exists() and time.monotonic()<deadline:time.sleep(.1)
                    check("sale reached prerequisites before invoice preparation",marker.exists())
                    close=http("POST","/v2/reports/daily-z",state["tokens"]["02"])
                    check("admission racing local close blocks close",close.status_code==409)
                    check("admitted sale signs after prerequisite completes",pending.result(timeout=30)["status"]=="SIGNED")
                close=client.reports.get_daily_z(date.today().isoformat())
                check("settled branch closes locally without remote acknowledgement",close.vscu_acknowledged is False and close.raw["kraAcknowledged"] is False and close.raw["closureScope"]=="LOCAL_AGGREGATE")
                denied(lambda:client.submit_sale(invoice(state["tin"],"02","phase2-after-close"),idempotency_key="phase2-after-close"))
            with KRAeTIMSClient(api_key=state["tokens"]["03"],base_url=origin) as client:
                state["timeoutOriginal"]=client.submit_sale(invoice(state["tin"],"03","phase2-timeout-source"),idempotency_key="phase2-timeout-source")
                state["timeoutNote"]=client.issue_credit_note(state["timeoutOriginal"]["purchaseId"],reason="Synthetic correction timeout",return_lines=[ReturnLine(originalItemSeq=1,qty=Decimal("1"))],reason_code=CreditNoteReason.REFUND,client_reference="phase2-timeout",idempotency_key="phase2-timeout")
                check("control-unit timeout retains correction reservation",state["timeoutNote"]["status"]=="OUTCOME_UNKNOWN")
            args.private_manifest.write_text(json.dumps(state))
            # Start a correction dispatch, observe its committed intent, then let the runner kill this JVM.
            from kra_etims.models import to_middleware_sale_payload
            crash=dict(clientReference="phase2-crash",reason="Synthetic correction crash",reasonCode="06",returnLines=[dict(originalItemSeq=1,qty="0.50")])
            def crash_call():
                try:httpx.post(origin+f"/v2/etims/sale/{state['mixed']['purchaseId']}/credit-note",json=crash,headers={"X-API-Key":state["tokens"]["00"],"Idempotency-Key":"phase2-crash"},timeout=3)
                except httpx.TimeoutException:pass
            thread=threading.Thread(target=crash_call);thread.start();marker=args.private_manifest.parent/"crash-marker"
            deadline=time.monotonic()+20
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.1)
            check("correction crash boundary reached after durable reservation",marker.exists());thread.join(timeout=5)
        else:
            with KRAeTIMSClient(api_key=state["tokens"]["00"],base_url=origin) as client:
                case=client._request("GET","/v2/etims/sales/search?clientReference=phase2-crash")
                check("restart retains original uncertain correction",case["status"] in ["PENDING_SYNC","OUTCOME_UNKNOWN"])
                denied(lambda:note(client,"phase2-overafterrestart","0.01"))
                closed=http("POST","/v2/reports/daily-z",state["tokens"]["00"])
                check("uncertain correction blocks day close after restart",closed.status_code==409)
            with KRAeTIMSClient(api_key=state["tokens"]["01"],base_url=origin) as client:
                current=client.inspect_stock_operation("phase2-stock-unknown")
                check("stock restart retains identity payload and unknown outcome",current.status=="OUTCOME_UNKNOWN" and current.movementId==state["unknownStock"]["movementId"] and current.payloadSha256==state["unknownStock"]["payloadSha256"])
                case=operator.retail.inspect_stock(state["tin"],"01","phase2-stock-unknown")[0]
                snapshot=case["transport"][0]
                proof=json.dumps(dict(version=1,identitySha256=hashlib.sha256(snapshot["identity"].encode()).hexdigest(),
                    businessSha256=snapshot["business_sha256"],wireSha256=snapshot["wire_sha256"],outcome="SYNCED",
                    executionOwner=snapshot["executionOwner"],provenance="Independent synthetic protocol fixture; not KRA evidence",
                    quiescenceEvidence="Original isolated fixture JVM stopped by runner" if not snapshot["transportReturned"] else ""))
                operator.retail.resolve_stock(tin=state["tin"],branch="01",key="phase2-stock-unknown",payload_sha256=case["payload_sha256"],outcome="SYNCED",evidence=proof,reason="Synthetic original stock resolution without replay")
                check("supported resolution changes original outcome without replacement",client.inspect_stock_operation("phase2-stock-unknown").movementId==current.movementId and client.inspect_stock_operation("phase2-stock-unknown").status=="SYNCED")
                close=client.reports.get_daily_z(date.today().isoformat())
                check("resolved stock permits local close",not close.vscu_acknowledged and not close.kra_acknowledged)
            with KRAeTIMSClient(api_key=state["tokens"]["03"],base_url=origin) as client:
                retry=client.issue_credit_note(state["timeoutOriginal"]["purchaseId"],reason="Synthetic correction timeout",return_lines=[ReturnLine(originalItemSeq=1,qty=Decimal("1"))],reason_code=CreditNoteReason.REFUND,client_reference="phase2-timeout",idempotency_key="phase2-timeout")
                check("timeout retry after restart keeps original unknown correction",retry["creditNoteId"]==state["timeoutNote"]["creditNoteId"] and client.get_sale_status(retry["creditNoteId"])["status"]=="OUTCOME_UNKNOWN")
                denied(lambda:client.issue_credit_note(state["timeoutOriginal"]["purchaseId"],return_lines=[ReturnLine(originalItemSeq=1,qty=Decimal("3"))],client_reference="phase2-timeout-replacement",idempotency_key="phase2-timeout-replacement"))
            with KRAeTIMSClient(api_key=state["tokens"]["02"],base_url=origin) as client:
                denied(lambda:client.get_sale_status(state["mixed"]["purchaseId"]))
    (args.evidence_dir/(args.phase+".json")).write_text(json.dumps(dict(result="PASS",checks=checks,realKraCalls=0),indent=2)+"\n")


if __name__=="__main__":main()
