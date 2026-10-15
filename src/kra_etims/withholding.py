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
Treatment = Literal["ASSESSED", "SIMULATION", "UNCONFIGURED", "UNSUPPORTED"]
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


class WithholdingContract(LedgerModel):
    schemaVersion: Literal[1]
    residency: Literal["RESIDENT", "NONRESIDENT_NO_PE"]
    rate: Annotated[Decimal, BeforeValidator(_decimal_input), Field(gt=0, le=1, allow_inf_nan=False)]
    platformFeeVat: Literal["SUPPLIED_AMOUNT"]
    waivers: Literal["NONE"]
    gifts: Literal["UNSUPPORTED"]
    refunds: Literal["REVIEW_NO_TAX_RELEASE"]
    legalSources: Evidence
    assessmentEvidence: Evidence


class WithholdingRefundReview(LedgerModel):
    action: Literal["ASSESS", "APPROVE"]
    outcome: Literal["RETAIN_DEDUCTION"]
    assessmentEvidence: Evidence
    evidence: Evidence


class WithholdingPolicyApproval(LedgerModel):
    operationId: EventIdentifier
    assessmentEvidence: Evidence
    approvalEvidence: Evidence


class WithholdingPolicy(LedgerModel):
    policyId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    version: int = Field(ge=1)
    treatment: Treatment
    category: Category
    basis: Basis
    effectiveFrom: datetime
    effectiveUntil: datetime
    decisionEvidence: Evidence
    contract: WithholdingContract | None = None

    @model_validator(mode="after")
    def validate_interval(self):
        if self.effectiveFrom.tzinfo is None or self.effectiveUntil.tzinfo is None:
            raise ValueError("Policy dates require time zones")
        if self.effectiveUntil <= self.effectiveFrom:
            raise ValueError("Policy interval must be nonempty")
        if self.treatment == "SIMULATION" and (self.category == "UNRESOLVED" or self.basis == "UNRESOLVED"):
            raise ValueError("Simulation requires an explicit category and basis")
        if self.treatment == "ASSESSED":
            if self.contract is None or self.category == "UNRESOLVED" or self.basis != "GROSS_RECEIPTS":
                raise ValueError("Complete supported assessed contract required")
            resident = self.category.endswith("_RESIDENT")
            if self.contract.residency != ("RESIDENT" if resident else "NONRESIDENT_NO_PE") or self.contract.rate != Decimal("0.05" if resident else "0.20"):
                raise ValueError("Unsupported category, residency or rate")
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
    amount: Money | None = Field(default=None, gt=0)


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
    remittanceStatus: str
    certificateStatus: str
    suppliedPaymentEvidence: Money = Decimal("0")
    reconciledSuppliedEvidence: Money = Decimal("0")
    verifiedRemittance: Money = Decimal("0")
    payoutStatus: Literal["PROJECTED_NOT_PAID"] = "PROJECTED_NOT_PAID"


class WithholdingInterface:
    def get_policy(self, policy_id: str, version: int) -> WithholdingPolicy:
        data = self._client._request("GET", f"/v2/withholding/policies/{quote(policy_id, safe='')}/versions/{version}")
        return WithholdingPolicy.model_validate(data)

    def review_refund(self, refund_id: str, review: WithholdingRefundReview) -> dict:
        endpoint = "approval" if review.action == "APPROVE" else "review"
        return self._client._request("POST", f"/v2/withholding/refunds/{quote(refund_id, safe='')}/{endpoint}", json=review.model_dump(mode="json"))

    def approve_policy(self, policy_id: str, version: int, approval: WithholdingPolicyApproval) -> dict:
        return self._client._request("POST", f"/v2/withholding/policies/{quote(policy_id, safe='')}/versions/{version}/approval", json=approval.model_dump(mode="json"))

    def prepare_batch(self, batch) -> dict:
        return self._client._request("POST", "/v2/withholding/remittance-batches", json=batch.model_dump(mode="json"))

    def batch_action(self, batch_id: str, transition: RemittanceTransition) -> dict:
        if transition.action not in {"RECORD_EVIDENCE", "RECONCILE"}:
            raise ValueError("Batch supports payment evidence and reconciliation only; certificates are per case")
        return self._client._request("POST", f"/v2/withholding/remittance-batches/{quote(batch_id, safe='')}/actions", json=transition.model_dump(mode="json"))

    def get_batch(self, operation_id: str) -> dict:
        return self._client._request("GET", f"/v2/withholding/remittance-batches/{quote(operation_id, safe='')}")

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
    async def get_policy(self, policy_id: str, version: int) -> WithholdingPolicy:
        data = await self._client._request("GET", f"/v2/withholding/policies/{quote(policy_id, safe='')}/versions/{version}")
        return WithholdingPolicy.model_validate(data)

    async def review_refund(self, refund_id: str, review: WithholdingRefundReview) -> dict:
        endpoint = "approval" if review.action == "APPROVE" else "review"
        return await self._client._request("POST", f"/v2/withholding/refunds/{quote(refund_id, safe='')}/{endpoint}", json=review.model_dump(mode="json"))

    async def approve_policy(self, policy_id: str, version: int, approval: WithholdingPolicyApproval) -> dict:
        return await self._client._request("POST", f"/v2/withholding/policies/{quote(policy_id, safe='')}/versions/{version}/approval", json=approval.model_dump(mode="json"))

    async def prepare_batch(self, batch) -> dict:
        return await self._client._request("POST", "/v2/withholding/remittance-batches", json=batch.model_dump(mode="json"))

    async def batch_action(self, batch_id: str, transition: RemittanceTransition) -> dict:
        if transition.action not in {"RECORD_EVIDENCE", "RECONCILE"}:
            raise ValueError("Batch supports payment evidence and reconciliation only; certificates are per case")
        return await self._client._request("POST", f"/v2/withholding/remittance-batches/{quote(batch_id, safe='')}/actions", json=transition.model_dump(mode="json"))

    async def get_batch(self, operation_id: str) -> dict:
        return await self._client._request("GET", f"/v2/withholding/remittance-batches/{quote(operation_id, safe='')}")

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
