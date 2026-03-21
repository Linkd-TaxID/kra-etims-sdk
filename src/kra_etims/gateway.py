"""
KRA eTIMS SDK — TaxID Links Supplier Gateway
=============================================
Wraps the backend "TaxID Links" supplier onboarding flow — the statutory
mechanism that allows buyers to claim deductions for purchases from informal
suppliers (kiosks, jua kali, market vendors) who have no eTIMS software.

Background
----------
Finance Act 2023 §16(1)(c): buyers cannot claim expense deductions unless the
purchase is supported by a valid eTIMS invoice. Informal traders rarely have
TIS software. KRA's Category 5 (Reverse Invoice) spec allows the BUYER to issue
the invoice on behalf of the supplier — but only with the supplier's explicit
consent, obtained via an SMS or WhatsApp confirmation.

Flow
----
1. Buyer calls ``onboard_supplier()`` (or ``onboard_suppliers()`` for bulk).
2. TIaaS sends the supplier an SMS/WhatsApp message with the amount and a
   confirmation token.
3. Supplier replies "YES {token}" (or "YES {KRA-PIN} {token}" if registered).
4. TIaaS raises a KRA Category 5 Reverse Invoice, signs it via the VSCU JAR,
   and persists the purchase record.
5. Buyer polls ``get_status(request_id)`` until ``status == "SIGNED"``.

Middleware endpoints (ground truth):
  POST /v2/gateway/supplier-onboarding/single  — single supplier
  POST /v2/gateway/supplier-onboarding         — bulk (list of suppliers)
  GET  /v2/gateway/supplier-onboarding/{id}/status

SDK usage (sync):
    result = client.gateway.onboard_supplier(
        phone="+254712345678",
        amount=5000,
        buyer_pin="A000123456B",
        buyer_name="Acme Superstore",
        item_description="Maize supply — March 2026",
    )
    print(result.request_id, result.status)   # 42, "PENDING"
    print(result.token)                        # "XK9T" — embedded in the SMS

    status = client.gateway.get_status(result.request_id)
    print(status.status)                       # "SIGNED" once trader confirms

SDK usage (async):
    result = await client.gateway.onboard_supplier(
        phone="+254712345678", amount=5000,
        buyer_pin="A000123456B", buyer_name="Acme Superstore",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .client import KRAeTIMSClient
    from .async_client import AsyncKRAeTIMSClient


# ---------------------------------------------------------------------------
# Input helper
# ---------------------------------------------------------------------------

@dataclass
class SupplierEntry:
    """
    A single supplier entry for bulk onboarding.

    Parameters
    ----------
    phone:
        Supplier's phone number in E.164 format (e.g. "+254712345678").
    amount:
        Amount in KES (VAT-inclusive).
    item_description:
        Optional free-text description shown in the outbound message.
    """
    phone:            str
    amount:           Union[Decimal, float, int, str]
    item_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class SupplierOnboardingResponse(BaseModel):
    """
    Response from a supplier onboarding initiation request.

    ``request_id`` is the primary key to poll with ``get_status()``.
    ``token`` is the confirmation code embedded in the outbound SMS/WhatsApp
    message — suppliers include it in their "YES {token}" reply.
    """
    request_id: int
    phone:      str
    status:     str            # always "PENDING" immediately after initiation
    token:      Optional[str] = None   # confirmation token in the outbound message
    channel:    Optional[str] = None   # "sms" | "whatsapp"
    expires_at: Optional[str] = None   # ISO-8601; request expires if unconfirmed
    raw:        Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "SupplierOnboardingResponse":
        payload = data.get("data", data)
        return cls(
            request_id=int(payload.get("requestId", 0)),
            phone=str(payload.get("phone", "")),
            status=str(payload.get("status", "PENDING")),
            token=payload.get("token"),
            channel=payload.get("channel"),
            expires_at=str(payload["expiresAt"]) if payload.get("expiresAt") else None,
            raw=payload,
        )


class SupplierGatewayStatus(BaseModel):
    """
    Status response for a supplier onboarding request.

    Status lifecycle:
      PENDING   — message sent, awaiting supplier reply
      CONFIRMED — supplier replied YES; VSCU signing in progress
      SIGNED    — KRA Category 5 invoice raised and signed; ``purchase_id`` is set
      EXPIRED   — supplier did not reply within the expiry window
      FAILED    — VSCU signing failed; see middleware logs
    """
    request_id:     int
    status:         str
    supplier_phone: Optional[str]    = None
    amount:         Optional[Decimal] = None
    channel:        Optional[str]    = None
    purchase_id:    Optional[int]    = None   # set when status == "SIGNED"
    expires_at:     Optional[str]    = None
    raw:            Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "SupplierGatewayStatus":
        payload    = data.get("data", data)
        raw_amount = payload.get("amount")
        return cls(
            request_id=int(payload.get("requestId", 0)),
            status=str(payload.get("status", "UNKNOWN")),
            supplier_phone=payload.get("supplierPhone"),
            amount=Decimal(str(raw_amount)) if raw_amount is not None else None,
            channel=payload.get("channel"),
            purchase_id=payload.get("purchaseId"),
            expires_at=str(payload["expiresAt"]) if payload.get("expiresAt") else None,
            raw=payload,
        )


class BulkOnboardingResponse(BaseModel):
    """Response from a bulk supplier onboarding request."""
    initiated: int
    failed:    int
    total:     int
    details:   List[Dict[str, Any]] = Field(default_factory=list)
    raw:       Optional[Dict[str, Any]] = Field(None, exclude=True)

    @classmethod
    def from_api(cls, data: dict) -> "BulkOnboardingResponse":
        payload = data.get("data", data)
        return cls(
            initiated=int(payload.get("initiated", 0)),
            failed=int(payload.get("failed", 0)),
            total=int(payload.get("total", 0)),
            details=payload.get("details", []),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Sync Gateway Interface
# ---------------------------------------------------------------------------

class TaxIDSupplierGateway:
    """
    Sync supplier gateway interface attached to ``KRAeTIMSClient.gateway``.

    Enables buyers to obtain KRA-compliant Category 5 (Reverse Invoice)
    receipts for purchases from informal suppliers — via an SMS/WhatsApp
    consent flow that requires no eTIMS software on the supplier's side.
    """

    def __init__(self, client: "KRAeTIMSClient") -> None:
        self._client = client

    def onboard_supplier(
        self,
        phone: str,
        amount: Union[Decimal, float, int, str],
        *,
        buyer_pin: str,
        buyer_name: str,
        item_description: Optional[str] = None,
        idempotency_key:  Optional[str] = None,
    ) -> SupplierOnboardingResponse:
        """
        Initiate a single supplier onboarding request.

        TIaaS sends the supplier an SMS or WhatsApp message. When they reply
        "YES {token}", the VSCU raises and signs a KRA Category 5 invoice.
        Poll ``get_status(result.request_id)`` for completion.

        Parameters
        ----------
        phone:
            Supplier's phone in E.164 format (e.g. "+254712345678").
        amount:
            Amount in KES (VAT-inclusive).
        buyer_pin:
            KRA PIN of the buyer company initiating the request.
        buyer_name:
            Display name shown to the supplier in the outbound message.
        item_description:
            Optional free-text description ("Maize supply — March 2026").
        idempotency_key:
            Prevent duplicate requests; safe to retry with the same key.
        """
        payload: Dict[str, Any] = {
            "buyerPin":  buyer_pin,
            "buyerName": buyer_name,
            "phone":     phone.strip(),
            "amount":    str(Decimal(str(amount))),
        }
        if item_description:
            payload["itemDescription"] = item_description

        raw = self._client._request(
            "POST",
            "/v2/gateway/supplier-onboarding/single",
            json=payload,
            idempotency_key=idempotency_key,
        )
        return SupplierOnboardingResponse.from_api(raw)

    def onboard_suppliers(
        self,
        suppliers: List[SupplierEntry],
        *,
        buyer_pin:  str,
        buyer_name: str,
    ) -> BulkOnboardingResponse:
        """
        Initiate bulk supplier onboarding for multiple suppliers at once.

        Each supplier receives a separate outbound message. Failures on
        individual suppliers do not abort the batch — check ``response.details``
        for per-supplier status.

        Parameters
        ----------
        suppliers:
            List of ``SupplierEntry`` instances.
        buyer_pin:
            KRA PIN of the buyer company.
        buyer_name:
            Display name shown to all suppliers in their outbound messages.
        """
        payload: Dict[str, Any] = {
            "buyerPin":  buyer_pin,
            "buyerName": buyer_name,
            "suppliers": [
                {
                    "phone":  s.phone.strip(),
                    "amount": str(Decimal(str(s.amount))),
                    **({"itemDescription": s.item_description}
                       if s.item_description else {}),
                }
                for s in suppliers
            ],
        }
        raw = self._client._request(
            "POST", "/v2/gateway/supplier-onboarding", json=payload
        )
        return BulkOnboardingResponse.from_api(raw)

    def get_status(self, request_id: int) -> SupplierGatewayStatus:
        """
        Poll the status of a supplier onboarding request.

        Poll until ``status == "SIGNED"`` (success) or ``"EXPIRED"/"FAILED"``.
        ``purchase_id`` is populated once VSCU signing completes.

        Parameters
        ----------
        request_id:
            The ``request_id`` from ``onboard_supplier()`` or ``onboard_suppliers()``.
        """
        raw = self._client._request(
            "GET", f"/v2/gateway/supplier-onboarding/{request_id}/status"
        )
        return SupplierGatewayStatus.from_api(raw)


# ---------------------------------------------------------------------------
# Async Gateway Interface
# ---------------------------------------------------------------------------

class AsyncTaxIDSupplierGateway:
    """Async supplier gateway interface attached to ``AsyncKRAeTIMSClient.gateway``."""

    def __init__(self, client: "AsyncKRAeTIMSClient") -> None:
        self._client = client

    async def onboard_supplier(
        self,
        phone: str,
        amount: Union[Decimal, float, int, str],
        *,
        buyer_pin:        str,
        buyer_name:       str,
        item_description: Optional[str] = None,
        idempotency_key:  Optional[str] = None,
    ) -> SupplierOnboardingResponse:
        """Async: initiate a single supplier onboarding request."""
        payload: Dict[str, Any] = {
            "buyerPin":  buyer_pin,
            "buyerName": buyer_name,
            "phone":     phone.strip(),
            "amount":    str(Decimal(str(amount))),
        }
        if item_description:
            payload["itemDescription"] = item_description

        raw = await self._client._request(
            "POST",
            "/v2/gateway/supplier-onboarding/single",
            json=payload,
            idempotency_key=idempotency_key,
        )
        return SupplierOnboardingResponse.from_api(raw)

    async def onboard_suppliers(
        self,
        suppliers: List[SupplierEntry],
        *,
        buyer_pin:  str,
        buyer_name: str,
    ) -> BulkOnboardingResponse:
        """Async: bulk supplier onboarding."""
        payload: Dict[str, Any] = {
            "buyerPin":  buyer_pin,
            "buyerName": buyer_name,
            "suppliers": [
                {
                    "phone":  s.phone.strip(),
                    "amount": str(Decimal(str(s.amount))),
                    **({"itemDescription": s.item_description}
                       if s.item_description else {}),
                }
                for s in suppliers
            ],
        }
        raw = await self._client._request(
            "POST", "/v2/gateway/supplier-onboarding", json=payload
        )
        return BulkOnboardingResponse.from_api(raw)

    async def get_status(self, request_id: int) -> SupplierGatewayStatus:
        """Async: poll a supplier onboarding request status."""
        raw = await self._client._request(
            "GET", f"/v2/gateway/supplier-onboarding/{request_id}/status"
        )
        return SupplierGatewayStatus.from_api(raw)
