"""Synthetic marketplace probe for the isolated middleware runner; never targets a public URL.
Run scripts/tests/marketplace-demo.py in the middleware repository for setup/restart/cleanup.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import httpx
from kra_etims.client import KRAeTIMSClient
from kra_etims.models import SaleInvoice, ItemDetail
from kra_etims.platform_sessions import PlatformSessions
from kra_etims.withholding import WithholdingPolicy, WithholdingReceipt, WithholdingDeduction, WithholdingRefund
from kra_etims.remittance import RemittanceExport, RemittanceTransition


def invoice(tin, reference, gross, name):
    gross = Decimal(gross)
    vat = (gross * Decimal("0.16") / Decimal("1.16")).quantize(Decimal("0.01"))
    line = ItemDetail(itemCd="SYNTHETIC", itemNm=name, qty=Decimal(1), uprc=gross,
                      totAmt=gross, taxTyCd="B", taxblAmt=gross-vat, taxAmt=vat)
    return SaleInvoice(tin=tin, bhfId="00", invcNo=reference,
        confirmDt=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        pmtTyCd="06", totItemCnt=1, totTaxblAmt=gross-vat, totTaxAmt=vat,
        totAmt=gross, itemList=[line])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["first", "resume"], required=True)
    parser.add_argument("--private-manifest", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.private_manifest.read_text())
    origin = state["origin"]
    parsed = urlparse(origin)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and state["isolatedSimulator"] is True
    evidence = args.evidence_dir
    evidence.mkdir(exist_ok=True)
    checks = []

    def check(name, assertion, detail=None):
        if not assertion:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True, "detail": detail})

    def call(path, body=None, *, token=None, browser=None, expected=None):
        http = browser or httpx.Client(base_url=origin, timeout=30)
        headers = {"Authorization": "Bearer "+token} if token else {}
        response = http.request("POST" if body is not None else "GET", path, json=body, headers=headers)
        if browser is None:
            http.close()
        if expected is not None:
            check("HTTP "+path, response.status_code == expected, response.status_code)
        return response

    def save(name, data):
        (evidence/name).write_text(json.dumps(data, indent=2, default=str)+"\n")

    operator = state["operator"]
    if args.phase == "resume":
        with KRAeTIMSClient(api_key=state["payerToken"], base_url=origin) as payer:
            original = state["sale"]
            with KRAeTIMSClient(api_key=state["creatorBranchKey"], base_url=origin) as creator:
                restored = creator.get_sale_status(original["purchaseId"])
                check("original sale survives SIGKILL/JVM restart", restored["cuInvoiceNumber"] == original["cuInvoiceNumber"])
            ledger = payer.withholding.reconcile("product-payment")
            check("withholding/refund snapshots survive restart", ledger.withholdingReviewRequired and ledger.withheld == Decimal("50"))
            case = payer.withholding.get_remittance("product-payment")
            check("operator audit survives restart", len(case.audit) == 6 and case.withholdingReviewRequired)
            check("documents remain supplied and unverified", case.evidenceVerification == "SUPPLIED_UNVERIFIED" and case.automaticKraRemittance is False)
            before = payer.get_sale_status(state["lastFee"]["purchaseId"])
            after = payer.submit_sale(invoice(state["payerTin"], "after-restart-fee", "11.60", "Synthetic fee after restart"), idempotency_key="after-restart-fee")
            number = lambda value: int(value["cuInvoiceNumber"].split("/")[1].split()[0])
            check("durable simulator receipt sequence continues", number(after) == number(before)+1)
            with KRAeTIMSClient(api_key=state["timeoutToken"], base_url=origin) as other:
                unknown = other.get_sale_status(state["timeoutPurchaseId"])
                check("ambiguous sale remains quarantined after restart", unknown["status"] in {"OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED"})
            save("resume.json", {"environment": "SIMULATED", "realKraCalls": 0, "checks": checks, "case": case.model_dump(mode="json"), "newSimulatedReceipt": after})
        call("/v2/etims/sales/"+str(state["sale"]["purchaseId"])+"/status", token=state["revokedToken"], expected=401)
        save("restart-checks.json", checks)
        return

    def workspace(label):
        invitation = call("/v2/admin/preview/invitations", {"email":label+"@example.test", "persona":"DEVELOPER", "clientName":"Synthetic marketplace "+label}, token=operator, expected=201).json()
        browser = httpx.Client(base_url=origin, headers={"Origin":origin,"X-Preview-Request":"1"}, timeout=30)
        token = parse_qs(urlparse(invitation["inviteUrl"]).fragment)["token"][0]
        view = call("/v2/preview/redeem", {"token":token}, browser=browser, expected=200).json()
        credential = call("/v2/preview/credentials", {}, browser=browser, expected=200).json()
        check(label+" simulator identity", view["kraSubmission"] is False and view["environment"] == "SIMULATED")
        return browser, view["tin"], credential["apiKey"]

    creator_browser, creator_tin, creator_key = workspace("creator-one")
    other_browser, other_tin, other_key = workspace("creator-two")
    payer_browser, payer_tin, payer_key = workspace("platform-payer")
    platform = call("/v2/admin/platforms", {"name":"Synthetic marketplace","requestsPerMinute":500}, token=operator, expected=201).json()
    fiscal_scopes = {"sale:create","sale:read","credit-note:create"}
    def grant(tin, scopes):
        return call(f"/v2/admin/platforms/{platform['platformId']}/grants", {"tin":tin,"bhfId":"00","environment":"SANDBOX","scopes":sorted(scopes),"requestsPerMinute":100}, token=operator, expected=201).json()["grantId"]
    creator_grant = grant(creator_tin, fiscal_scopes)
    payer_grant = grant(payer_tin, fiscal_scopes | {"withholding:read","withholding:write","withholding:approve"})
    with PlatformSessions(platform["platformSecret"], base_url=origin) as sessions:
        creator_session = sessions.session(creator_grant, environment="SANDBOX", scopes=fiscal_scopes)
        with sessions.branch_client(creator_grant, environment="SANDBOX", scopes=fiscal_scopes) as creator, KRAeTIMSClient(api_key=payer_key, base_url=origin) as payer:
            sale_invoice = invoice(creator_tin, "creator-digital-sale", "1000", "Synthetic digital ebook")
            sale = creator.submit_sale(sale_invoice, idempotency_key="creator-digital-sale")
            check("creator product sale signed by simulator", sale["status"] == "SIGNED" and sale["sdcId"].startswith("SIM"), sale["purchaseId"])
            retry = creator.submit_sale(sale_invoice, idempotency_key="creator-digital-sale")
            check("duplicate sale returns original", retry["purchaseId"] == sale["purchaseId"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                duplicates = list(pool.map(lambda _: creator.submit_sale(sale_invoice, idempotency_key="creator-digital-sale"), range(2)))
            check("concurrent sale retries return original", all(r["purchaseId"] == sale["purchaseId"] for r in duplicates))
            fee = payer.submit_sale(invoice(payer_tin, "platform-fee", "75", "Synthetic marketplace platform fee"), idempotency_key="platform-fee")
            check("fee and creator invoices have separate issuers", fee["status"] == "SIGNED" and fee["sdcId"] != sale["sdcId"])
            call(f"/v2/etims/sales/{sale['purchaseId']}/status", token=other_key, expected=404)
            call("/v2/etims/sale", {"supplierPin":payer_tin,"amount":"10","invoiceDate":datetime.now().date().isoformat(),"clientReference":"wrong-creator"}, token=creator_session.accessToken.get_secret_value(), expected=403)
            call("/v2/etims/sale", {}, token=platform["platformSecret"], expected=403)

            now = datetime.now(timezone.utc)
            policy = WithholdingPolicy(policyId="fixture",version=1,treatment="SIMULATION",category="MARKETPLACE_RESIDENT",basis="GROSS_RECEIPTS",effectiveFrom=now-timedelta(days=1),effectiveUntil=now+timedelta(days=1),decisionEvidence="Synthetic 5% arithmetic fixture; no accepted business tax determination")
            payer.withholding.create_policy(policy)
            receipt = WithholdingReceipt(eventId="product-payment",creatorId="creator-one",policyId="fixture",policyVersion=1,occurredAt=now,grossReceipts="1000",platformFee="64.66",receiptVat="137.93",platformFeeVat="10.34",sourceEvidence=f"SIMULATED payment; creator sale {sale['purchaseId']}; fee invoice {fee['purchaseId']}; assumes fee VAT-inclusive; no provider payment")
            calculated = payer.withholding.record_receipt(receipt)
            check("supported simulation withholding and payout projection", calculated.calculatedWithholding == Decimal("50") and calculated.netPayable == Decimal("875"))
            with ThreadPoolExecutor(max_workers=2) as pool:
                duplicates = list(pool.map(lambda _: payer.withholding.record_receipt(receipt),range(2)))
            check("concurrent accounting events deduplicate", all(r == calculated for r in duplicates))
            payer.withholding.record_deduction("product-payment", WithholdingDeduction(eventId="deduction",occurredAt=now,evidence="SIMULATED deduction only; no funds moved"))
            balance = payer.withholding.reconcile("product-payment")
            check("actual simulated deduction in payout breakdown", balance.withheld == Decimal("50") and balance.netPayable == Decimal("875") and balance.remitted == 0)
            save("payout.json", {"simulation":True,"gross":"1000.00","feeExVat":"64.66","feeVat":"10.34","feeTotal":"75.00","receiptVat":"137.93","simulatedWithholding":"50.00","projectedPayout":"875.00","payoutExecuted":False,"assumptions":["Standard VAT band B fixture","7.5% fee assumed VAT-inclusive","waiver already exhausted","5% gross-basis withholding arithmetic only"],"snapshot":calculated.model_dump(mode="json")})

            export = RemittanceExport(operationId="export",creatorPin="A000000001X",amount="50",deductedAt=now,dueDate=(now+timedelta(days=10)).date(),deadlineEvidence="Synthetic supplied date, not statutory calculation",assessmentEvidence="Synthetic fixture dossier A000000001X mapped to simulator creator-one; not independently verified",deductionEvidence="Synthetic deduction reference")
            case = payer.withholding.prepare_remittance("product-payment", export)
            payer.withholding.remittance_action("product-payment", RemittanceTransition(operationId="review",action="REVIEW",evidence="Synthetic operator reviewed export"))
            # Distinct sandbox credentials demonstrate separation; production requires distinct active operator subjects.
            with sessions.branch_client(payer_grant, environment="SANDBOX", scopes={"withholding:approve"}) as approver:
                approver.withholding.remittance_action("product-payment", RemittanceTransition(operationId="approval",action="APPROVE",evidence="Synthetic independent sandbox approval"))
            payer.withholding.remittance_action("product-payment", RemittanceTransition(operationId="payment-evidence",action="RECORD_EVIDENCE",evidence="SIMULATED remittance document supplied by operator; no money moved",reference="SYNTHETIC-PAYMENT",amount="50"))
            payer.withholding.remittance_action("product-payment", RemittanceTransition(operationId="reconciliation",action="RECONCILE",evidence="Synthetic assessment, payer, withholdee, period, acknowledgement and amount matched; not KRA verification",amount="50"))
            certificate = RemittanceTransition(operationId="certificate",action="RECORD_CERTIFICATE",evidence="Synthetic certificate reference, not KRA issuance",reference="SYNTHETIC-CERTIFICATE",attachmentReference="synthetic-vault:certificate",attachmentSha256="a"*64)
            case = payer.withholding.remittance_action("product-payment", certificate)
            check("certificate supplied evidence never automatic remittance", not case.automaticKraRemittance and case.state == "CERTIFICATE_RECORDED_UNVERIFIED")
            check("duplicate certificate adds no audit", len(payer.withholding.remittance_action("product-payment",certificate).audit) == 6)
            save("remittance.json",case.model_dump(mode="json"))

            gift_policy = WithholdingPolicy(policyId="gift",version=1,treatment="UNSUPPORTED",category="UNRESOLVED",basis="UNRESOLVED",effectiveFrom=now-timedelta(days=1),effectiveUntil=now+timedelta(days=1),decisionEvidence="True gift versus monetisation unresolved; no production exemption inferred")
            payer.withholding.create_policy(gift_policy)
            gift = payer.withholding.record_receipt(WithholdingReceipt(eventId="gift-payment",creatorId="creator-one",policyId="gift",policyVersion=1,occurredAt=now,grossReceipts="100",platformFee="10",receiptVat="0",platformFeeVat="0",sourceEvidence="Synthetic no-benefit gift; fee VAT also unresolved; no fiscal submission"))
            check("unresolved gift is not treated as zero tax", gift.status == "UNSUPPORTED" and gift.netPayable is None and gift.calculatedWithholding is None)
            save("gift.json",{"invoiceSubmitted":False,"paymentExecuted":False,"gift":gift.model_dump(mode="json")})

            # Full flat corrections only: no itemised/partial correction support is invented.
            credit = creator.issue_credit_note(sale["purchaseId"],reason="Synthetic full refund",client_reference="creator-refund-credit",idempotency_key="creator-refund-credit")
            fee_credit = payer.issue_credit_note(fee["purchaseId"],reason="Synthetic full fee refund",client_reference="fee-refund-credit",idempotency_key="fee-refund-credit")
            check("separate creator and fee fiscal corrections sign", credit["status"] == fee_credit["status"] == "SIGNED")
            credit_retry = creator.issue_credit_note(sale["purchaseId"],reason="Synthetic full refund",client_reference="creator-refund-credit",idempotency_key="creator-refund-credit")
            check("duplicate correction retains original", credit_retry["creditNoteId"] == credit["creditNoteId"])
            refund = WithholdingRefund(eventId="product-refund",occurredAt=now+timedelta(seconds=1),grossRefund="1000",feeRefund="64.66",receiptVatRefund="137.93",feeVatRefund="10.34",approvalEvidence="SIMULATED provider refund and independent fee refund; no provider API; linked two simulated credit notes")
            payer.withholding.record_refund("product-payment",refund)
            check("duplicate refund adds no entry", payer.withholding.record_refund("product-payment",refund).eventId == refund.eventId)
            corrected = payer.withholding.reconcile("product-payment")
            check("refund retains withheld liability pending external review", corrected.withheld == Decimal("50") and corrected.netPayable == Decimal("-50") and corrected.withholdingReviewRequired)
            save("refund.json", {"providerRefundExecuted":False,"creatorCredit":credit,"feeCredit":fee_credit,"accounting":corrected.model_dump(mode="json")})
            state.update(sale=sale,lastFee={**fee_credit,"purchaseId":fee_credit["creditNoteId"]},payerTin=payer_tin,payerToken=payer_key,creatorBranchKey=creator_key,revokedToken=creator_session.accessToken.get_secret_value())
            call(f"/v2/admin/platforms/{platform['platformId']}/grants/{creator_grant}/revoke", {},token=operator,expected=204)
            call(f"/v2/etims/sales/{sale['purchaseId']}/status",token=state["revokedToken"],expected=401)
            call("/v2/platform/branch-sessions",{"grantId":creator_grant,"scopes":["sale:create"]},token=platform["platformSecret"],expected=403)
            check("other issuer remains authorised after creator consent revocation", payer.get_sale_status(fee["purchaseId"])["status"] == "SIGNED")

    unknown_response = call("/v2/preview/sample-sales",{"clientReference":"timeout-original","scenario":"AMBIGUOUS_TIMEOUT_AFTER_SIGN","amount":"116"},browser=other_browser)
    found = call("/v2/preview/sales/search?clientReference=timeout-original",browser=other_browser,expected=200).json()
    check("actual delayed simulator response quarantines fiscal outcome",found["status"] in {"OUTCOME_UNKNOWN","RECONCILIATION_REQUIRED"},unknown_response.status_code)
    observation = call("/v2/preview/failures/last",browser=other_browser,expected=200).json()
    check("simulator issued a receipt before response loss",observation["observation"]["simulatedReceiptIssued"] is True)
    duplicate = call("/v2/preview/sample-sales",{"clientReference":"timeout-original","scenario":"AMBIGUOUS_TIMEOUT_AFTER_SIGN","amount":"116"},browser=other_browser,expected=200).json()
    check("ambiguous retry returns original without dispatch",duplicate["purchaseId"] == found["purchaseId"])
    save("timeout.json",{"environment":"SIMULATED","httpStatus":unknown_response.status_code,"original":found,"observation":observation,"retry":duplicate})
    state.update(timeoutToken=other_key,timeoutPurchaseId=found["purchaseId"])
    args.private_manifest.write_text(json.dumps(state))
    save("first.json",{"environment":"SIMULATED","realKraCalls":0,"checks":checks,"creatorTin":creator_tin,"payerTin":payer_tin,"sale":sale,"fee":fee})
    for browser in [creator_browser,other_browser,payer_browser]:
        browser.close()


if __name__ == "__main__":
    main()
