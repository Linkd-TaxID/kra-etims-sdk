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


class SaleResult(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    purchase_id: int = Field(alias="purchaseId", gt=0)
    status: Literal["SIGNED", "PENDING_SYNC", "OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED", "NOT_SENT", "FAILED", "SYNCED"]
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
        if self.status == "SIGNED" and not all(
            value is not None and value.strip()
            for value in (self.cu_invoice_number, self.sdc_id, self.receipt_signature)
        ):
            raise ValueError("SIGNED requires CU invoice number, device identity and receipt signature")
        return self

    @classmethod
    def from_result(cls, data: dict) -> "SaleResult":
        result = cls.model_validate(data)
        result.response_metadata = getattr(data, "response_metadata", None)
        return result


class SaleStatusResult(SaleResult):
    failure_reason: Optional[str] = Field(default=None, alias="failureReason")
