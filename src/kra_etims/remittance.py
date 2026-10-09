"""Reviewed operator evidence; no KRA filing, payment or independent verification."""
from datetime import datetime, date, timezone
from typing import Literal
from pydantic import Field, model_validator
from .withholding import LedgerModel, Money, Evidence, EventIdentifier

Action = Literal["REVIEW", "APPROVE", "RECORD_EVIDENCE", "RECONCILE", "RECORD_CERTIFICATE", "CANCEL"]
State = Literal["EXPORTED", "REVIEWED", "APPROVED", "EVIDENCE_RECORDED", "RECONCILED_SUPPLIED_EVIDENCE", "CERTIFICATE_RECORDED_UNVERIFIED", "CANCELLED"]


class RemittanceExport(LedgerModel):
    operationId: EventIdentifier
    creatorPin: str = Field(pattern=r"^[AP][0-9]{9}[A-Z]$")
    amount: Money = Field(gt=0)
    deductedAt: datetime
    dueDate: date
    deadlineEvidence: Evidence
    assessmentEvidence: Evidence
    deductionEvidence: Evidence

    @model_validator(mode="after")
    def dates(self):
        if self.deductedAt.tzinfo is None:
            raise ValueError("Deduction date requires a time zone")
        if self.dueDate < self.deductedAt.astimezone(timezone.utc).date():
            raise ValueError("Due date predates deduction")
        return self


class RemittanceTransition(LedgerModel):
    operationId: EventIdentifier
    action: Action
    evidence: Evidence
    reference: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    attachmentReference: str | None = Field(default=None, min_length=1, max_length=1000)
    attachmentSha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    amount: Money | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def action_fields(self):
        document = self.action in {"RECORD_EVIDENCE", "RECORD_CERTIFICATE"}
        if document:
            if self.reference is None:
                raise ValueError("Document reference is required")
            if (self.attachmentReference is None) != (self.attachmentSha256 is None):
                raise ValueError("Attachment reference and SHA256 must be supplied together")
        elif any(x is not None for x in (self.reference, self.attachmentReference, self.attachmentSha256)):
            raise ValueError("Document fields apply only to evidence and certificates")
        if (self.amount is not None) != (self.action in {"RECORD_EVIDENCE", "RECONCILE"}):
            raise ValueError("Amount is required only for evidence and reconciliation")
        return self


class RemittanceAudit(LedgerModel):
    operationId: str
    action: str
    state: State
    actor: str
    recordedAt: datetime
    suppliedEvidence: RemittanceTransition | None


class RemittanceCase(LedgerModel):
    receiptId: str
    creatorId: str
    export: RemittanceExport
    preparedBy: str
    state: State
    evidenceVerification: Literal["SUPPLIED_UNVERIFIED"]
    automaticKraRemittance: Literal[False]
    withholdingReviewRequired: bool
    pendingEvidence: Money
    reconciledSuppliedEvidence: Money
    audit: list[RemittanceAudit]
