"""TaxID Links supplier outreach and consent records.

Consent does not establish a fiscal invoice. Dedicated KRA reverse-invoicing
integration remains unavailable even with the server feature flag enabled.
Status responses expose failure_reason and fiscal_submission_available;
CONFIRMED means consent recorded, not signing in progress. Do not poll for an
invoice when fiscal_submission_available is false. Legacy responses omitting
that field leave availability unknown, not approved.

Routes: POST /v2/gateway/supplier-onboarding/single, POST
/v2/gateway/supplier-onboarding, GET
/v2/gateway/supplier-onboarding/{id}/status.
"""

from __future__ import annotations

import asyncio
import time
import random
import uuid
import warnings
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

from pydantic import BaseModel, Field

from .exceptions import OSCUUnavailableError, TIaaSUnavailableError

if TYPE_CHECKING:
    from .client import KRAeTIMSClient
    from .async_client import AsyncKRAeTIMSClient

# Maximum number of retry attempts (3 retries = 4 total attempts).
_MAX_RETRIES = 3

# Backoff cap in seconds doubles per attempt: 0.5s, 1.0s, 2.0s (full jitter).
_BASE_DELAY = 0.5


def _is_retryable(exc: Exception, method: str, idempotency_key: Optional[str]) -> bool:
    """
    Retry only what cannot have produced a side effect.

    TIaaSUnavailableError means the request never left the client (or was a
    read). An OSCU 503 is a server-declared transient failure, retried for a
    mutation only under an idempotency key. The VSCU 24h ceiling
    (KRAConnectivityTimeoutError) lasts hours and is never retried here, nor
    is TIaaSAmbiguousStateError, which needs reconciliation, not a resend.
    """
    if isinstance(exc, TIaaSUnavailableError):
        return True
    if isinstance(exc, OSCUUnavailableError):
        return method.upper() == "GET" or idempotency_key is not None
    return False


def _backoff(attempt: int) -> float:
    # Full jitter de-correlates many POS terminals recovering from one outage.
    return random.uniform(0, _BASE_DELAY * (2 ** attempt))


def _money(amount: Union[Decimal, float, int, str]) -> str:
    if isinstance(amount, float):
        warnings.warn(
            "Passing a float amount is deprecated; pass Decimal or str. "
            f"{amount!r} was rounded to cents.",
            DeprecationWarning,
            stacklevel=3,
        )
        return str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return str(Decimal(str(amount)))


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
      CONFIRMED — supplier consent recorded; fiscal submission may remain gated
      SIGNED    — historical signed outcome; not an available reverse-invoicing contract
      EXPIRED   — supplier did not reply within the expiry window
      FAILED    — initiation/submission failed; inspect failure_reason
    """
    request_id:     int
    status:         str
    supplier_phone: Optional[str]    = None
    amount:         Optional[Decimal] = None
    channel:        Optional[str]    = None
    purchase_id:    Optional[int]    = None
    failure_reason: Optional[str]    = None
    fiscal_submission_available: Optional[bool] = None
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
            failure_reason=payload.get("failureReason"),
            fiscal_submission_available=payload.get("fiscalSubmissionAvailable"),
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

    def _execute(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Wraps ``_client._request`` with bounded, full-jitter retry for failures
        that cannot have had a server-side effect — see ``_is_retryable``.
        Up to ``_MAX_RETRIES`` retries (4 attempts in total).
        """
        from .exceptions import KRAeTIMSError

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._client._request(method, path, json=json, idempotency_key=idempotency_key)
            except KRAeTIMSError as exc:
                if attempt == _MAX_RETRIES or not _is_retryable(exc, method, idempotency_key):
                    raise
            time.sleep(_backoff(attempt))
        raise AssertionError("unreachable")

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
            "amount":    _money(amount),
        }
        if item_description:
            payload["itemDescription"] = item_description

        raw = self._execute(
            "POST",
            "/v2/gateway/supplier-onboarding/single",
            json=payload,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
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
                    "amount": _money(s.amount),
                    **({"itemDescription": s.item_description}
                       if s.item_description else {}),
                }
                for s in suppliers
            ],
        }
        raw = self._execute(
            "POST", "/v2/gateway/supplier-onboarding", json=payload
        )
        return BulkOnboardingResponse.from_api(raw)

    def get_status(self, request_id: int) -> SupplierGatewayStatus:
        """
        Poll the status of a supplier onboarding request.

        CONFIRMED records consent only. If fiscal_submission_available is false,
        retain the gate reason and stop waiting for an invoice. Missing availability
        on an older server is unknown, not permission to submit a reverse invoice.

        Parameters
        ----------
        request_id:
            The ``request_id`` from ``onboard_supplier()`` or ``onboard_suppliers()``.
        """
        raw = self._execute(
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

    async def _execute(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async equivalent of ``TaxIDSupplierGateway._execute``."""
        from .exceptions import KRAeTIMSError

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._client._request(method, path, json=json, idempotency_key=idempotency_key)
            except KRAeTIMSError as exc:
                if attempt == _MAX_RETRIES or not _is_retryable(exc, method, idempotency_key):
                    raise
            await asyncio.sleep(_backoff(attempt))
        raise AssertionError("unreachable")

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
            "amount":    _money(amount),
        }
        if item_description:
            payload["itemDescription"] = item_description

        raw = await self._execute(
            "POST",
            "/v2/gateway/supplier-onboarding/single",
            json=payload,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
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
                    "amount": _money(s.amount),
                    **({"itemDescription": s.item_description}
                       if s.item_description else {}),
                }
                for s in suppliers
            ],
        }
        raw = await self._execute(
            "POST", "/v2/gateway/supplier-onboarding", json=payload
        )
        return BulkOnboardingResponse.from_api(raw)

    async def get_status(self, request_id: int) -> SupplierGatewayStatus:
        """Async: poll a supplier onboarding request status."""
        raw = await self._execute(
            "GET", f"/v2/gateway/supplier-onboarding/{request_id}/status"
        )
        return SupplierGatewayStatus.from_api(raw)
