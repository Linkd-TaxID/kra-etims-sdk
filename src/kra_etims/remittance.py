"""Reviewed operator evidence; no KRA filing, payment or independent verification."""
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import Field, model_validator
from .withholding import LedgerModel, Money, Evidence, EventIdentifier

Action = Literal["REVIEW", "APPROVE", "RECORD_EVIDENCE", "RECONCILE", "RECORD_CERTIFICATE", "CANCEL"]
State = Literal["EXPORTED", "REVIEWED", "APPROVED", "EVIDENCE_RECORDED", "RECONCILED_SUPPLIED_EVIDENCE", "CERTIFICATE_RECORDED_UNVERIFIED", "CANCELLED"]


class RemittanceCalendar(LedgerModel):
    version: Evidence
    validFrom: date
    validUntil: date
    holidays: list[date]
    sourceEvidence: Evidence

    def deadline(self, deducted_at: datetime) -> date:
        if deducted_at.tzinfo is None:
            raise ValueError("Deduction date requires a time zone")
        current = deducted_at.astimezone(ZoneInfo("Africa/Nairobi")).date()
        if current < self.validFrom or current >= self.validUntil:
            raise ValueError("Calendar does not cover deduction")
        if len(set(self.holidays)) != len(self.holidays) or any(d < self.validFrom or d > self.validUntil for d in self.holidays):
            raise ValueError("Invalid calendar holiday coverage")
        working = 0
        while working < 5:
            current += timedelta(days=1)
            if current > self.validUntil:
                raise ValueError("Calendar does not cover deadline")
            if current.weekday() < 5 and current not in self.holidays:
                working += 1
        return current


class RemittanceBatch(LedgerModel):
    operationId: EventIdentifier
    receiptIds: list[EventIdentifier] = Field(min_length=1, max_length=500)
    evidence: Evidence

    @model_validator(mode="after")
    def unique_receipts(self):
        if len(set(self.receiptIds)) != len(self.receiptIds):
            raise ValueError("Unique receipt identities required")
        return self


class RemittanceExport(LedgerModel):
    operationId: EventIdentifier
    creatorPin: str = Field(pattern=r"^[AP][0-9]{9}[A-Z]$")
    amount: Money = Field(gt=0)
    deductedAt: datetime
    dueDate: date
    deadlineEvidence: Evidence
    assessmentEvidence: Evidence
    deductionEvidence: Evidence
    calendar: RemittanceCalendar | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.deductedAt.tzinfo is None:
            raise ValueError("Deduction date requires a time zone")
        if self.dueDate < self.deductedAt.astimezone(ZoneInfo("Africa/Nairobi")).date():
            raise ValueError("Due date predates deduction")
        if self.calendar is not None and self.dueDate != self.calendar.deadline(self.deductedAt):
            raise ValueError("Due date must equal five Kenya working days after deduction")
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
    pendingRemittance: Money = 0
    suppliedPaymentEvidence: Money = 0
    verifiedRemittance: Money = 0
    deadlineVerification: str = "OPERATOR_CALENDAR_SUPPLIED_UNVERIFIED"
    audit: list[RemittanceAudit]
