"""
KRA eTIMS SDK — TaxIDSupplierGateway
========================================
The gateway wraps the complex backend webhook / SMS orchestration for
reverse (supplier) invoicing via USSD / WhatsApp.

A supplier in the field — with no POS, no internet, just a feature phone —
can initiate a compliant reverse invoice by texting a USSD code.  The TIaaS
backend orchestrates the KRA eTIMS reverse-invoice flow and replies with an
SMS confirmation.

SDK interface (sync):
    result = client.gateway.request_reverse_invoice(
        phone_number="+254712345678",
        amount=5000,
        tin="P051234567X",
        bhf_id="00",
    )

SDK interface (async):
    result = await client.gateway.request_reverse_invoice(
        phone_number="+254712345678",
        amount=5000,
        tin="P051234567X",
        bhf_id="00",
    )
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, TYPE_CHECKING, Union

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .client import KRAeTIMSClient
    from .async_client import AsyncKRAeTIMSClient


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class GatewayReverseInvoiceResponse(BaseModel):
    """Typed response from a gateway reverse-invoice request."""
    request_id:    str
    phone_number:  str
    amount:        Decimal
    status:        str                       # "pending" | "processing" | "completed" | "failed"
    invoice_no:    Optional[str] = None      # Set once the eTIMS invoice is raised
    message:       Optional[str] = None      # Human-readable status message
    raw:           Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "GatewayReverseInvoiceResponse":
        payload = data.get("data", data)
        return cls(
            request_id=str(payload.get("requestId", payload.get("request_id", ""))),
            phone_number=str(payload.get("phoneNumber", payload.get("phone_number", ""))),
            amount=Decimal(str(payload.get("amount", "0"))),
            status=str(payload.get("status", "pending")),
            invoice_no=payload.get("invoiceNo") or payload.get("invoice_no"),
            message=payload.get("message"),
            raw=payload,
        )


class GatewayStatusResponse(BaseModel):
    """Typed response for polling a gateway request status."""
    request_id:  str
    status:      str
    invoice_no:  Optional[str] = None
    qr_string:   Optional[str] = None
    message:     Optional[str] = None
    raw:         Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "GatewayStatusResponse":
        payload = data.get("data", data)
        return cls(
            request_id=str(payload.get("requestId", payload.get("request_id", ""))),
            status=str(payload.get("status", "unknown")),
            invoice_no=payload.get("invoiceNo") or payload.get("invoice_no"),
            qr_string=payload.get("qrCode") or payload.get("qr_code"),
            message=payload.get("message"),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Sync Gateway Interface
# ---------------------------------------------------------------------------

class TaxIDSupplierGateway:
    """
    Sync gateway interface attached to ``KRAeTIMSClient.gateway``.

    Wraps the backend's webhook / SMS orchestration for USSD-initiated
    supplier reverse invoicing — no POS, no app, just a phone number.
    """

    def __init__(self, client: "KRAeTIMSClient") -> None:
        self._client = client

    def request_reverse_invoice(
        self,
        phone_number: str,
        amount: Union[Decimal, float, int, str],
        *,
        tin: Optional[str] = None,
        bhf_id: Optional[str] = None,
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> GatewayReverseInvoiceResponse:
        """
        Initiate a reverse (credit-note) invoice via USSD / WhatsApp.

        The TIaaS backend orchestrates the full KRA eTIMS reverse-invoice
        flow and sends an SMS confirmation to the supplier.

        Parameters
        ----------
        phone_number:
            E.164 formatted phone number of the supplier (e.g. "+254712345678").
        amount:
            Invoice amount in KES (VAT-inclusive).
        tin:
            Supplier TIN.  Defaults to the client's configured TIN.
        bhf_id:
            Branch ID.  Defaults to "00".
        description:
            Optional free-text description (appears on the SMS receipt).
        idempotency_key:
            Prevent duplicate requests; safe to retry with the same key.

        Returns
        -------
        GatewayReverseInvoiceResponse
            Contains ``request_id`` for status polling.
        """
        payload: Dict[str, Any] = {
            "phoneNumber": phone_number.strip(),
            "amount":      str(Decimal(str(amount))),
        }
        if tin:
            payload["tin"] = tin
        if bhf_id:
            payload["bhfId"] = bhf_id
        if description:
            payload["description"] = description

        raw = self._client._request(
            "POST",
            "/v2/gateway/reverse-invoice",
            json=payload,
            idempotency_key=idempotency_key,
        )
        return GatewayReverseInvoiceResponse.from_api(raw)

    def get_status(self, request_id: str) -> GatewayStatusResponse:
        """
        Poll the status of a gateway reverse-invoice request.

        Parameters
        ----------
        request_id:
            The ``request_id`` returned by ``request_reverse_invoice()``.
        """
        raw = self._client._request(
            "GET", f"/v2/gateway/reverse-invoice/{request_id}"
        )
        return GatewayStatusResponse.from_api(raw)


# ---------------------------------------------------------------------------
# Async Gateway Interface
# ---------------------------------------------------------------------------

class AsyncTaxIDSupplierGateway:
    """
    Async gateway interface attached to ``AsyncKRAeTIMSClient.gateway``.
    """

    def __init__(self, client: "AsyncKRAeTIMSClient") -> None:
        self._client = client

    async def request_reverse_invoice(
        self,
        phone_number: str,
        amount: Union[Decimal, float, int, str],
        *,
        tin: Optional[str] = None,
        bhf_id: Optional[str] = None,
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> GatewayReverseInvoiceResponse:
        """Async: initiate a reverse invoice via USSD / WhatsApp."""
        payload: Dict[str, Any] = {
            "phoneNumber": phone_number.strip(),
            "amount":      str(Decimal(str(amount))),
        }
        if tin:
            payload["tin"] = tin
        if bhf_id:
            payload["bhfId"] = bhf_id
        if description:
            payload["description"] = description

        raw = await self._client._request(
            "POST",
            "/v2/gateway/reverse-invoice",
            json=payload,
            idempotency_key=idempotency_key,
        )
        return GatewayReverseInvoiceResponse.from_api(raw)

    async def get_status(self, request_id: str) -> GatewayStatusResponse:
        """Async: poll the status of a gateway reverse-invoice request."""
        raw = await self._client._request(
            "GET", f"/v2/gateway/reverse-invoice/{request_id}"
        )
        return GatewayStatusResponse.from_api(raw)
