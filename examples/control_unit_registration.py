"""Registration and original inspection against an explicit isolated loopback manifest.

This example refuses remote identities and does not establish KRA acceptance.
Provision and initialize both native test branches before running it.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime,timezone
from decimal import Decimal
from kra_etims import KRAeTIMSClient
from kra_etims.models import ItemRegistration, BranchCustomerRegistration, ItemDetail, SaleInvoice


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-manifest",type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads(args.private_manifest.read_text())
    origin=manifest["origin"]
    if manifest.get("isolatedSimulator") is not True or urlparse(origin).hostname!="127.0.0.1":
        raise ValueError("A fresh isolated loopback manifest is required")
    for mode,branch in manifest["branches"].items():
        if mode not in {"VSCU","OSCU"} or not branch["tin"].startswith("S"):
            raise ValueError("Only explicit synthetic native protocol branches are supported")
        with KRAeTIMSClient(api_key=branch["apiKey"],base_url=origin) as client:
            status=client.device_status()
            if status["controlUnitType"]!=mode or status["status"]!="READY":
                raise ValueError("Original branch mode and readiness must match")
            reference="registration-example-"+mode
            registration=ItemRegistration(sku=reference,itemNm="Synthetic notebook",itemClsCd="5059690800",taxTyCd="B",unitPrice=Decimal("126"))
            client.register_item(registration)
            # Explicit branch master data is distinct from taxpayer lookup.
            client.register_customer(BranchCustomerRegistration(pin="A000123456B",name="Synthetic buyer"))
            line=ItemDetail(itemCd=reference,itemNm=registration.itemNm,itemClsCd=registration.itemClsCd,qty=1,uprc=Decimal("126"),dcAmt=Decimal("10"),totAmt=Decimal("116"),taxTyCd="B",taxblAmt=Decimal("100"),taxAmt=Decimal("16"))
            invoice=SaleInvoice(tin=branch["tin"],bhfId="00",invcNo=reference,confirmDt=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),pmtTyCd="01",totItemCnt=1,totAmt=Decimal("116"),totTaxblAmt=Decimal("100"),totTaxAmt=Decimal("16"),itemList=[line],custPin="A000123456B",custNm="Synthetic buyer")
            result=client.submit_sale(invoice,idempotency_key=reference)
            original=client.find_sale(operation_key=reference)
            detail=client.get_sale_original(original["purchaseId"])
            assert detail["originalTransport"] and detail["payloadKind"]=="BUSINESS_SNAPSHOT_BEFORE_PROTOCOL_ADAPTATION"
            print(json.dumps({"protocol":mode,"operation":reference,"status":original["status"],"officialAcceptance":False}))
            if result["status"]!="SIGNED":
                # Keep the original key/body for inspection and audited recovery.
                raise RuntimeError("Original outcome unresolved; do not replace or blindly resubmit")


if __name__=="__main__":main()
