import time
import os
import asyncio
import httpx
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

class AsyncKRAeTIMSClient:
    """
    Asynchronous SDK for TIaaS (Tax Identity as a Service).
    Optimized for non-blocking I/O in frameworks like FastAPI.
    """
    
    def __init__(self, client_id: str, client_secret: str, base_url: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        
        env_url = (os.getenv("TAXID_API_URL") or "").strip()
        default_url = "https://taxid-production.up.railway.app"
        raw_url = env_url or base_url or default_url
        self.base_url = raw_url.strip().rstrip('/')
        
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        
        self._client = httpx.AsyncClient(
            headers={"X-TIaaS-Service": "Handshake"},
            timeout=30.0
        )
        self._lock = asyncio.Lock()
        
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.aclose()

    async def aclose(self):
        """Manually close the underlying httpx client."""
        await self._client.aclose()

    async def _authenticate(self) -> None:
        """
        Async thread-safe OAuth 2.0 flow using asyncio.Lock.
        """
        async with self._lock:
            now = time.time()
            if not self._access_token or (self._token_expiry - now) < 60:
                try:
                    resp = await self._client.post(
                        f"{self.base_url}/oauth/token",
                        auth=(self.client_id, self.client_secret),
                        data={"grant_type": "client_credentials"}
                    )
                    if resp.status_code != 200:
                        raise KRAeTIMSAuthError(f"TIaaS Authentication failed: {resp.text}")
                    
                    data = resp.json()
                    self._access_token = data.get("access_token")
                    self._token_expiry = now + data.get("expires_in", 3600)
                except httpx.RequestError as e:
                    raise KRAeTIMSAuthError(f"TIaaS Authentication unreachable: {str(e)}")

    @sanitize_kra_url
    async def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Core async request dispatcher."""
        await self._authenticate()
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        
        if idempotency_key:
            headers["X-TIaaS-Idempotency-Key"] = idempotency_key
            
        try:
            resp = await self._client.request(method, url, json=json, headers=headers)
            
            if resp.status_code == 503:
                raise KRAConnectivityTimeoutError()
                
            resp.raise_for_status()
            return resp.json()
            
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # Immediate failure to connect
            raise TIaaSUnavailableError()
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RequestError):
            # Ambiguous state for writes
            if method.upper() in ["POST", "PUT", "DELETE", "PATCH"]:
                raise TIaaSAmbiguousStateError()
            raise TIaaSUnavailableError()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                raise KRAConnectivityTimeoutError()
            raise e

    async def initialize_device(self, data: DeviceInit) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/init", json=data.model_dump(mode='json', exclude_none=True))

    async def sync_data(self, data: DataSyncRequest) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/sync", json=data.model_dump(mode='json', exclude_none=True))

    async def save_item(self, data: ItemSave) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/item", json=data.model_dump(mode='json', exclude_none=True))

    @sanitize_kra_url
    async def submit_sale(self, invoice: SaleInvoice, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/sale", json=invoice.model_dump(mode='json', exclude_none=True), idempotency_key=idempotency_key)

    @sanitize_kra_url
    async def check_compliance(self, pin: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v2/etims/compliance/{pin}")

    async def submit_reverse_invoice(self, invoice: ReverseInvoice) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/reverse", json=invoice.model_dump(mode='json', exclude_none=True))

    async def update_stock(self, data: StockItem) -> Dict[str, Any]:
        return await self._request("POST", "/v2/etims/stock", json=data.model_dump(mode='json', exclude_none=True))

    async def flush_offline_queue(self, invoices: List[SaleInvoice]) -> List[Dict[str, Any]]:
        """
        Submits a batch of offline invoices once connectivity is restored.
        Returns a list of processing results.
        """
        results = []
        for invoice in invoices:
            try:
                res = await self.submit_sale(invoice)
                results.append({"invoice_no": invoice.invcNo, "status": "success", "data": res})
            except Exception as e:
                results.append({"invoice_no": invoice.invcNo, "status": "error", "message": str(e)})
        return results

    async def batch_update_stock(self, items: List[StockItem]) -> List[Dict[str, Any]]:
        """
        Category 8: High-volume batch update with 500-item chunking (Async).
        Sends each chunk to the batch endpoint.
        """
        results = []
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            try:
                payload = [item.model_dump(mode='json', exclude_none=True) for item in chunk]
                res = await self._request("POST", "/v2/etims/stock/batch", json={"items": payload})
                results.append({"chunk": i//500, "status": "success", "count": len(chunk)})
            except Exception as e:
                results.append({"chunk": i//500, "status": "error", "message": str(e)})
        return results
