import time
import os
import threading
import requests
from typing import Optional, Dict, Any, Union
from .middleware import sanitize_kra_url
from .exceptions import (
    KRAConnectivityTimeoutError, 
    KRAeTIMSAuthError,
    TIaaSUnavailableError,
    TIaaSAmbiguousStateError
)
from .models import (
    DeviceInit, DataSyncRequest, BranchInfo, ItemSave, 
    ImportItem, SaleInvoice, ReverseInvoice, StockItem
)
from typing import List

class KRAeTIMSClient:
    """
    Senior-level SDK for TIaaS (Tax Identity as a Service).
    Acts as a high-performance remote control for the stateful TIaaS Middleware.
    
    This client manages the 'Public Handshake' with api.taxid.co.ke, while the 
    middleware handles the underlying VSCU JAR orchestration and AES-256 encryption.
    """
    
    def __init__(self, client_id: str, client_secret: str, base_url: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        
        # Priority: TAXID_API_URL > constructor argument > Hardcoded Production URL
        env_url = (os.getenv("TAXID_API_URL") or "").strip()
        default_url = "https://taxid-production.up.railway.app"
        raw_url = env_url or base_url or default_url
        self.base_url = raw_url.strip().rstrip('/')
        
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        
        # Connection Pooling and Handshake Compliance
        self._session = requests.Session()
        self._session.headers.update({
            "X-TIaaS-Service": "Handshake"
        })
        self._lock = threading.Lock()
        
    def _authenticate(self) -> None:
        """
        Implements OAuth 2.0 flow with a 60-second proactive refresh buffer.
        Ensures zero-latency for high-volume B2B operations by preemptively 
        refreshing credentials before they expire.
        """
        with self._lock:
            now = time.time()
            # Double-check inside lock to prevent redundant refreshes
            if not self._access_token or (self._token_expiry - now) < 60:
                resp = self._session.post(
                    f"{self.base_url}/oauth/token",
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials"}
                )
                if resp.status_code != 200:
                    raise KRAeTIMSAuthError(f"TIaaS Authentication failed: {resp.text}")
                
                data = resp.json()
                self._access_token = data.get("access_token")
                # expires_in usually 3600; we subtract the buffer internally in the check
                self._token_expiry = now + data.get("expires_in", 3600)

    @sanitize_kra_url
    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Core request dispatcher with resilience mapping and URL sanitization.
        Trailing spaces are stripped programmatically to fix GavaConnect silent failures.
        """
        self._authenticate()
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        
        if idempotency_key:
            headers["X-TIaaS-Idempotency-Key"] = idempotency_key
        
        try:
            resp = self._session.request(method, url, json=json, headers=headers, timeout=30)
            
            # Map HTTP 503 to KRAConnectivityTimeoutError
            # Triggered when the 24-hour VSCU offline ceiling is breached.
            if resp.status_code == 503:
                raise KRAConnectivityTimeoutError(
                    "KRA Connectivity Timeout: The 24-hour VSCU offline ceiling has been breached. "
                    "TIaaS cannot sign invoices until connectivity to KRA GavaConnect is restored."
                )
                
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # If it's a POST/PUT/DELETE, we don't know if the request reached the server
            if method.upper() in ["POST", "PUT", "DELETE", "PATCH"]:
                raise TIaaSAmbiguousStateError()
            # Map ConnectionError to TIaaSUnavailableError for Railway instance issues (GETs)
            raise TIaaSUnavailableError()
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 503:
                raise KRAConnectivityTimeoutError()
            raise e

    def initialize_device(self, data: DeviceInit) -> Dict[str, Any]:
        """Category 1: Initialize device/branch on eTIMS."""
        return self._request("POST", "/v2/etims/init", json=data.model_dump(mode='json', exclude_none=True))

    def sync_data(self, data: DataSyncRequest) -> Dict[str, Any]:
        """Category 2: Data Synchronization (Codes, Items, Branches)."""
        return self._request("POST", "/v2/etims/sync", json=data.model_dump(mode='json', exclude_none=True))

    def save_item(self, data: ItemSave) -> Dict[str, Any]:
        """Category 4: Save or Update Item master data."""
        return self._request("POST", "/v2/etims/item", json=data.model_dump(mode='json', exclude_none=True))

    @sanitize_kra_url
    def submit_sale(self, invoice: SaleInvoice, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Category 6: Submit a Sales Invoice (Normal/Copy/Training)."""
        return self._request("POST", "/v2/etims/sale", json=invoice.model_dump(mode='json', exclude_none=True), idempotency_key=idempotency_key)

    @sanitize_kra_url
    def check_compliance(self, pin: str) -> Dict[str, Any]:
        """Verify device compliance at the Railway endpoint."""
        return self._request("GET", f"/v2/etims/compliance/{pin}")

    def submit_reverse_invoice(self, invoice: ReverseInvoice) -> Dict[str, Any]:
        """Category 7: Submit a Reverse/Credit Note Invoice."""
        return self._request("POST", "/v2/etims/reverse", json=invoice.model_dump(mode='json', exclude_none=True))

    def update_stock(self, data: StockItem) -> Dict[str, Any]:
        """Category 8: Stock management (Adjustment/Transfer/Loss)."""
        return self._request("POST", "/v2/etims/stock", json=data.model_dump(mode='json', exclude_none=True))

    def flush_offline_queue(self, invoices: List[SaleInvoice]) -> List[Dict[str, Any]]:
        """
        Submits a batch of offline invoices once connectivity is restored.
        Returns a list of processing results.
        """
        results = []
        for invoice in invoices:
            try:
                res = self.submit_sale(invoice)
                results.append({"invoice_no": invoice.invcNo, "status": "success", "data": res})
            except Exception as e:
                results.append({"invoice_no": invoice.invcNo, "status": "error", "message": str(e)})
        return results

    def batch_update_stock(self, items: List[StockItem]) -> List[Dict[str, Any]]:
        """
        Category 8: High-volume batch update with 500-item chunking.
        Sends each chunk of 500 items as a single POST request to the batch endpoint.
        """
        results = []
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            try:
                # We send the entire chunk as a list
                payload = [item.model_dump(mode='json', exclude_none=True) for item in chunk]
                res = self._request("POST", "/v2/etims/stock/batch", json={"items": payload})
                results.append({"chunk": i//500, "status": "success", "count": len(chunk)})
            except Exception as e:
                results.append({"chunk": i//500, "status": "error", "message": str(e)})
        return results
