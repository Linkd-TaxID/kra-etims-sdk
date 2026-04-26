"""
KRAeTIMSClient — synchronous SDK for TIaaS (Tax Identity as a Service).

Transport: httpx.Client (thread-safe; replaces requests.Session + threading.local).
Auth:      API key (preferred) or OAuth2 client_credentials with threading.Lock.
"""

import threading
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

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(client_id, client_secret, api_key, base_url)
        # httpx.Client is thread-safe — one instance handles all concurrent threads.
        self._http = httpx.Client(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=30.0,
        )
        self._lock = threading.Lock()   # guards OAuth2 token refresh only
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
            with self._lock:
                if self._reports is None:
                    from .reports import ReportsInterface
                    self._reports = ReportsInterface(self)
        return self._reports

    @property
    def gateway(self):
        """Access gateway interface: client.gateway.request_reverse_invoice(...)"""
        if self._gateway is None:
            with self._lock:
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

        with self._lock:
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
    ) -> Dict[str, Any]:
        """Core request dispatcher with resilience mapping."""
        _attrs: Dict[str, Any] = {"http.method": method, "http.path": path}
        if idempotency_key:
            _attrs["idempotency_key"] = idempotency_key

        with _span("kra_etims.request", _attrs):
            self._authenticate()
            url     = self._build_url(path)
            headers = self._build_auth_headers(idempotency_key)

            try:
                resp = self._http.request(method, url, json=json, headers=headers)
                return self._parse_response(resp, method, idempotency_key)

            except httpx.ConnectError:
                # TCP handshake never completed — request was never sent.
                raise TIaaSUnavailableError()
            except httpx.ConnectTimeout:
                # TCP handshake never completed — request was never sent.
                raise TIaaSUnavailableError()
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                    httpx.ReadError):
                # Request was sent (or partially sent/received) — state is ambiguous
                # for mutating methods (POST/PUT/DELETE/PATCH). The server may have
                # committed the change before the connection dropped.
                if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
                    raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key)
                raise TIaaSUnavailableError()
            except httpx.RequestError as exc:
                raise TIaaSUnavailableError() from exc

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
        """
        Category 1 (Pre-step): Trigger the middleware device wake-up.

        The middleware calls the KRA Sandbox API, retrieves the cmcKey for
        the configured tenant, encrypts it with AES-256, and persists it to
        the TenantDevice record.
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
        """Category 4: Save or update item master data."""
        return self._request(
            "POST", "/v2/etims/item",
            json=data.model_dump(mode="json", exclude_none=True),
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
            return self._request(
                "POST", "/v2/etims/sale",
                json=invoice.model_dump(mode="json", exclude_none=True),
                idempotency_key=idempotency_key,
            )

    # ------------------------------------------------------------------
    # Category 7 — Credit Notes
    # ------------------------------------------------------------------

    def issue_credit_note(
        self,
        original_purchase_id: int,
        reason: Optional[str] = None,
        items: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Issue a credit note against a previously signed sale.

        Posts to ``POST /v2/etims/sale/{id}/credit-note``.  The middleware
        retrieves the original invoice, constructs the KRA credit note payload,
        signs it via the VSCU JAR, and returns the signed receipt.

        If ``items`` is ``None`` the full original invoice is reversed.
        Supply ``items`` only to partially reverse specific line items.

        :param original_purchase_id: TIaaS database ID of the original sale.
        :param reason: Human-readable reversal reason (optional).
        :param items: Partial line items to reverse; ``None`` reverses the full invoice.
        :raises CreditNoteConflictError: HTTP 409 — a credit note already exists for
            this sale.  Retrieve the existing credit note instead of retrying.
        :raises KRAeTIMSError: HTTP 404 — original sale not found.
        :raises KRAConnectivityTimeoutError: VSCU offline ceiling breached (HTTP 503).
        """
        with _span("kra_etims.issue_credit_note", {"sale.id": str(original_purchase_id)}):
            body: Dict[str, Any] = {}
            if reason is not None:
                body["reason"] = reason
            if items is not None:
                body["items"] = items
            return self._request(
                "POST", f"/v2/etims/sale/{original_purchase_id}/credit-note",
                json=body if body else None,
            )

    def submit_reverse_invoice(self, invoice: ReverseInvoice) -> Dict[str, Any]:
        """
        .. deprecated::
            ``/v2/etims/reverse`` is no longer active.  Use
            :meth:`issue_credit_note` which posts to
            ``POST /v2/etims/sale/{id}/credit-note`` and handles 409 conflicts.
        """
        warnings.warn(
            "submit_reverse_invoice() is deprecated and targets a removed endpoint. "
            "Use issue_credit_note(original_purchase_id, reason, items) instead.",
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
        """Category 8: Stock adjustment / transfer / loss."""
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
    ) -> Dict[str, Any]:
        """
        Submit a stock adjustment to ``POST /v2/etims/stock/adjustment``.

        The middleware assigns a monotonic ``sarNo``, computes all financial
        totals server-side, signs the movement via the VSCU JAR, and persists
        the event-sourced stock ledger entry.

        A 201 response means the VSCU signed synchronously (``status=SYNCED``).
        A 202 response means the VSCU call failed transiently and the movement
        is queued for retry (``status=FAILED``); issue a **new** request with
        the same data — do not retry with the same ``sarNo``.

        :param lines: At least one :class:`StockAdjustmentLine`.
        :param cust_tin: Customer KRA TIN (B2B movements only).  Validated
            against the KRA PIN pattern client-side before sending.
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
        return self._request(
            "POST", "/v2/etims/stock/adjustment",
            json=request.model_dump(mode="json", exclude_none=True),
        )

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
        from ``tin:invcNo``.  On a retry the middleware deduplicates safely, and
        KRADuplicateInvoiceError (code 12) is treated as a confirmed success —
        the invoice was already processed on a previous attempt.
        """
        with _span("kra_etims.flush_offline_queue", {"queue.size": len(invoices)}):
            results = []
            for invoice in invoices:
                idem_key = f"{invoice.tin}:{invoice.invcNo}"
                try:
                    res = self.submit_sale(invoice, idempotency_key=idem_key)
                    results.append(
                        {"invoice_no": invoice.invcNo, "status": "success", "data": res}
                    )
                except KRADuplicateInvoiceError:
                    # Code 12 = already processed on a prior attempt.  The fiscal
                    # record exists on KRA — this is a safe idempotent success.
                    results.append(
                        {"invoice_no": invoice.invcNo, "status": "already_processed"}
                    )
                except Exception as exc:
                    results.append(
                        {"invoice_no": invoice.invcNo, "status": "error", "message": str(exc)}
                    )
            return results

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return self._request("GET", f"/v2/etims/compliance/{pin}")
