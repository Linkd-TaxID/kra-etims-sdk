import time
import os
import threading
import warnings
import requests
from typing import Optional, Dict, Any, List

# KRA eTIMS success result codes — only two variants are documented:
#   "000"  per KRA VSCU/OSCU Specification v2.0 §4.14/§4.18 (JSON HTTP API)
#   "00"   per KRA TIS Specification v2.0 §21.6.3 (VSCU JAR XML serial protocol,
#          but also emitted by the VSCU JAR's HTTP endpoint in observed deployments)
# "0" and "0000" appear in no official KRA document and are excluded.
_KRA_SUCCESS_CODES: frozenset[str] = frozenset({"00", "000"})


def _is_kra_success(result: dict) -> bool:
    """
    Return True when the KRA result code indicates success.

    Handles two officially documented success code variants — "000" (JSON API spec)
    and "00" (VSCU JAR HTTP endpoint, derived from the TIS XML serial protocol).
    An absent ``resultCd`` key means this is a TIaaS-native response; HTTP errors
    are already handled by ``raise_for_status()`` so absent == no KRA error.
    """
    if "resultCd" not in result:
        return True  # TIaaS-native response; no KRA result code to check
    return str(result["resultCd"]).strip() in _KRA_SUCCESS_CODES


from .middleware import sanitize_kra_url
from .exceptions import (
    KRA_ERROR_MAP,
    KRAConnectivityTimeoutError,
    KRADuplicateInvoiceError,
    KRAeTIMSAuthError,
    KRAeTIMSError,
    TIaaSUnavailableError,
    TIaaSAmbiguousStateError,
    CreditNoteConflictError,
)
from .models import (
    DeviceInit, DataSyncRequest, BranchInfo, ItemSave,
    ImportItem, SaleInvoice, ReverseInvoice, StockItem,
    StockAdjustmentLine, StockAdjustmentRequest,
)


