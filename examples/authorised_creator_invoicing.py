"""Caller supplies approved invoices, distinct issuer grants and stable source keys.
No production tax policy, merchant consent or KRA approval is inferred here.
"""
from kra_etims.models import SaleInvoice
from kra_etims.platform_sessions import PlatformSessions


def invoice_creator_and_fee(*, taxid_url: str, platform_secret: str,
                            creator_grant: int, platform_grant: int,
                            creator_invoice: SaleInvoice, fee_invoice: SaleInvoice,
                            creator_source_key: str, fee_source_key: str, record_result):
    if creator_grant == platform_grant or creator_invoice.tin == fee_invoice.tin:
        raise ValueError("Creator sale and platform fee require separate authorised issuers")
    if creator_source_key == fee_source_key:
        raise ValueError("Use distinct stable source keys for creator sale and fee")
    with PlatformSessions(platform_secret, base_url=taxid_url) as sessions:
        with sessions.branch_client(creator_grant, environment="SANDBOX", scopes={"sale:create"}) as creator:
            sale = creator.submit_sale(creator_invoice, idempotency_key=creator_source_key)
        record_result(creator_source_key, sale)
        # Persist each independent outcome before attempting another invoice.
        with sessions.branch_client(platform_grant, environment="SANDBOX", scopes={"sale:create"}) as platform:
            fee = platform.submit_sale(fee_invoice, idempotency_key=fee_source_key)
        record_result(fee_source_key, fee)
    return sale, fee
