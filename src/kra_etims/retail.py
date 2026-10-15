"""Local middleware recovery; these methods never send a fiscal operation to KRA."""
from urllib.parse import urlencode
from .models import StockRecoveryEvidence

def _evidence(value):
    return value.model_dump_json() if isinstance(value, StockRecoveryEvidence) else value


def _path(kind, tin, branch, key):
    return "/v2/admin/retail/" + kind + "?" + urlencode(dict(tin=tin, branch=branch, key=key))


class RetailInterface:
    def __init__(self, client):
        self._client = client

    def inspect_stock(self, tin, branch, key):
        return self._client._request("GET", _path("stock", tin, branch, key))["cases"]

    def inspect_stock_master(self, tin, branch, key):
        return self._client._request("GET", _path("stock-master", tin, branch, key))

    def resolve_stock_master(self, *, tin, branch, key, payload_sha256, outcome, evidence, reason):
        return self._client._request("POST", "/v2/admin/retail/stock-master/resolve", json=dict(
            tin=tin, branch=branch, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=_evidence(evidence), reason=reason))

    def inspect_admission(self, tin, branch, key):
        return self._client._request("GET", _path("admission", tin, branch, key))["cases"]

    def resolve_stock(self, *, tin, branch, key, payload_sha256, outcome, evidence, reason):
        return self._client._request("POST", "/v2/admin/retail/stock/resolve", json=dict(
            tin=tin, branch=branch, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=_evidence(evidence), reason=reason))

    def abandon_admission(self, *, tin, branch, key, request_hash, evidence, reason):
        return self._client._request("POST", "/v2/admin/retail/admission/abandon", json=dict(
            tin=tin, branch=branch, key=key, requestHash=request_hash, evidence=evidence, reason=reason))


class AsyncRetailInterface:
    def __init__(self, client):
        self._client = client

    async def inspect_stock(self, tin, branch, key):
        return (await self._client._request("GET", _path("stock", tin, branch, key)))["cases"]

    async def inspect_stock_master(self, tin, branch, key):
        return await self._client._request("GET", _path("stock-master", tin, branch, key))

    async def resolve_stock_master(self, *, tin, branch, key, payload_sha256, outcome, evidence, reason):
        return await self._client._request("POST", "/v2/admin/retail/stock-master/resolve", json=dict(
            tin=tin, branch=branch, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=_evidence(evidence), reason=reason))

    async def inspect_admission(self, tin, branch, key):
        return (await self._client._request("GET", _path("admission", tin, branch, key)))["cases"]

    async def resolve_stock(self, *, tin, branch, key, payload_sha256, outcome, evidence, reason):
        return await self._client._request("POST", "/v2/admin/retail/stock/resolve", json=dict(
            tin=tin, branch=branch, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=_evidence(evidence), reason=reason))

    async def abandon_admission(self, *, tin, branch, key, request_hash, evidence, reason):
        return await self._client._request("POST", "/v2/admin/retail/admission/abandon", json=dict(
            tin=tin, branch=branch, key=key, requestHash=request_hash, evidence=evidence, reason=reason))
