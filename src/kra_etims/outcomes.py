"""Validated sale outcomes; historical metadata stays explicitly unavailable."""
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class ResponseMetadata:
    http_status: int
    request_id: Optional[str] = None
    environment: Optional[str] = None
    retry_after: Optional[str] = None


class FiscalResultDict(dict):
    """The unchanged JSON dictionary with response metadata outside its wire keys."""
    def __init__(self, body: dict, metadata: ResponseMetadata):
        super().__init__(body)
        self.response_metadata = metadata


_FiscalStatus = Literal["SIGNED", "PENDING_SYNC", "OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED", "NOT_SENT", "FAILED", "SYNCED"]


# Mirrors the backend's minimum (VscuSignedReceiptDto.hasCompleteReceiptData:
# rcptNo, sdcId, signature). kraQrPayload is deliberately not required: SIGNED
# rows recorded before the protocol-evidence check can lack it.
SIGNED_EVIDENCE_FIELDS = ("cuInvoiceNumber", "sdcId", "receiptSignature")


def has_signed_evidence(body) -> bool:
    """True when a wire body is SIGNED and carries every evidence field."""
    return isinstance(body, dict) and body.get("status") == "SIGNED" and all(
        isinstance(body.get(field), str) and body[field].strip() for field in SIGNED_EVIDENCE_FIELDS
    )


def _require_signed_evidence(result):
    if result.status == "SIGNED" and not has_signed_evidence(result.model_dump(by_alias=True)):
        raise ValueError("SIGNED requires CU invoice number, device identity and receipt signature")
    return result


class SaleResult(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    purchase_id: int = Field(alias="purchaseId", gt=0)
    status: _FiscalStatus
    client_reference: Optional[str] = Field(default=None, alias="clientReference")
    request_id: Optional[str] = Field(default=None, alias="requestId")
    cu_invoice_number: Optional[str] = Field(default=None, alias="cuInvoiceNumber")
    sdc_id: Optional[str] = Field(default=None, alias="sdcId")
    receipt_signature: Optional[str] = Field(default=None, alias="receiptSignature")
    kra_qr_payload: Optional[str] = Field(default=None, alias="kraQrPayload")
    vscu_timestamp: Optional[str] = Field(default=None, alias="vscuTimestamp")
    response_metadata: Optional[ResponseMetadata] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def require_signed_evidence(self):
        return _require_signed_evidence(self)

    @classmethod
    def from_result(cls, data: dict) -> "SaleResult":
        result = cls.model_validate(data)
        result.response_metadata = getattr(data, "response_metadata", None)
        return result


class SaleStatusResult(SaleResult):
    failure_reason: Optional[str] = Field(default=None, alias="failureReason")


class CreditNoteResult(BaseModel):
    """POST /v2/etims/sale/{id}/credit-note body (backend CreditNoteResponse)."""
    model_config = ConfigDict(extra="allow", strict=True)

    credit_note_id: int = Field(alias="creditNoteId", gt=0)
    original_purchase_id: int = Field(alias="originalPurchaseId", gt=0)
    status: _FiscalStatus
    client_reference: Optional[str] = Field(default=None, alias="clientReference")
    request_id: Optional[str] = Field(default=None, alias="requestId")
    cu_invoice_number: Optional[str] = Field(default=None, alias="cuInvoiceNumber")
    sdc_id: Optional[str] = Field(default=None, alias="sdcId")
    receipt_signature: Optional[str] = Field(default=None, alias="receiptSignature")
    kra_qr_payload: Optional[str] = Field(default=None, alias="kraQrPayload")
    vscu_timestamp: Optional[str] = Field(default=None, alias="vscuTimestamp")

    @model_validator(mode="after")
    def require_signed_evidence(self):
        return _require_signed_evidence(self)


class SaleOriginalResult(BaseModel):
    """GET /v2/etims/sales/{id}/original body; inspection only, no fiscal status."""
    model_config = ConfigDict(extra="allow", strict=True)

    purchase_id: int = Field(alias="purchaseId", gt=0)
