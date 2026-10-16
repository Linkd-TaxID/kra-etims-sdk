"""Middleware operator recovery. Evidence must bind the original durable identity.
Production registration remains gated pending real-device protocol evidence.
"""
from urllib.parse import urlencode


def _inspect_path(tin, branch, kind, key):
    return "/v2/admin/prerequisites?" + urlencode(dict(tin=tin, branch=branch, kind=kind, key=key))


def _sale_path(tin, branch, key):
    return "/v2/admin/prerequisites/sale?" + urlencode(dict(tin=tin, branch=branch, key=key))


class PrerequisiteInterface:
    def __init__(self, client):
        self._client = client

    def inspect(self, tin, branch, kind, key):
        return self._client._request("GET", _inspect_path(tin, branch, kind, key))["cases"]

    def inspect_sale_transport(self, queue_id):
        return self._client._request("GET", f"/v2/admin/reconciliation/{queue_id}/transport")

    def inspect_sale(self, tin, branch, key):
        return self._client._request("GET", _sale_path(tin, branch, key))["cases"]

    def resolve(self, *, tin, branch, kind, key, payload_sha256, outcome, evidence, reason):
        return self._client._request("POST", "/v2/admin/prerequisites/resolve", json=dict(
            tin=tin, branch=branch, kind=kind, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=evidence, reason=reason))

    def release_sale(self, *, tin, branch, key, request_hash, evidence, reason):
        return self._client._request("POST", "/v2/admin/prerequisites/release-sale", json=dict(
            tin=tin, branch=branch, key=key, requestHash=request_hash, evidence=evidence, reason=reason))


class AsyncPrerequisiteInterface:
    def __init__(self, client):
        self._client = client

    async def inspect(self, tin, branch, kind, key):
        return (await self._client._request("GET", _inspect_path(tin, branch, kind, key)))["cases"]

    async def inspect_sale_transport(self, queue_id):
        return await self._client._request("GET", f"/v2/admin/reconciliation/{queue_id}/transport")

    async def inspect_sale(self, tin, branch, key):
        return (await self._client._request("GET", _sale_path(tin, branch, key)))["cases"]

    async def resolve(self, *, tin, branch, kind, key, payload_sha256, outcome, evidence, reason):
        return await self._client._request("POST", "/v2/admin/prerequisites/resolve", json=dict(
            tin=tin, branch=branch, kind=kind, key=key, payloadSha256=payload_sha256,
            outcome=outcome, evidence=evidence, reason=reason))

    async def release_sale(self, *, tin, branch, key, request_hash, evidence, reason):
        return await self._client._request("POST", "/v2/admin/prerequisites/release-sale", json=dict(
            tin=tin, branch=branch, key=key, requestHash=request_hash, evidence=evidence, reason=reason))


def original_transport_evidence(snapshot, *, outcome, provenance, quiescence_evidence=None):
    """Bind a supplied operator assertion to the immutable original transport.

    This helper does not verify an upstream outcome or authorize a replay.
    Never include communication keys in provenance or evidence attachments.
    """
    import hashlib
    import json
    if hasattr(snapshot,"model_dump"):
        snapshot=snapshot.model_dump(by_alias=True)
    if outcome not in {"REGISTERED", "REJECTED", "SIGNED", "NOT_SENT"}:
        raise ValueError("Unsupported original outcome")
    if not provenance or not provenance.strip():
        raise ValueError("Outcome provenance is required")
    owner=snapshot.get("executionOwner",snapshot.get("execution_owner"))
    if owner and not snapshot.get("transportReturned",snapshot.get("transport_returned")) and not quiescence_evidence:
        raise ValueError("Orphaned execution requires original-worker quiescence evidence; an epoch is not cancellation")
    return json.dumps(dict(version=1, outcome=outcome, provenance=provenance,
        executionOwner=owner, quiescenceEvidence=quiescence_evidence,
        identitySha256=hashlib.sha256(snapshot["identity"].encode()).hexdigest(),
        businessSha256=snapshot["business_sha256"], wireSha256=snapshot["wire_sha256"]))
