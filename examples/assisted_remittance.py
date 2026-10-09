"""Synthetic request construction only; no payment, filing or certificate is executed."""
from datetime import datetime, date, timezone
from decimal import Decimal
from kra_etims.remittance import RemittanceExport, RemittanceTransition


def synthetic_requests():
    export = RemittanceExport(operationId="synthetic-export", creatorPin="A000000001X",
        amount=Decimal("5.00"), deductedAt=datetime(2026, 10, 9, tzinfo=timezone.utc),
        dueDate=date(2026, 10, 16), deadlineEvidence="Synthetic operator holiday review",
        assessmentEvidence="Synthetic assessment fixture; not an approved production tax policy",
        deductionEvidence="Synthetic deduction reference; not a real deduction")
    review = RemittanceTransition(operationId="synthetic-review", action="REVIEW", evidence="Synthetic reviewed export")
    approval = RemittanceTransition(operationId="synthetic-approval", action="APPROVE", evidence="Synthetic independent approval")
    return export, review, approval

# Submit through separate authenticated branch operators in the documented order.
# A later payment/certificate document remains supplied, unverified evidence.
