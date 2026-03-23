import time
import os
import asyncio
import httpx
from typing import Optional, Dict, Any, List

from .middleware import sanitize_kra_url
from .exceptions import (
    KRA_ERROR_MAP,
    KRAConnectivityTimeoutError,
    KRADuplicateInvoiceError,
    KRAeTIMSAuthError,
    KRAeTIMSError,
    TIaaSUnavailableError,
    TIaaSAmbiguousStateError,
)
from .models import (
    DeviceInit, DataSyncRequest, BranchInfo, ItemSave,
    ImportItem, SaleInvoice, ReverseInvoice, StockItem,
)

# Maximum concurrent in-flight requests during offline-queue flush.
# Chosen to respect typical rate limits without stalling the event loop.
_FLUSH_CONCURRENCY = 50


class AsyncKRAeTIMSClient:
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
    ):
        self.client_id = client_id
        self._client_secret = client_secret

        env_url = (os.getenv("TAXID_API_URL") or "").strip()
        default_url = "https://taxid-production.up.railway.app"
        raw_url = env_url or base_url or default_url
        self.base_url = raw_url.strip().rstrip("/")

        # Full API key parity with sync client
        self._api_key: Optional[str] = os.getenv("TAXID_API_KEY") or api_key

        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

        self._client = httpx.AsyncClient(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=30.0,
        )
        self._lock = asyncio.Lock()

        # Sub-interfaces (lazy initialised on first access)
        self._reports = None
        self._gateway = None

    # ------------------------------------------------------------------
    # Representation — never echo secrets
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"client_id={self.client_id!r}, "
            f"base_url={self.base_url!r}, "
            f"auth_mode={'api_key' if self._api_key else 'oauth2'}"
            f")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.aclose()

    async def aclose(self) -> None:
        """Manually close the underlying httpx client."""
        await self._client.aclose()

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
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> None:
        """
        Async OAuth 2.0 Client Credentials with asyncio.Lock-based
        double-checked locking and a 60-second refresh buffer.

        Skipped entirely when an API key is configured.
        """
        if self._api_key:
            return

        async with self._lock:
            now = time.time()  # re-read: time elapsed while waiting for lock
            if not self._access_token or (self._token_expiry - now) < 60:
                try:
                    resp = await self._client.post(
                        f"{self.base_url}/oauth/token",
                        auth=(self.client_id, self._client_secret),
                        data={"grant_type": "client_credentials"},
                    )
                    if resp.status_code != 200:
                        try:
                            body = resp.json()
                            detail = body.get("error_description") or body.get("error") or f"HTTP {resp.status_code}"
                        except Exception:
                            detail = f"HTTP {resp.status_code}"
                        raise KRAeTIMSAuthError(f"TIaaS Authentication failed: {detail}")
                    data = resp.json()
                    self._access_token = data.get("access_token")
                    expires_in = int(data.get("expires_in", 3600))
                    self._token_expiry = now + (expires_in if expires_in > 0 else 3600)
                except httpx.RequestError as exc:
                    raise KRAeTIMSAuthError(
                        f"TIaaS Authentication unreachable: {type(exc).__name__}"
                    ) from exc

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error_response(self, response_json: Any) -> None:
        """
        Intercept opaque KRA application errors embedded in HTTP 200 bodies.
        Maps KRA result codes to precise, actionable exceptions.
        """
        if not isinstance(response_json, dict):
            raise KRAeTIMSError(
                f"Unexpected response format: expected JSON object, "
                f"got {type(response_json).__name__}"
            )

        result_cd = str(response_json.get("resultCd", "00")).strip()
        if result_cd == "00":
            return

        result_msg = response_json.get("resultMsg", "Unknown KRA error")

        if result_cd in KRA_ERROR_MAP:
            exc_class, default_msg = KRA_ERROR_MAP[result_cd]
            raise exc_class(f"{default_msg}: {result_msg}")

        raise KRAeTIMSError(f"KRA Error [{result_cd}]: {result_msg}")

    # ------------------------------------------------------------------
    # Core request dispatcher
    # ------------------------------------------------------------------

    @sanitize_kra_url
    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Core async request dispatcher with resilience mapping.

        The ``@sanitize_kra_url`` decorator is async-aware (returns
        ``async def wrapper``) — no event-loop deadlocks.
        """
        await self._authenticate()
        url = f"{self.base_url}/{path.lstrip('/')}"

        if self._api_key:
            headers: Dict[str, str] = {"X-API-Key": self._api_key}
        else:
            headers = {"Authorization": f"Bearer {self._access_token}"}

        if idempotency_key:
            headers["X-TIaaS-Idempotency-Key"] = idempotency_key

        try:
            resp = await self._client.request(
                method, url, json=json, headers=headers
            )

            if resp.status_code == 503:
                raise KRAConnectivityTimeoutError()

            resp.raise_for_status()
            try:
                response_data = resp.json()
            except (ValueError, httpx.DecodingError):
                raise KRAeTIMSError(
                    f"Non-JSON response from TIaaS [{resp.status_code}]: "
                    f"{resp.text[:200]}"
                )
            self._handle_error_response(response_data)
            return response_data

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
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 503:
                raise KRAConnectivityTimeoutError()
            raise KRAeTIMSError(
                f"TIaaS returned HTTP {exc.response.status_code}"
            ) from exc

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

    @sanitize_kra_url
    async def submit_sale(
        self,
        invoice: SaleInvoice,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Category 6: Submit a Sales Invoice (Normal / Copy / Training)."""
        return await self._request(
            "POST", "/v2/etims/sale",
            json=invoice.model_dump(mode="json", exclude_none=True),
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # Category 7 — Reverse Invoices
    # ------------------------------------------------------------------

    async def submit_reverse_invoice(
        self, invoice: ReverseInvoice
    ) -> Dict[str, Any]:
        """Category 7: Submit a Reverse / Credit Note Invoice."""
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

    async def batch_update_stock(
        self, items: List[StockItem]
    ) -> List[Dict[str, Any]]:
        """Category 8: High-volume batch update with 500-item chunking (async)."""
        results = []
        for i in range(0, len(items), 500):
            chunk = items[i : i + 500]
            try:
                payload = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in chunk
                ]
                await self._request(
                    "POST", "/v2/etims/stock/batch", json={"items": payload}
                )
                results.append(
                    {"chunk": i // 500, "status": "success", "count": len(chunk)}
                )
            except Exception as exc:
                results.append(
                    {"chunk": i // 500, "status": "error", "message": str(exc)}
                )
        return results

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
        semaphore = asyncio.Semaphore(_FLUSH_CONCURRENCY)

        async def _submit_one(invoice: SaleInvoice) -> Dict[str, Any]:
            async with semaphore:
                idem_key = f"{invoice.tin}:{invoice.invcNo}"
                return await self.submit_sale(invoice, idempotency_key=idem_key)

        tasks = [_submit_one(inv) for inv in invoices]
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

    @sanitize_kra_url
    async def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return await self._request("GET", f"/v2/etims/compliance/{pin}")