class KRAeTIMSClient:
    """
    Sync SDK for TIaaS (Tax Identity as a Service).

    Acts as a high-performance remote control for the stateful TIaaS
    middleware — which handles VSCU JAR orchestration, AES-256 encryption,
    and KRA GavaConnect communication.

    Auth modes (in priority order):
      1. API Key  — env TAXID_API_KEY or ``api_key`` kwarg (preferred B2B)
      2. OAuth 2.0 — client_credentials with auto-refresh

    Attach sub-interfaces:
      client.reports.get_daily_z("2026-03-11")
      client.gateway.request_reverse_invoice(phone_number=..., amount=...)
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

        # API Key auth — env var takes priority over constructor arg
        self._api_key: Optional[str] = os.getenv("TAXID_API_KEY") or api_key

        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

        # Thread-local storage: each thread gets its own requests.Session.
        # A single shared Session corrupts the connection pool under concurrent
        # Celery workers — connections are not returned cleanly when multiple
        # threads call session.request() simultaneously.
        self._session_local = threading.local()
        self._lock = threading.Lock()  # guards token refresh only

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
    # Session management — one Session per thread
    # ------------------------------------------------------------------

    def _get_session(self) -> requests.Session:
        """
        Return the requests.Session for the current thread, creating one if needed.

        requests.Session is not thread-safe: concurrent threads sharing a single
        Session instance can corrupt the urllib3 connection pool (connections not
        returned, duplicate headers mutated mid-flight). Using threading.local()
        gives each Celery worker its own Session with its own connection pool,
        eliminating contention without sacrificing connection reuse within a thread.
        """
        if not hasattr(self._session_local, "session"):
            session = requests.Session()
            session.headers.update({"X-TIaaS-Service": "Handshake"})
            self._session_local.session = session
        return self._session_local.session

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
    # Authentication
    # ------------------------------------------------------------------

    def _authenticate(self) -> None:
        """
        OAuth 2.0 Client Credentials with thread-safe double-checked locking
        and a 60-second refresh buffer.

        Skipped entirely when an API key is configured.
        """
        if self._api_key:
            return

        # Outer pre-lock check: skip lock acquisition entirely on the hot path
        # when the token is valid. Prevents all concurrent threads bottlenecking
        # on self._lock for a nanosecond-level expiry test.
        now = time.time()
        if self._access_token and (self._token_expiry - now) >= 60:
            return

        with self._lock:
            now = time.time()  # re-read: time elapsed while waiting for lock
            if not self._access_token or (self._token_expiry - now) < 60:
                resp = self._get_session().post(
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

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error_response(self, response_json: Any) -> None:
        """
        Intercept opaque KRA application errors embedded in HTTP 200 bodies.

        KRA returns ``resultCd: "00"`` on success.  Any other code maps to a
        precise, actionable exception so developers never parse raw JSON.
        """
        if not isinstance(response_json, dict):
            raise KRAeTIMSError(
                f"Unexpected response format: expected JSON object, "
                f"got {type(response_json).__name__}"
            )

        if _is_kra_success(response_json):
            return

        result_cd = str(response_json.get("resultCd", "")).strip()
        result_msg = response_json.get("resultMsg", "Unknown KRA error")

        if result_cd in KRA_ERROR_MAP:
            exc_class, default_msg = KRA_ERROR_MAP[result_cd]
            raise exc_class(f"{default_msg}: {result_msg}")

        raise KRAeTIMSError(f"KRA Error [{result_cd}]: {result_msg}")

    # ------------------------------------------------------------------
    # Core request dispatcher
    # ------------------------------------------------------------------

    @sanitize_kra_url
    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Core request dispatcher with resilience mapping and URL sanitization.

        Applies ``sanitize_kra_url`` to strip whitespace from path strings —
        the mandatory fix for KRA GavaConnect silent routing failures.
        """
        self._authenticate()
        url = f"{self.base_url}/{path.lstrip('/')}"

        if self._api_key:
            headers: Dict[str, str] = {"X-API-Key": self._api_key}
        else:
            headers = {"Authorization": f"Bearer {self._access_token}"}

        if idempotency_key:
            headers["X-TIaaS-Idempotency-Key"] = idempotency_key

        try:
            resp = self._get_session().request(
                method, url, json=json, headers=headers, timeout=30
            )

            if resp.status_code == 503:
                raise KRAConnectivityTimeoutError()

            resp.raise_for_status()
            try:
                response_data = resp.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                raise KRAeTIMSError(
                    f"Non-JSON response from TIaaS [{resp.status_code}]: "
                    f"{resp.text[:200]}"
                )
            self._handle_error_response(response_data)
            return response_data

        except requests.exceptions.ConnectTimeout:
            # TCP handshake never completed — request was never sent.
            raise TIaaSUnavailableError()
        except requests.exceptions.ReadTimeout:
            # Request was sent; response never arrived — state is ambiguous.
            # Must precede ConnectionError: ReadTimeout is a subclass of it.
            if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
                raise TIaaSAmbiguousStateError(idempotency_key=idempotency_key)
            raise TIaaSUnavailableError()
        except requests.exceptions.ConnectionError:
            raise TIaaSUnavailableError()
        except requests.exceptions.RequestException as exc:
            if hasattr(exc, "response") and exc.response is not None:
                status_code = exc.response.status_code
                if status_code == 503:
                    raise KRAConnectivityTimeoutError()
                if status_code == 409:
                    try:
                        body = exc.response.json()
                        msg = body.get("message") or body.get("error") or exc.response.text[:200]
                    except Exception:
                        msg = exc.response.text[:200]
                    raise CreditNoteConflictError(msg) from exc
                if status_code == 404:
                    raise KRAeTIMSError(
                        f"Resource not found (HTTP 404): {exc.response.text[:200]}"
                    ) from exc
                raise KRAeTIMSError(f"TIaaS returned HTTP {status_code}") from exc
            raise KRAeTIMSError("TIaaS returned an error (no response)") from exc

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

    @sanitize_kra_url
    def submit_sale(
        self,
        invoice: SaleInvoice,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Category 6: Submit a Sales Invoice (Normal / Copy / Training)."""
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

    @sanitize_kra_url
    def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance for the given TIN/PIN."""
        return self._request("GET", f"/v2/etims/compliance/{pin}")
