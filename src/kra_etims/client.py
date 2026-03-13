import time
import os
import threading
import requests
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

        self._session = requests.Session()
        self._session.headers.update({"X-TIaaS-Service": "Handshake"})
        self._lock = threading.Lock()

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
                resp = self._session.post(
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
            resp = self._session.request(
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
            if (
                hasattr(exc, "response")
                and exc.response is not None
                and exc.response.status_code == 503
            ):
                raise KRAConnectivityTimeoutError()
            raise

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
    # Category 7 — Reverse Invoices
    # ------------------------------------------------------------------

    def submit_reverse_invoice(self, invoice: ReverseInvoice) -> Dict[str, Any]:
        """Category 7: Submit a Reverse / Credit Note Invoice."""
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

    def batch_update_stock(self, items: List[StockItem]) -> List[Dict[str, Any]]:
        """Category 8: High-volume batch update with 500-item chunking."""
        results = []
        for i in range(0, len(items), 500):
            chunk = items[i : i + 500]
            try:
                payload = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in chunk
                ]
                res = self._request(
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
