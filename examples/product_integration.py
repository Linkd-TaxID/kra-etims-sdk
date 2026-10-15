"""Both native synthetic adapters, distinct seller/fee grants, real TaxID HTTP.
Run through taxID/scripts/tests/phase-d-product.py. No KRA validation or payment.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlparse
from kra_etims import KRAeTIMSClient, AsyncKRAeTIMSClient
from kra_etims.models import (SaleInvoice, ItemDetail, ItemRegistration,
    BranchCustomerRegistration, StockAdjustmentLine, StockAdjustmentRequest, StockMasterRequest)
from kra_etims.platform_sessions import PlatformSessions, AsyncPlatformSessions


def invoice(tin, reference, sku, amount, tax):
    total=Decimal(amount);vat=Decimal(tax)
    return SaleInvoice(tin=tin,bhfId='00',invcNo=reference,
        confirmDt=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),
        totItemCnt=1,totAmt=total,totTaxAmt=vat,totTaxblAmt=total-vat,
        itemList=[ItemDetail(itemCd=sku,itemNm='Synthetic product',itemClsCd='5059690800',
            qty=Decimal('1'),uprc=total,totAmt=total,taxAmt=vat,taxblAmt=total-vat,taxTyCd='B')])


def run(state):
    origin=state['origin']
    if urlparse(origin).hostname!='127.0.0.1' or state.get('isolatedSimulator') is not True:
        raise ValueError('Fresh isolated loopback manifest required')
    result={}
    for mode, branch in state['branches'].items():
        assert branch['sellerGrant']!=branch['feeGrant'] and branch['sellerTin']!=branch['feeTin']
        with PlatformSessions(state['platform'],base_url=origin) as sessions:
            with sessions.branch_client(branch['sellerGrant'],environment='SANDBOX',scopes=set(branch['sellerScopes'])) as seller:
                assert seller.initialize_branch()['status']=='READY'
                assert seller.capabilities()['controlUnitType']==mode
                assert seller.capabilities()['supportScope']=='LOCAL_ENGINEERING_REQUIRES_DEVICE_ACCEPTANCE'
                for kind in ('CODES','CLASSIFICATIONS','NOTICES'):seller.metadata(kind)
                customer=seller.register_customer(BranchCustomerRegistration(pin='A000123456B',name='Synthetic buyer'))
                assert seller.lookup_customer('A000123456B')['status']=='FOUND'
                catalog=seller.register_item(ItemRegistration(sku='product',itemNm='Synthetic product',itemClsCd='5059690800',taxTyCd='B',unitPrice=Decimal('116')))
                sale_invoice=invoice(branch['sellerTin'],'seller-sale-'+mode,'product','116','16')
                sale=seller.submit_sale(sale_invoice,idempotency_key='seller-sale-'+mode)
                assert sale['status']=='SIGNED'
                duplicate=seller.submit_sale(sale_invoice,idempotency_key='seller-sale-'+mode)
                assert duplicate['purchaseId']==sale['purchaseId']
                assert seller.find_sale(operation_key='seller-sale-'+mode)['purchaseId']==sale['purchaseId']
                original=seller.get_sale_original(sale['purchaseId'])
                correction=seller.issue_credit_note(sale['purchaseId'],reason='Synthetic full return',reason_code='06',client_reference='return-'+mode,idempotency_key='return-'+mode)
                assert correction['status']=='SIGNED'
                movement=StockAdjustmentRequest(lines=[StockAdjustmentLine(itemCd=catalog['itemCd'],itemNm='Synthetic product',ioType='A',qty=Decimal('1'),prc=Decimal('116'),taxTyCd='B')])
                stock=seller.submit_stock_adjustment(movement.lines,idempotency_key='product-stock-'+mode)
                assert stock.status=='SYNCED'
                master=seller.submit_stock_master(StockMasterRequest(originalIoKey='product-stock-'+mode,itemCd=catalog['itemCd'],rsdQty=Decimal('10.25')),idempotency_key='product-master-'+mode)
                assert master.status=='SYNCED'
                report=seller.reports.get_x_report(datetime.now(timezone.utc).date().isoformat())
            with sessions.branch_client(branch['feeGrant'],environment='SANDBOX',scopes=set(branch['feeScopes'])) as fee:
                assert fee.initialize_branch()['status']=='READY'
                fee_invoice=invoice(branch['feeTin'],'platform-fee-'+mode,'fee','11.60','1.60')
                fee_sale=fee.submit_sale(fee_invoice,idempotency_key='platform-fee-'+mode)
                assert fee_sale['status']=='SIGNED'
                try:fee.find_sale(operation_key='seller-sale-'+mode)
                except Exception:pass
                else:raise AssertionError('Fee issuer read seller original')
            # Each invoice outcome is retained independently; separate local grants are not KRA consent.
            result[mode]={'sellerSale':sale,'platformFee':fee_sale,'correction':correction,
                'stock':stock.model_dump(mode='json'),'master':master.model_dump(mode='json'),
                'sellerTin':branch['sellerTin'],'feeTin':branch['feeTin'],'localGrantSeparation':True,
                'realIssuerAuthorizationProven':False,'kraAccepted':False,'paymentsExecuted':False}
    async def parity():
        for mode, branch in state['branches'].items():
            async with AsyncPlatformSessions(state['platform'],base_url=origin) as sessions:
                async with await sessions.branch_client(branch['sellerGrant'],environment='SANDBOX',scopes=set(branch['sellerScopes'])) as seller:
                    assert (await seller.initialize_branch())['status']=='READY'
                    assert (await seller.capabilities())['controlUnitType']==mode
                    await seller.device_status();await seller.device_health();await seller.device_monitoring()
                    for kind in ('CODES','CLASSIFICATIONS','NOTICES'):await seller.metadata(kind)
                    await seller.register_customer(BranchCustomerRegistration(pin='A000123456B',name='Synthetic buyer'))
                    await seller.lookup_pin('A000123456B')
                    catalog=await seller.register_item(ItemRegistration(sku='product',itemNm='Synthetic product',itemClsCd='5059690800',taxTyCd='B',unitPrice=Decimal('116')))
                    same=await seller.submit_sale(invoice(branch['sellerTin'],'seller-sale-'+mode,'product','116','16'),idempotency_key='seller-sale-'+mode)
                    assert same['purchaseId']==result[mode]['sellerSale']['purchaseId']
                    await seller.get_sale_original(same['purchaseId'])
                    await seller.find_sale(operation_key='seller-sale-'+mode)
                    credit=await seller.issue_credit_note(same['purchaseId'],reason='Synthetic full return',reason_code='06',client_reference='return-'+mode,idempotency_key='return-'+mode)
                    assert credit['creditNoteId']==result[mode]['correction']['creditNoteId']
                    stock=await seller.inspect_stock_operation('product-stock-'+mode)
                    assert stock.status=='SYNCED'
                    duplicate_stock=await seller.submit_stock_adjustment([StockAdjustmentLine(itemCd=catalog['itemCd'],itemNm='Synthetic product',ioType='A',qty=Decimal('1'),prc=Decimal('116'),taxTyCd='B')],idempotency_key='product-stock-'+mode)
                    assert duplicate_stock.sarNo==stock.sarNo
                    duplicate_master=await seller.submit_stock_master(StockMasterRequest(originalIoKey='product-stock-'+mode,itemCd=catalog['itemCd'],rsdQty=Decimal('10.25')),idempotency_key='product-master-'+mode)
                    assert duplicate_master.status=='SYNCED'
                    await seller.inspect_stock_master('product-master-'+mode)
                    await seller.reports.get_x_report(datetime.now(timezone.utc).date().isoformat())
                    closed=await seller.reports.get_daily_z(datetime.now(timezone.utc).date().isoformat())
                    assert not closed.kra_acknowledged and closed.closure_scope=='LOCAL_AGGREGATE'
            result[mode]['syncAsyncParity']=True
    asyncio.run(parity())
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--private-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.write_text(json.dumps(run(json.loads(args.private_manifest.read_text())),indent=2)+'\n')
