"""
AsyncKRAeTIMSClient — async SDK for TIaaS (Tax Identity as a Service).

Transport: httpx.AsyncClient.
Auth:      API key (preferred) or OAuth2 client_credentials with asyncio.Lock.
"""

import asyncio
import hashlib
import time
import warnings
from typing import Any, Dict, List, Optional

import httpx

from ._base_client import _BaseKRAeTIMSClient
from ._telemetry import inject_trace_context, span as _span
from .exceptions import (
    KRAConnectivityTimeoutError,
    KRADuplicateInvoiceError,
    KRAeTIMSAuthError,
    KRAeTIMSError,
    TIaaSAmbiguousStateError,
    TIaaSUnavailableError,
)
from .models import BranchCustomerRegistration, ItemRegistration, ItemDetail
from .models import ReturnLine, CreditNoteReason, StockOperationResult, StockMasterRequest, StockMasterResult
from .models import (
    TenantProvisioning,
    DeviceInit,
    DataSyncRequest,
    ItemSave,
    SaleInvoice,
    ReverseInvoice,
    StockItem,
    StockAdjustmentLine,
    StockAdjustmentRequest,
    to_middleware_item_payload,
    to_middleware_sale_payload,
)

# Default in-flight requests during offline-queue flush. TaxID serialises
# signing per tenant (fair lock, vscu.sign.lockwait.ms admission), so extra
# parallelism only hides RTT; beyond a few, waiters are diverted to the
# server-side queue and come back PENDING_SYNC instead of signed.
_FLUSH_CONCURRENCY = 4


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

    @property
    def retail(self):
        from .retail import AsyncRetailInterface
        return AsyncRetailInterface(self)

    @property
    def prerequisites(self):
        """Accountable operator recovery; never automatically replays registration."""
        from .prerequisites import AsyncPrerequisiteInterface
        return AsyncPrerequisiteInterface(self)

    @property
    def withholding(self):
        """Accounting ledger and supplied remittance evidence; no KRA money movement."""
        from .withholding import AsyncWithholdingInterface
        return AsyncWithholdingInterface(self)

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        timeout: float | httpx.Timeout | None = 30.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(client_id, client_secret, api_key, base_url)
        self._timeout = httpx.Timeout(timeout)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=self._timeout,
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
        await self.aclose()

    async def aclose(self) -> None:
        """Close an SDK-owned client; injected clients remain caller-owned."""
        if self._owns_http:
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
                        timeout=self._timeout,
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
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Core async request dispatcher with resilience mapping."""
        _attrs: Dict[str, Any] = {"http.method": method, "http.path": path}
        if idempotency_key:
            # Default keys embed the taxpayer PIN; export a correlatable digest only.
            _attrs["idempotency_key.sha256"] = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]

        with _span("kra_etims.request", _attrs):
            await self._authenticate()
            url     = self._build_url(path)
            headers = self._build_auth_headers(idempotency_key)
            inject_trace_context(headers)

            try:
                resp = await self._http.request(method, url, json=json, files=files, headers=headers, timeout=self._timeout)
            except httpx.RequestError as exc:
                raise self._transport_error(exc, method, idempotency_key) from exc
            return self._parse_response(resp, method, idempotency_key)

    async def provision_tenant(self, data: "TenantProvisioning") -> Dict[str, Any]:
        """Create an immutable branch device using an unbound operator credential."""
        return await self._request("POST", "/v2/admin/tenants", json=data.model_dump(mode="json", exclude_none=True))

    async def issue_tenant_key(self, device_id: int, client_name: str, role: str = "ROLE_SDK_CLIENT") -> Dict[str, Any]:
        """Operator-only. The returned API credential is displayed once; store it privately."""
        return await self._request("POST", f"/v2/admin/tenants/{device_id}/api-keys",
                             json={"clientName": client_name, "role": role})

    async def device_status(self) -> Dict[str, Any]:
        """Read this branch's durable onboarding checkpoint without dispatching initialization."""
        return await self._request("GET", "/v2/etims/device")

    async def device_health(self) -> Dict[str, Any]:
        """Probe this branch with a read-only protocol request; does not prove central synchronization."""
        return await self._request("GET", "/v2/etims/device/health")

    async def capabilities(self) -> Dict[str, Any]:
        """Branch engineering support and restrictions; never authorization or KRA approval."""
        return await self._request("GET", "/v2/etims/device/capabilities")

    async def metadata(self, kind: str, *, since: str = "20000101000000") -> Dict[str, Any]:
        """Read codes, classifications or notices through the stored branch adapter."""
        from urllib.parse import urlencode
        if kind not in {"CODES", "CLASSIFICATIONS", "NOTICES"} or len(since) != 14 or not since.isascii() or not since.isdigit():
            raise ValueError("Supported metadata kind and fourteen-digit cursor required")
        return await self._request("GET", "/v2/etims/device/metadata?" + urlencode({"kind": kind, "since": since}))

    async def initialize_branch(self) -> Dict[str, Any]:
        """Initialize the authorized stored identity; inspect uncertain states without replay."""
        return await self._request("POST", "/v2/etims/init")

    async def device_monitoring(self) -> Dict[str, Any]:
        """Read local protocol observations; no central-sync or notification proof."""
        return await self._request("GET", "/v2/etims/device/monitoring")

    async def list_tenants(self) -> List[Dict[str, Any]]:
        """Operator-only safe onboarding summaries; never returns communication keys."""
        return await self._request("GET", "/v2/admin/tenants")

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
        """Initialize this credential's branch through its durable VSCU/OSCU selection.

        READY confirms the persisted mode-specific checkpoint. OUTCOME_UNKNOWN or
        KEY_REQUIRED requires authoritative recovery; do not automatically resend.
        No communication key is returned. Existing GET route remains compatible.
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
        """
        Category 4: Save or update item master data.

        Posts the middleware's ``POST /v2/etims/items`` registry schema — the
        same mapping as the sync client (:func:`kra_etims.models.to_middleware_item_payload`).
        The async client previously posted the KRA-native ``ItemSave`` dump to the
        deprecated ``/v2/etims/item`` endpoint, diverging from the sync client and
        failing with 400 against middleware versions that bound that endpoint to the
        sale-line schema (campaign-5 finding, 2026-07-14).
        """
        return await self._request(
            "POST", "/v2/etims/items",
            json=to_middleware_item_payload(data),
        )

    async def bulk_import_items(self, csv_path: str) -> Dict[str, Any]:
        """
        Bulk-register a catalog of items from a CSV file (middleware Track C5).

        See :meth:`KRAeTIMSClient.bulk_import_items` for the full semantics —
        a 207 response means some rows failed independently, not that the
        whole upload was rejected; check ``failed`` on the returned dict.
        """
        with open(csv_path, "rb") as fh:
            return await self._request(
                "POST", "/v2/etims/items/bulk-import",
                files={"file": (csv_path, fh, "text/csv")},
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
        emitted.  Pass an explicit key to suppress the warning and to preserve
        the original request identity when a network timeout drops the response.
        Confirmed pre-signing prerequisite blocks can resume with this exact key
        and body after reconciliation. Uncertain signing requires status lookup.
        """
        if idempotency_key is None:
            idempotency_key = f"{invoice.tin}:{invoice.invcNo}"
            warnings.warn(
                f"submit_sale() called without an idempotency_key — "
                f"auto-generated '{idempotency_key}'. "
                "Pass idempotency_key=<your-key> explicitly to suppress this warning "
                "and retain the original identity for status lookup and recovery.",
                UserWarning,
                stacklevel=2,
            )
        with _span("kra_etims.submit_sale", {
            "invoice.no": str(invoice.invcNo),
        }):
            # Transmit the middleware's flat sale schema — NOT the KRA-native
            # SaleInvoice dump, which the middleware 400s. See
            # models.to_middleware_sale_payload for the field mapping.
            return await self._request(
                "POST", "/v2/etims/sale",
                json=to_middleware_sale_payload(invoice),
                idempotency_key=idempotency_key,
            )

    async def submit_sale_typed(self, invoice: SaleInvoice, idempotency_key: Optional[str] = None) -> "SaleResult":
        """Submit once and return a validated result with response metadata."""
        from .outcomes import SaleResult
        return SaleResult.from_result(await self.submit_sale(invoice, idempotency_key=idempotency_key))

    async def get_sale_status_typed(self, invc_no: int) -> "SaleStatusResult":
        """Inspect the original sale without issuing another fiscal call."""
        from .outcomes import SaleStatusResult
        return SaleStatusResult.from_result(await self.get_sale_status(invc_no))

    async def lookup_customer(self, pin: str) -> Dict[str, Any]:
        """Read taxpayer information through this branch's protocol; does not register a customer."""
        from urllib.parse import urlencode
        return await self._request("GET", "/v2/etims/customers/lookup?"+urlencode({"pin":pin}))

    async def get_sale_original(self, purchase_id: int) -> Dict[str, Any]:
        """Inspect original business payload and safe protocol identity without fiscal dispatch."""
        return await self._request("GET", f"/v2/etims/sales/{purchase_id}/original")

    async def register_customer(self, registration: "BranchCustomerRegistration") -> Dict[str, Any]:
        """Explicit branch-customer registration; independent of taxpayer lookup."""
        return await self._request("POST", "/v2/etims/customers", json=registration.model_dump(mode="json"))

    async def register_item(self, item: "ItemDetail | ItemRegistration") -> Dict[str, Any]:
        """Register an immutable source item using the branch's stored protocol.

        ``itemCd`` is the source SKU. The server owns the fiscal item code. Unknown
        registration outcomes require original-operation review; do not replace the SKU.
        """
        from .models import _to_middleware_sale_line
        if isinstance(item,ItemRegistration):
            payload=item.model_dump(mode="json")
        else:
            if not item.itemClsCd:
                raise ValueError("Item registration requires itemClsCd")
            payload=_to_middleware_sale_line(item)
        return await self._request("POST", "/v2/etims/items", json=payload)

    async def find_sale(self, *, client_reference=None, operation_key=None, request_id=None, transaction_id=None) -> Dict[str, Any]:
        """Inspect one original operation after an HTTP timeout without submitting it."""
        from urllib.parse import urlencode
        query={"clientReference":client_reference,"operationKey":operation_key,"requestId":request_id,"transactionId":transaction_id}
        selected={key:value for key,value in query.items() if value is not None}
        if len(selected)!=1:
            raise ValueError("Supply exactly one original sale lookup identity")
        return await self._request("GET", "/v2/etims/sales/search?"+urlencode(selected))

    async def get_sale_status(self, invc_no: int) -> Dict[str, Any]:
        """
        Poll the ground-truth outcome of a sale that returned 202 PENDING_SYNC
        from :meth:`submit_sale` (middleware Track C4). See
        :meth:`KRAeTIMSClient.get_sale_status` for full semantics.

        :raises KRAeTIMSError: HTTP 404 — no sale with this ``purchaseId`` for
            the authenticated tenant.
        """
        return await self._request("GET", f"/v2/etims/sales/{invc_no}/status")

    # ------------------------------------------------------------------
    # Category 7 — Credit Notes
    # ------------------------------------------------------------------

    async def issue_credit_note(
        self,
        original_purchase_id: int,
        reason: Optional[str] = None,
        items: Optional[List[Dict[str, Any]]] = None,
        *,
        client_reference: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        return_lines: Optional[List[ReturnLine]] = None,
        reason_code: Optional[CreditNoteReason] = None,
    ) -> Dict[str, Any]:
        """Return original quantities using a stable correction identity.

        Omitted selections reverse remaining original quantities. Legacy ``items``
        selects unambiguous original SKUs; replacement prices and tax fields are ignored.
        Partial/repeated returns are restricted to explicit synthetic fixtures.
        KRA TIS 6.16 defines once-only cancellation; live combinations need a verified
        contract and production corrections remain evidence-gated. Unknown/deferred
        corrections reserve their original quantities and must never be replaced.
        """
        with _span("kra_etims.issue_credit_note", {"sale.id": str(original_purchase_id)}):
            if not client_reference and not idempotency_key:
                raise ValueError("Credit notes require a stable client_reference or idempotency_key")
            body: Dict[str, Any] = {}
            if client_reference is not None:
                body["clientReference"] = client_reference
            if reason is not None:
                body["reason"] = reason
            if items is not None:
                body["items"] = items
            if return_lines is not None:
                if items:
                    raise ValueError("Select return_lines or legacy items, not both")
                body["returnLines"] = [line.model_dump(mode="json") for line in return_lines]
            if reason_code is not None:
                body["reasonCode"] = CreditNoteReason(reason_code).value
            return await self._request(
                "POST", f"/v2/etims/sale/{original_purchase_id}/credit-note",
                json=body if body else None,
                idempotency_key=idempotency_key,
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
            "For original-invoice refunds use issue_credit_note(); this does not provide buyer-issued reverse invoicing.",
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
        """Restricted legacy stock route; use submit_stock_adjustment with an original operation key."""
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
        *,
        idempotency_key: Optional[str] = None,
    ) -> StockOperationResult:
        """Submit an independent stock movement with a stable operation key.

        This does not duplicate sale or return stock effects. OUTCOME_UNKNOWN
        retains its original SAR and payload across timeout/restart; inspect it
        and resolve only with authoritative evidence. Never issue a replacement.
        VSCU uses its stock I/O route; OSCU uses authenticated /insertStockIO.
        Absolute remaining stock is a separate explicit submit_stock_master request.
        Production stock dispatch remains evidence-gated.
        """
        if not idempotency_key:
            raise ValueError("Stock requires a stable idempotency_key")
        request = StockAdjustmentRequest(
            lines=lines,
            custTin=cust_tin,
            custNm=cust_nm,
            remark=remark,
            orgSarNo=org_sar_no,
        )
        result = await self._request(
            "POST", "/v2/etims/stock/adjustment",
            json=request.model_dump(mode="json", exclude_none=True),
            idempotency_key=idempotency_key,
        )
        return StockOperationResult.model_validate(result)

    async def inspect_stock_operation(self, operation_key: str) -> StockOperationResult:
        """Read retained current stock outcome; a cached POST response may be older."""
        from urllib.parse import quote
        result = await self._request("GET", "/v2/etims/stock/operations/" + quote(operation_key, safe=""))
        return StockOperationResult.model_validate(result)

    async def submit_stock_master(self, request: StockMasterRequest, *, idempotency_key: str) -> StockMasterResult:
        """Send supplied absolute residual quantity after confirmed original I/O.

        Never calculated from sales/returns. Unknown originals fence branch dispatch;
        duplicate keys retain the original authenticated payload and cannot dispatch again.
        """
        if not idempotency_key:
            raise ValueError("Stock master requires a stable idempotency_key")
        result = await self._request("POST", "/v2/etims/stock/master",
            json=request.model_dump(mode="json"), idempotency_key=idempotency_key)
        return StockMasterResult.model_validate(result)

    async def inspect_stock_master(self, operation_key: str) -> StockMasterResult:
        """Inspect the original absolute stock operation without fiscal dispatch."""
        from urllib.parse import quote
        result = await self._request("GET", "/v2/etims/stock/master/operations/" + quote(operation_key, safe=""))
        return StockMasterResult.model_validate(result)

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
        self, invoices: List[SaleInvoice], *, concurrency: int = _FLUSH_CONCURRENCY
    ) -> List[Dict[str, Any]]:
        """
        Concurrently submit a batch of offline-queued invoices once
        connectivity is restored.

        Uses ``asyncio.gather`` with ``return_exceptions=True`` so a single
        failed invoice never aborts the batch, and ``asyncio.Semaphore``
        (concurrency, default 4) to bound load on the tenant's signing lock.

        Returns a list of per-invoice result dicts in the same order as the
        input list.
        """
        with _span("kra_etims.flush_offline_queue", {"queue.size": len(invoices)}):
            semaphore = asyncio.Semaphore(concurrency)

            async def _submit_one(invoice: SaleInvoice) -> Dict[str, Any]:
                async with semaphore:
                    idem_key = f"{invoice.tin}:{invoice.invcNo}"
                    return await self.submit_sale(invoice, idempotency_key=idem_key)

            tasks      = [_submit_one(inv) for inv in invoices]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            return [
                self._flush_outcome(inv.invcNo, f"{inv.tin}:{inv.invcNo}", outcome)
                for inv, outcome in zip(invoices, raw_results)
            ]

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    async def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return await self._request("GET", f"/v2/etims/compliance/{pin}")

    async def lookup_pin(self, pin: str) -> Dict[str, Any]:
        """Compatibility alias for taxpayer lookup; does not register a buyer."""
        return await self.lookup_customer(pin)
