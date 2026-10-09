"""Run only against a TaxID sandbox with a branch-scoped credential."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from kra_etims import KRAeTIMSClient
from kra_etims.withholding import WithholdingPolicy, WithholdingReceipt, WithholdingDeduction


def main():
    now = datetime.now(timezone.utc)
    event_id = f"synthetic-{uuid4()}"  # persist this ID before sending in a real adapter
    with KRAeTIMSClient() as client:
        client.withholding.create_policy(WithholdingPolicy(
            policyId=event_id, version=1, treatment="SIMULATION",
            category="MARKETPLACE_RESIDENT", basis="GROSS_RECEIPTS",
            effectiveFrom=now, effectiveUntil=now + timedelta(days=1),
            decisionEvidence="Synthetic arithmetic example; Phase 1 production treatment unresolved",
        ))
        receipt = client.withholding.record_receipt(WithholdingReceipt(
            eventId=event_id, creatorId="synthetic-creator", policyId=event_id,
            policyVersion=1, occurredAt=now, grossReceipts=Decimal("1000.00"),
            platformFee=Decimal("75.00"), receiptVat=Decimal("0.00"),
            platformFeeVat=Decimal("0.00"), sourceEvidence="Synthetic receipt",
        ))
        print("Calculated only:", receipt.calculatedWithholding)
        client.withholding.record_deduction(event_id, WithholdingDeduction(
            eventId=f"{event_id}-deduction", occurredAt=now,
            evidence="Synthetic deduction accounting; no money moved",
        ))
        result = client.withholding.reconcile(event_id)
        print("Withheld:", result.withheld, "Pending:", result.pendingRemittance,
              "Remitted:", result.remitted, result.remittanceStatus)


if __name__ == "__main__":
    main()
