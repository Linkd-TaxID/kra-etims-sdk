"""
AsyncKRAeTIMSClient — async SDK for TIaaS (Tax Identity as a Service).

Transport: httpx.AsyncClient.
Auth:      API key (preferred) or OAuth2 client_credentials with asyncio.Lock.
"""

import asyncio
import time
import warnings
from typing import Any, Dict, List, Optional

import httpx

from ._base_client import _BaseKRAeTIMSClient
from ._telemetry import span as _span
from .exceptions import (
    KRAConnectivityTimeoutError,
    KRADuplicateInvoiceError,
    KRAeTIMSAuthError,
    KRAeTIMSError,
    TIaaSAmbiguousStateError,
    TIaaSUnavailableError,
)
from .models import (
    DeviceInit,
    DataSyncRequest,
    ItemSave,
    SaleInvoice,
    ReverseInvoice,
    StockItem,
    StockAdjustmentLine,
    StockAdjustmentRequest,
)

# Maximum concurrent in-flight requests during offline-queue flush.
# Chosen to respect typical rate limits without stalling the event loop.
_FLUSH_CONCURRENCY = 50


class AsyncKRAeTIMSClient(_BaseKRAeTIMSClient):
    """
    Async SDK for TIaaS (Tax Identity as a Service).
    Optimised for non-blocking I/O in frameworks like FastAPI and Starlette.

    Auth modes (in priority order — full parity with sync client):
      1. API Key  — env TAXID_API_KEY or ``api_key`` kwarg (preferred B2B)
      2. OAuth 2.0 — client_credentials with asyncio.Lock refresh guard

    Attach sub-interfaces:
      await client.reports.get_daily_z("2026-03-11")
      await client.gateway.request_reverse_invoice(phone_number=..., amount=...)

    Use as async context manager:
      async with AsyncKRAeTIMSClient(...) as client:
          response = await client.submit_sale(invoice)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(client_id, client_secret, api_key, base_url)
        self._http = httpx.AsyncClient(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=30.0,
        )
        self._lock = asyncio.Lock()     # guards OAuth2 token refresh only
        self._reports = None
        self._gateway = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncKRAeTIMSClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    async def aclose(self) -> None:
        """Manually close the underlying httpx client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Sub-interface accessors
    # ------------------------------------------------------------------

    @property
    def reports(self):
        """Access async reporting interface: await client.reports.get_daily_z(...)"""
        if self._reports is None:
            from .reports import AsyncReportsInterface
            self._reports = AsyncReportsInterface(self)
        return self._reports

    @property
    def gateway(self):
        """Access async gateway interface: await client.gateway.request_reverse_invoice(...)"""
        if self._gateway is None:
            from .gateway import AsyncTaxIDSupplierGateway
            self._gateway = AsyncTaxIDSupplierGateway(self)
        return self._gateway

    # ------------------------------------------------------------------
    # Authentication (concrete async — asyncio.Lock)
    # ------------------------------------------------------------------

    async def _authenticate(self) -> None:
        """
        Async OAuth 2.0 Client Credentials with asyncio.Lock-based
        double-checked locking and a 60-second refresh buffer.

        Skipped entirely when an API key is configured.

        Pattern mirrors the sync client (TIaaS Engineering Spec §2.1, §2.2):
        outer pre-lock validity check → skip lock acquisition on the hot path
        when the token is valid. Without this check every coroutine serialises
        on ``async with self._lock`` even with a fresh token, creating a
        bottleneck under concurrent ``flush_offline_queue`` loads.

        asyncio is cooperatively scheduled: no coroutine switch occurs between
        the outer ``time.time()`` call and ``async with self._lock`` because
        there is no ``await`` between them — making the outer check safe.
        """
        if self._api_key:
            return

        # Outer pre-lock check: skip lock acquisition entirely on the hot path.
        # Equivalent to the sync client's pattern (client.py) that prevents all
        # concurrent coroutines bottlenecking on self._lock for a token validity test.
        now = time.time()
        if self._access_token and (self._token_expiry - now) >= 60:
            return

        async with self._lock:
            now = time.time()  # re-read: time elapsed while waiting for lock
            if not self._access_token or (self._token_expiry - now) < 60:
                try:
                    resp = await self._http.post(
                        f"{self.base_url}/oauth/token",
                        auth=(self.client_id, self._client_secret),
                        data={"grant_type": "client_credentials"},
                    )
                except httpx.RequestError as exc:
                    raise KRAeTIMSAuthError(
                        f"TIaaS Authentication unreachable: {type(exc).__name__}"
                    ) from exc

                if resp.status_code != 200:
                    try:
                        body   = resp.json()
                        detail = (body.get("error_description")
                                  or body.get("error")
                                  or f"HTTP {resp.status_code}")
                    except Exception:
                        detail = f"HTTP {resp.status_code}"
                    raise KRAeTIMSAuthError(f"TIaaS Authentication failed: {detail}")

                data               = resp.json()
                self._access_token = data.get("access_token")
                expires_in         = int(data.get("expires_in", 3600))
                self._token_expiry = now + (expires_in if expires_in > 0 else 3600)

    # ------------------------------------------------------------------
    # Core request dispatcher (concrete async)
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Core async request dispatcher with resilience mapping."""
        _attrs: Dict[str, Any] = {"http.method": method, "http.path": path}
        if idempotency_key:
            _attrs["idempotency_key"] = idempotency_key

        with _span("kra_etims.request", _attrs):
            await self._authenticate()
            url     = self._build_url(path)
            headers = self._build_auth_headers(idempotency_key)

            try:
                resp = await self._http.request(method, url, json=json, headers=headers)
                return self._parse_response(resp, method, idempotency_key)

            except (httpx.ConnectError, httpx.ConnectTimeout):
                # TCP handshake never completed — request was never sent.
                raise TIaaSUnavailableError()
            except (
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RequestError,
            ):
                # Request was sent; response never arrived — state is ambiguous.
                if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
                    raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key)
                raise TIaaSUnavailableError()

    # ------------------------------------------------------------------
    # Category 1 — Device Initialisation
    # ------------------------------------------------------------------

    async def initialize_device(self, data: DeviceInit) -> Dict[str, Any]:
        """Category 1: Register device/branch on eTIMS."""
        return await self._request(
            "POST", "/v2/etims/init",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    async def initialize_device_handshake(self) -> Dict[str, Any]:
        """
        Category 1 (Pre-step): Trigger the middleware device wake-up.

        The middleware retrieves and AES-256-encrypts the cmcKey, then
        persists it to the TenantDevice record.
        """
        return await self._request("GET", "/v2/etims/init-handshake")

    # ------------------------------------------------------------------
    # Category 2 — Data Synchronisation
    # ------------------------------------------------------------------

    async def sync_data(self, data: DataSyncRequest) -> Dict[str, Any]:
        """Category 2: Sync codes, items, and branches."""
        return await self._request(
            "POST", "/v2/etims/sync",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Category 4 — Item Management
    # ------------------------------------------------------------------

    async def save_item(self, data: ItemSave) -> Dict[str, Any]:
        """Category 4: Save or update item master data."""
        return await self._request(
            "POST", "/v2/etims/item",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Category 6 — Sales Invoices
    # ------------------------------------------------------------------

    async def submit_sale(
        self,
        invoice: SaleInvoice,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Category 6: Submit a Sales Invoice (Normal / Copy / Training).

        ``idempotency_key`` is strongly recommended.  If omitted, a deterministic
        key ``"{tin}:{invcNo}"`` is auto-generated and a :class:`UserWarning` is
        emitted.  Pass an explicit key to suppress the warning and to guarantee
        safe retry behaviour when a network timeout drops the response before it
        reaches the caller.
        """
        if idempotency_key is None:
            idempotency_key = f"{invoice.tin}:{invoice.invcNo}"
            warnings.warn(
                f"submit_sale() called without an idempotency_key — "
                f"auto-generated '{idempotency_key}'. "
                "Pass idempotency_key=<your-key> explicitly to suppress this warning "
                "and guarantee safe retry behaviour on network timeouts.",
                UserWarning,
                stacklevel=2,
            )
        with _span("kra_etims.submit_sale", {
            "invoice.no": str(invoice.invcNo),
            "invoice.tin": invoice.tin,
        }):
            return await self._request(
                "POST", "/v2/etims/sale",
                json=invoice.model_dump(mode="json", exclude_none=True),
                idempotency_key=idempotency_key,
            )

    # ------------------------------------------------------------------
    # Category 7 — Credit Notes
    # ------------------------------------------------------------------

    async def issue_credit_note(
        self,
        original_purchase_id: int,
        reason: Optional[str] = None,
        items: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Issue a credit note against a previously signed sale.

        Posts to ``POST /v2/etims/sale/{id}/credit-note``.

        :param original_purchase_id: TIaaS database ID of the original sale.
        :param reason: Human-readable reversal reason (optional).
        :param items: Partial line items to reverse; ``None`` reverses the full invoice.
        :raises CreditNoteConflictError: HTTP 409 — credit note already exists.
        :raises KRAeTIMSError: HTTP 404 — original sale not found.
        :raises KRAConnectivityTimeoutError: VSCU offline ceiling breached (HTTP 503).
        """
        with _span("kra_etims.issue_credit_note", {"sale.id": str(original_purchase_id)}):
            body: Dict[str, Any] = {}
            if reason is not None:
                body["reason"] = reason
            if items is not None:
                body["items"] = items
            return await self._request(
                "POST", f"/v2/etims/sale/{original_purchase_id}/credit-note",
                json=body if body else None,
            )

    async def submit_reverse_invoice(
        self, invoice: ReverseInvoice
    ) -> Dict[str, Any]:
        """
        .. deprecated::
            ``/v2/etims/reverse`` is no longer active.  Use
            :meth:`issue_credit_note` instead.
        """
        warnings.warn(
            "submit_reverse_invoice() is deprecated and targets a removed endpoint. "
            "Use issue_credit_note(original_purchase_id, reason, items) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self._request(
            "POST", "/v2/etims/reverse",
            json=invoice.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Category 8 — Stock Management
    # ------------------------------------------------------------------

    async def update_stock(self, data: StockItem) -> Dict[str, Any]:
        """Category 8: Stock adjustment / transfer / loss."""
        return await self._request(
            "POST", "/v2/etims/stock",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    async def submit_stock_adjustment(
        self,
        lines: List[StockAdjustmentLine],
        cust_tin: Optional[str] = None,
        cust_nm: Optional[str] = None,
        remark: Optional[str] = None,
        org_sar_no: int = 0,
    ) -> Dict[str, Any]:
        """
        Submit a stock adjustment to ``POST /v2/etims/stock/adjustment``.

        A 201 response means the VSCU signed synchronously (``status=SYNCED``).
        A 202 response means the movement is queued for retry (``status=FAILED``);
        issue a new request — do not retry with the same data.

        :param lines: At least one :class:`StockAdjustmentLine`.
        :param cust_tin: Customer KRA TIN (B2B movements only).
        :param cust_nm: Customer name.
        :param remark: Free-text remark (max 400 chars).
        :param org_sar_no: Original SAR number for amendments (default 0).
        :raises KRAConnectivityTimeoutError: VSCU offline ceiling breached (HTTP 503).
        """
        request = StockAdjustmentRequest(
            lines=lines,
            custTin=cust_tin,
            custNm=cust_nm,
            remark=remark,
            orgSarNo=org_sar_no,
        )
        return await self._request(
            "POST", "/v2/etims/stock/adjustment",
            json=request.model_dump(mode="json", exclude_none=True),
        )

    async def batch_update_stock(
        self, items: List[StockItem]
    ) -> List[Dict[str, Any]]:
        """
        .. deprecated::
            ``/v2/etims/stock/batch`` has been removed (returns HTTP 501).
            Use :meth:`submit_stock_adjustment` instead.
        """
        raise NotImplementedError(
            "batch_update_stock() targets /v2/etims/stock/batch which has been removed. "
            "Use submit_stock_adjustment(lines=[StockAdjustmentLine(...)]) instead."
        )

    # ------------------------------------------------------------------
    # Offline Queue — concurrent flush with rate-limit guard
    # ------------------------------------------------------------------

    async def flush_offline_queue(
        self, invoices: List[SaleInvoice]
    ) -> List[Dict[str, Any]]:
        """
        Concurrently submit a batch of offline-queued invoices once
        connectivity is restored.

        Uses ``asyncio.gather`` with ``return_exceptions=True`` so a single
        failed invoice never aborts the batch, and ``asyncio.Semaphore``
        (limit: 50) to prevent rate-limit violations on the TIaaS backend.

        Returns a list of per-invoice result dicts in the same order as the
        input list.
        """
        with _span("kra_etims.flush_offline_queue", {"queue.size": len(invoices)}):
            semaphore = asyncio.Semaphore(_FLUSH_CONCURRENCY)

            async def _submit_one(invoice: SaleInvoice) -> Dict[str, Any]:
                async with semaphore:
                    idem_key = f"{invoice.tin}:{invoice.invcNo}"
                    return await self.submit_sale(invoice, idempotency_key=idem_key)

            tasks      = [_submit_one(inv) for inv in invoices]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            results: List[Dict[str, Any]] = []
            for invoice, outcome in zip(invoices, raw_results):
                if isinstance(outcome, KRADuplicateInvoiceError):
                    # Code 12 = already processed on a prior attempt.  The fiscal
                    # record exists on KRA — this is a safe idempotent success.
                    results.append({
                        "invoice_no": invoice.invcNo,
                        "status":     "already_processed",
                    })
                elif isinstance(outcome, Exception):
                    results.append({
                        "invoice_no": invoice.invcNo,
                        "status":     "error",
                        "message":    str(outcome),
                    })
                else:
                    results.append({
                        "invoice_no": invoice.invcNo,
                        "status":     "success",
                        "data":       outcome,
                    })
            return results

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    async def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return await self._request("GET", f"/v2/etims/compliance/{pin}")
