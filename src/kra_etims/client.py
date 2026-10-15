"""
KRAeTIMSClient — synchronous SDK for TIaaS (Tax Identity as a Service).

Transport: httpx.Client (thread-safe; replaces requests.Session + threading.local).
Auth:      API key (preferred) or OAuth2 client_credentials with threading.Lock.
"""

import threading
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


class KRAeTIMSClient(_BaseKRAeTIMSClient):
    """
    Sync SDK for TIaaS. Use in Django, Flask, scripts, or Celery workers.

    httpx.Client is thread-safe — a single instance serves all Celery worker
    threads concurrently without per-thread session overhead.

    Auth modes (in priority order):
      1. API Key  — env TAXID_API_KEY or ``api_key`` kwarg (preferred B2B)
      2. OAuth 2.0 — client_credentials with auto-refresh

    Attach sub-interfaces:
      client.reports.get_daily_z("2026-03-11")
      client.gateway.request_reverse_invoice(phone_number=..., amount=...)

    Usage:
        with KRAeTIMSClient(client_id, client_secret, api_key=...) as client:
            receipt = client.submit_sale(invoice)
    """

    @property
    def retail(self):
        from .retail import RetailInterface
        return RetailInterface(self)

    @property
    def prerequisites(self):
        """Accountable operator recovery; never automatically replays registration."""
        from .prerequisites import PrerequisiteInterface
        return PrerequisiteInterface(self)

    @property
    def withholding(self):
        """Accounting ledger and supplied remittance evidence; no KRA money movement."""
        from .withholding import WithholdingInterface
        return WithholdingInterface(self)

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(client_id, client_secret, api_key, base_url)
        # httpx.Client is thread-safe — one instance handles all concurrent threads.
        self._http = httpx.Client(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=30.0,
        )
        self._auth_lock = threading.Lock()   # guards OAuth2 token refresh
        self._init_lock = threading.Lock()   # guards lazy interface init
        self._reports = None
        self._gateway = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "KRAeTIMSClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self._http.close()

    def close(self) -> None:
        """Manually close the underlying httpx client."""
        self._http.close()

    # ------------------------------------------------------------------
    # Sub-interface accessors
    # ------------------------------------------------------------------

    @property
    def reports(self):
        """Access reporting interface: client.reports.get_daily_z(...)"""
        if self._reports is None:
            with self._init_lock:
                if self._reports is None:
                    from .reports import ReportsInterface
                    self._reports = ReportsInterface(self)
        return self._reports

    @property
    def gateway(self):
        """Access gateway interface: client.gateway.request_reverse_invoice(...)"""
        if self._gateway is None:
            with self._init_lock:
                if self._gateway is None:
                    from .gateway import TaxIDSupplierGateway
                    self._gateway = TaxIDSupplierGateway(self)
        return self._gateway

    # ------------------------------------------------------------------
    # Authentication (concrete sync — threading.Lock)
    # ------------------------------------------------------------------

    def _authenticate(self) -> None:
        """
        OAuth 2.0 Client Credentials with thread-safe double-checked locking
        and a 60-second refresh buffer.

        Skipped entirely when an API key is configured.
        """
        if self._api_key:
            return

        # Outer pre-lock check: skip lock acquisition on the hot path
        # when the token is valid.
        now = time.time()
        if self._access_token and (self._token_expiry - now) >= 60:
            return

        with self._auth_lock:
            now = time.time()  # re-read: time elapsed while waiting for lock
            if not self._access_token or (self._token_expiry - now) < 60:
                try:
                    resp = self._http.post(
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
    # Core request dispatcher (concrete sync)
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Core request dispatcher with resilience mapping."""
        _attrs: Dict[str, Any] = {"http.method": method, "http.path": path}
        if idempotency_key:
            # Default keys embed the taxpayer PIN; export a correlatable digest only.
            _attrs["idempotency_key.sha256"] = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]

        with _span("kra_etims.request", _attrs):
            self._authenticate()
            url     = self._build_url(path)
            headers = self._build_auth_headers(idempotency_key)
            inject_trace_context(headers)

            try:
                resp = self._http.request(method, url, json=json, files=files, headers=headers)
            except httpx.RequestError as exc:
                raise self._transport_error(exc, method, idempotency_key) from exc
            return self._parse_response(resp, method, idempotency_key)

    def provision_tenant(self, data: "TenantProvisioning") -> Dict[str, Any]:
        """Create an immutable branch device using an unbound operator credential."""
        return self._request("POST", "/v2/admin/tenants", json=data.model_dump(mode="json", exclude_none=True))

    def issue_tenant_key(self, device_id: int, client_name: str, role: str = "ROLE_SDK_CLIENT") -> Dict[str, Any]:
        """Operator-only. The returned API credential is displayed once; store it privately."""
        return self._request("POST", f"/v2/admin/tenants/{device_id}/api-keys",
                             json={"clientName": client_name, "role": role})

    def device_status(self) -> Dict[str, Any]:
        """Read this branch's durable onboarding checkpoint without dispatching initialization."""
        return self._request("GET", "/v2/etims/device")

    def device_health(self) -> Dict[str, Any]:
        """Probe this branch with a read-only protocol request; does not prove central synchronization."""
        return self._request("GET", "/v2/etims/device/health")

    def capabilities(self) -> Dict[str, Any]:
        """Branch engineering support and restrictions; never authorization or KRA approval."""
        return self._request("GET", "/v2/etims/device/capabilities")

    def metadata(self, kind: str, *, since: str = "20000101000000") -> Dict[str, Any]:
        """Read codes, classifications or notices through the stored branch adapter."""
        from urllib.parse import urlencode
        if kind not in {"CODES", "CLASSIFICATIONS", "NOTICES"} or len(since) != 14 or not since.isascii() or not since.isdigit():
            raise ValueError("Supported metadata kind and fourteen-digit cursor required")
        return self._request("GET", "/v2/etims/device/metadata?" + urlencode({"kind": kind, "since": since}))

    def initialize_branch(self) -> Dict[str, Any]:
        """Initialize the authorized stored identity; inspect uncertain states without replay."""
        return self._request("POST", "/v2/etims/init")

    def device_monitoring(self) -> Dict[str, Any]:
        """Read local protocol observations; no central-sync or notification proof."""
        return self._request("GET", "/v2/etims/device/monitoring")

    def list_tenants(self) -> List[Dict[str, Any]]:
        """Operator-only safe onboarding summaries; never returns communication keys."""
        return self._request("GET", "/v2/admin/tenants")

    # ------------------------------------------------------------------
    # Category 1 — Device Initialisation
    # ------------------------------------------------------------------

    def initialize_device(self, data: DeviceInit) -> Dict[str, Any]:
        """Category 1: Register device/branch on eTIMS."""
        return self._request(
            "POST", "/v2/etims/init",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    def initialize_device_handshake(self) -> Dict[str, Any]:
        """Initialize this credential's branch through its durable VSCU/OSCU selection.

        READY confirms the persisted mode-specific checkpoint. OUTCOME_UNKNOWN or
        KEY_REQUIRED requires authoritative recovery; do not automatically resend.
        No communication key is returned. Existing GET route remains compatible.
        """
        return self._request("GET", "/v2/etims/init-handshake")

    # ------------------------------------------------------------------
    # Category 2 — Data Synchronisation
    # ------------------------------------------------------------------

    def sync_data(self, data: DataSyncRequest) -> Dict[str, Any]:
        """Category 2: Sync codes, items, and branches."""
        return self._request(
            "POST", "/v2/etims/sync",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Category 4 — Item Management
    # ------------------------------------------------------------------

    def save_item(self, data: ItemSave) -> Dict[str, Any]:
        """
        Category 4: Save or update item master data.

        Posts the middleware's ``POST /v2/etims/items`` registry schema — NOT
        the KRA-native ``ItemSave`` dump, which the middleware 400s. See
        :func:`kra_etims.models.to_middleware_item_payload` for the mapping.
        The response includes ``vscuRegistered`` (whether the VSCU JAR
        acknowledged the registration) and the server-generated ``itemCd``.
        """
        return self._request(
            "POST", "/v2/etims/items",
            json=to_middleware_item_payload(data),
        )

    def bulk_import_items(self, csv_path: str) -> Dict[str, Any]:
        """
        Bulk-register a catalog of items from a CSV file (middleware Track C5).

        Posts multipart to ``POST /v2/etims/items/bulk-import``. Each row is
        validated and registered independently — a bad row doesn't abort the
        upload. The response's ``results`` list carries one entry per row with
        its own success/failure; check ``failed`` on the returned dict rather
        than relying on the HTTP status code alone (207 means "some rows
        failed", not "the whole upload failed" — this call does not raise for
        a 207, only for a structural rejection like a missing column, an empty
        file, or more than the server's row-count ceiling, which the
        middleware reports as a 400).
        """
        with open(csv_path, "rb") as fh:
            return self._request(
                "POST", "/v2/etims/items/bulk-import",
                files={"file": (csv_path, fh, "text/csv")},
            )

    # ------------------------------------------------------------------
    # Category 6 — Sales Invoices
    # ------------------------------------------------------------------

    def submit_sale(
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
            return self._request(
                "POST", "/v2/etims/sale",
                json=to_middleware_sale_payload(invoice),
                idempotency_key=idempotency_key,
            )

    def lookup_customer(self, pin: str) -> Dict[str, Any]:
        """Read taxpayer information through this branch's protocol; does not register a customer."""
        from urllib.parse import urlencode
        return self._request("GET", "/v2/etims/customers/lookup?"+urlencode({"pin":pin}))

    def get_sale_original(self, purchase_id: int) -> Dict[str, Any]:
        """Inspect original business payload and safe protocol identity without fiscal dispatch."""
        return self._request("GET", f"/v2/etims/sales/{purchase_id}/original")

    def register_customer(self, registration: "BranchCustomerRegistration") -> Dict[str, Any]:
        """Explicit branch-customer registration; independent of taxpayer lookup."""
        return self._request("POST", "/v2/etims/customers", json=registration.model_dump(mode="json"))

    def register_item(self, item: "ItemDetail | ItemRegistration") -> Dict[str, Any]:
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
        return self._request("POST", "/v2/etims/items", json=payload)

    def find_sale(self, *, client_reference=None, operation_key=None, request_id=None, transaction_id=None) -> Dict[str, Any]:
        """Inspect one original operation after an HTTP timeout without submitting it."""
        from urllib.parse import urlencode
        query={"clientReference":client_reference,"operationKey":operation_key,"requestId":request_id,"transactionId":transaction_id}
        selected={key:value for key,value in query.items() if value is not None}
        if len(selected)!=1:
            raise ValueError("Supply exactly one original sale lookup identity")
        return self._request("GET", "/v2/etims/sales/search?"+urlencode(selected))

    def get_sale_status(self, invc_no: int) -> Dict[str, Any]:
        """
        Poll the ground-truth outcome of a sale that returned 202 PENDING_SYNC
        from :meth:`submit_sale` (middleware Track C4).

        ``invc_no`` is the ``purchaseId`` from the original :meth:`submit_sale`
        response, not the KRA ``invcNo`` sequence number. Prefer a registered
        ``receipt.signed``/``receipt.failed`` webhook over polling this in a
        loop; this endpoint is the authoritative fallback for tenants with no
        webhook configured or a missed delivery.

        :raises KRAeTIMSError: HTTP 404 — no sale with this ``purchaseId`` for
            the authenticated tenant.
        """
        return self._request("GET", f"/v2/etims/sales/{invc_no}/status")

    # ------------------------------------------------------------------
    # Category 7 — Credit Notes
    # ------------------------------------------------------------------

    def issue_credit_note(
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
            return self._request(
                "POST", f"/v2/etims/sale/{original_purchase_id}/credit-note",
                json=body if body else None,
                idempotency_key=idempotency_key,
            )

    def submit_reverse_invoice(self, invoice: ReverseInvoice) -> Dict[str, Any]:
        """
        .. deprecated::
            ``/v2/etims/reverse`` is no longer active. This historical refund
            method is unrelated to KRA buyer-issued reverse invoicing. For an
            original-invoice refund use :meth:`issue_credit_note`. Dedicated
            buyer-issued invoicing remains unavailable pending an approved contract.
        """
        warnings.warn(
            "submit_reverse_invoice() is deprecated and targets a removed endpoint. "
            "For original-invoice refunds use issue_credit_note(); this does not provide buyer-issued reverse invoicing.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._request(
            "POST", "/v2/etims/reverse",
            json=invoice.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Category 8 — Stock Management
    # ------------------------------------------------------------------

    def update_stock(self, data: StockItem) -> Dict[str, Any]:
        """Restricted legacy stock route; use submit_stock_adjustment with an original operation key."""
        return self._request(
            "POST", "/v2/etims/stock",
            json=data.model_dump(mode="json", exclude_none=True),
        )

    def submit_stock_adjustment(
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
        result = self._request(
            "POST", "/v2/etims/stock/adjustment",
            json=request.model_dump(mode="json", exclude_none=True),
            idempotency_key=idempotency_key,
        )
        return StockOperationResult.model_validate(result)

    def inspect_stock_operation(self, operation_key: str) -> StockOperationResult:
        """Read retained current stock outcome; a cached POST response may be older."""
        from urllib.parse import quote
        result = self._request("GET", "/v2/etims/stock/operations/" + quote(operation_key, safe=""))
        return StockOperationResult.model_validate(result)

    def submit_stock_master(self, request: StockMasterRequest, *, idempotency_key: str) -> StockMasterResult:
        """Send supplied absolute residual quantity after confirmed original I/O.

        Never calculated from sales/returns. Unknown originals fence branch dispatch;
        duplicate keys retain the original authenticated payload and cannot dispatch again.
        """
        if not idempotency_key:
            raise ValueError("Stock master requires a stable idempotency_key")
        result = self._request("POST", "/v2/etims/stock/master",
            json=request.model_dump(mode="json"), idempotency_key=idempotency_key)
        return StockMasterResult.model_validate(result)

    def inspect_stock_master(self, operation_key: str) -> StockMasterResult:
        """Inspect the original absolute stock operation without fiscal dispatch."""
        from urllib.parse import quote
        result = self._request("GET", "/v2/etims/stock/master/operations/" + quote(operation_key, safe=""))
        return StockMasterResult.model_validate(result)

    def batch_update_stock(self, items: List[StockItem]) -> List[Dict[str, Any]]:
        """
        .. deprecated::
            ``/v2/etims/stock/batch`` has been removed (returns HTTP 501).
            Use :meth:`submit_stock_adjustment` with a list of
            :class:`StockAdjustmentLine` objects instead.
        """
        raise NotImplementedError(
            "batch_update_stock() targets /v2/etims/stock/batch which has been removed. "
            "Use submit_stock_adjustment(lines=[StockAdjustmentLine(...)]) instead."
        )

    # ------------------------------------------------------------------
    # Offline Queue
    # ------------------------------------------------------------------

    def flush_offline_queue(self, invoices: List[SaleInvoice]) -> List[Dict[str, Any]]:
        """
        Submit a batch of offline-queued invoices once connectivity is restored.
        Returns per-invoice results; failures do not abort the batch.

        Each invoice is submitted with a deterministic idempotency key derived
        from ``tin:invcNo``. Retain that identity during original-outcome inspection.
        A duplicate response is an unresolved error until receipt evidence is
        obtained; it is not counted as completed success.
        """
        with _span("kra_etims.flush_offline_queue", {"queue.size": len(invoices)}):
            results = []
            for invoice in invoices:
                idem_key = f"{invoice.tin}:{invoice.invcNo}"
                try:
                    outcome: Any = self.submit_sale(invoice, idempotency_key=idem_key)
                except Exception as exc:  # one bad invoice must not abort the batch
                    outcome = exc
                results.append(self._flush_outcome(invoice.invcNo, idem_key, outcome))
            return results

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return self._request("GET", f"/v2/etims/compliance/{pin}")

    def lookup_pin(self, pin: str) -> Dict[str, Any]:
        """Look up a KRA PIN via GavaConnect (read-only, no VSCU state change)."""
        return self._request("GET", f"/v2/taxpayer/lookup?pin={pin}")
