"""Phase 3 accounting ledger. Simulation is not a production tax determination."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, TYPE_CHECKING
from urllib.parse import quote
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .remittance import RemittanceCase, RemittanceExport, RemittanceTransition


def _decimal_input(value):
    if isinstance(value, (float, bool)):
        raise ValueError("Use Decimal, integer or decimal string for ledger money")
    return value


Money = Annotated[Decimal, BeforeValidator(_decimal_input), Field(max_digits=18, decimal_places=2, allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
EventIdentifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$" )]
Evidence = Annotated[str, Field(min_length=1, max_length=2000)]
Treatment = Literal["SIMULATION", "UNCONFIGURED", "UNSUPPORTED"]
Category = Literal["DIGITAL_CONTENT_RESIDENT", "DIGITAL_CONTENT_NONRESIDENT_NO_PE", "MARKETPLACE_RESIDENT", "MARKETPLACE_NONRESIDENT_NO_PE", "UNRESOLVED"]
Basis = Literal["GROSS_RECEIPTS", "RECEIPTS_EXCLUDING_VAT", "RECEIPTS_EXCLUDING_VAT_AND_FEE", "UNRESOLVED"]


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def event_timezone(self):
        occurred_at = getattr(self, "occurredAt", None)
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise ValueError("Event dates require a time zone")
        return self


class WithholdingPolicy(LedgerModel):
    policyId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    version: int = Field(ge=1)
    treatment: Treatment
    category: Category
    basis: Basis
    effectiveFrom: datetime
    effectiveUntil: datetime
    decisionEvidence: Evidence

    @model_validator(mode="after")
    def validate_interval(self):
        if self.effectiveFrom.tzinfo is None or self.effectiveUntil.tzinfo is None:
            raise ValueError("Policy dates require time zones")
        if self.effectiveUntil <= self.effectiveFrom:
            raise ValueError("Policy interval must be nonempty")
        if self.treatment == "SIMULATION" and (self.category == "UNRESOLVED" or self.basis == "UNRESOLVED"):
            raise ValueError("Simulation requires an explicit category and basis")
        return self


class WithholdingReceipt(LedgerModel):
    eventId: EventIdentifier
    creatorId: Identifier
    policyId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    policyVersion: int = Field(ge=1)
    occurredAt: datetime
    grossReceipts: Money = Field(gt=0)
    platformFee: Money = Field(ge=0)
    receiptVat: Money = Field(ge=0)
    platformFeeVat: Money = Field(ge=0)
    sourceEvidence: Evidence


class WithholdingRefund(LedgerModel):
    eventId: EventIdentifier
    occurredAt: datetime
    grossRefund: Money = Field(gt=0)
    feeRefund: Money = Field(ge=0)
    receiptVatRefund: Money = Field(ge=0)
    feeVatRefund: Money = Field(ge=0)
    approvalEvidence: Evidence


class WithholdingDeduction(LedgerModel):
    eventId: EventIdentifier
    occurredAt: datetime
    evidence: Evidence


class WithholdingSnapshot(LedgerModel):
    eventId: str
    originalEventId: str | None
    kind: Literal["RECEIPT", "REFUND", "DEDUCTION"]
    creatorId: str
    occurredAt: datetime
    policy: WithholdingPolicy
    status: str
    grossReceipts: Money
    platformFee: Money
    receiptVat: Money
    platformFeeVat: Money
    calculationBasis: Money | None
    rate: Decimal | None
    calculatedWithholding: Money | None
    withheld: Money
    pendingRemittance: Money
    remitted: Money
    netPayable: Money | None
    evidence: str


class WithholdingReconciliation(LedgerModel):
    original: WithholdingSnapshot
    entries: list[WithholdingSnapshot]
    grossReceipts: Money
    platformFee: Money
    receiptVat: Money
    platformFeeVat: Money
    calculatedWithholding: Money | None
    withheld: Money
    pendingRemittance: Money
    remitted: Money
    netPayable: Money | None
    withholdingReviewRequired: bool
    remittanceStatus: Literal["NOT_IMPLEMENTED"]
    certificateStatus: Literal["NOT_IMPLEMENTED"]


class WithholdingInterface:
    def prepare_remittance(self, original_id: str, export: RemittanceExport) -> RemittanceCase:
        from .remittance import RemittanceCase
        data = self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance/export", json=export.model_dump(mode="json"))
        return RemittanceCase.model_validate(data)

    def get_remittance(self, original_id: str) -> RemittanceCase:
        from .remittance import RemittanceCase
        data = self._client._request("GET", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance")
        return RemittanceCase.model_validate(data)

    def remittance_action(self, original_id: str, transition: RemittanceTransition) -> RemittanceCase:
        from .remittance import RemittanceCase
        endpoint = "approval" if transition.action == "APPROVE" else "actions"
        data = self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance/{endpoint}", json=transition.model_dump(mode="json"))
        return RemittanceCase.model_validate(data)

    def __init__(self, client):
        self._client = client

    def create_policy(self, policy: WithholdingPolicy) -> WithholdingPolicy:
        data = self._client._request("POST", "/v2/withholding/policies", json=policy.model_dump(mode="json"))
        return WithholdingPolicy.model_validate(data)

    def record_receipt(self, receipt: WithholdingReceipt) -> WithholdingSnapshot:
        data = self._client._request("POST", "/v2/withholding/receipts", json=receipt.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    def get_event(self, event_id: str) -> WithholdingSnapshot:
        data = self._client._request("GET", f"/v2/withholding/events/{quote(event_id, safe='')}")
        return WithholdingSnapshot.model_validate(data)

    def record_refund(self, original_id: str, refund: WithholdingRefund) -> WithholdingSnapshot:
        data = self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/refunds", json=refund.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    def record_deduction(self, original_id: str, deduction: WithholdingDeduction) -> WithholdingSnapshot:
        data = self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/deductions", json=deduction.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    def reconcile(self, original_id: str) -> WithholdingReconciliation:
        data = self._client._request("GET", f"/v2/withholding/receipts/{quote(original_id, safe='')}/reconciliation")
        return WithholdingReconciliation.model_validate(data)


class AsyncWithholdingInterface:
    async def prepare_remittance(self, original_id: str, export: RemittanceExport) -> RemittanceCase:
        from .remittance import RemittanceCase
        data = await self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance/export", json=export.model_dump(mode="json"))
        return RemittanceCase.model_validate(data)

    async def get_remittance(self, original_id: str) -> RemittanceCase:
        from .remittance import RemittanceCase
        data = await self._client._request("GET", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance")
        return RemittanceCase.model_validate(data)

    async def remittance_action(self, original_id: str, transition: RemittanceTransition) -> RemittanceCase:
        from .remittance import RemittanceCase
        endpoint = "approval" if transition.action == "APPROVE" else "actions"
        data = await self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/remittance/{endpoint}", json=transition.model_dump(mode="json"))
        return RemittanceCase.model_validate(data)

    def __init__(self, client):
        self._client = client

    async def create_policy(self, policy: WithholdingPolicy) -> WithholdingPolicy:
        data = await self._client._request("POST", "/v2/withholding/policies", json=policy.model_dump(mode="json"))
        return WithholdingPolicy.model_validate(data)

    async def record_receipt(self, receipt: WithholdingReceipt) -> WithholdingSnapshot:
        data = await self._client._request("POST", "/v2/withholding/receipts", json=receipt.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    async def get_event(self, event_id: str) -> WithholdingSnapshot:
        data = await self._client._request("GET", f"/v2/withholding/events/{quote(event_id, safe='')}")
        return WithholdingSnapshot.model_validate(data)

    async def record_refund(self, original_id: str, refund: WithholdingRefund) -> WithholdingSnapshot:
        data = await self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/refunds", json=refund.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    async def record_deduction(self, original_id: str, deduction: WithholdingDeduction) -> WithholdingSnapshot:
        data = await self._client._request("POST", f"/v2/withholding/receipts/{quote(original_id, safe='')}/deductions", json=deduction.model_dump(mode="json"))
        return WithholdingSnapshot.model_validate(data)

    async def reconcile(self, original_id: str) -> WithholdingReconciliation:
        data = await self._client._request("GET", f"/v2/withholding/receipts/{quote(original_id, safe='')}/reconciliation")
        return WithholdingReconciliation.model_validate(data)
