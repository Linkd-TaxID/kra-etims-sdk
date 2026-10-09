"""Platform credentials mint scoped capabilities; fiscal clients receive only those capabilities."""
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from typing import Literal
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Environment = Literal["SANDBOX", "PRODUCTION"]


class BranchSession(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    accessToken: SecretStr
    expiresAt: datetime
    capabilityId: int = Field(gt=0)
    grantId: int = Field(gt=0)
    environment: Environment
    scopes: frozenset[str]

    @field_validator("expiresAt")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Session expiry requires a time zone")
        return value


def _selection(grant_id, environment, scopes):
    if not isinstance(scopes, (set, frozenset)):
        raise ValueError("Scopes must be an explicit set")
    selected = frozenset(scopes)
    if isinstance(grant_id, bool) or not isinstance(grant_id, int) or grant_id < 1:
        raise ValueError("Positive operator-issued grant ID required")
    if environment not in {"SANDBOX", "PRODUCTION"} or not selected or any(not isinstance(s, str) or not s.strip() for s in selected):
        raise ValueError("Explicit environment and nonempty scopes required")
    return grant_id, environment, selected


def _check(session, selection):
    grant, environment, scopes = selection
    if session.grantId != grant or session.environment != environment or session.scopes != scopes:
        raise ValueError("Session does not match the requested grant, environment and scopes")
    if session.expiresAt <= datetime.now(timezone.utc) + timedelta(seconds=30):
        raise ValueError("Session expiry is too close")
    return session


class PlatformSessions:
    def __init__(self, platform_secret: str, *, base_url: str):
        if not platform_secret or not base_url:
            raise ValueError("Explicit platform credential and TaxID URL required")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {platform_secret}"}, timeout=30)
        self._base_url = base_url
        self._cache = {}
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            self._cache.clear()
            self._http.close()

    def session(self, grant_id: int, *, environment: Environment, scopes: set[str]) -> BranchSession:
        selection = _selection(grant_id, environment, scopes)
        with self._lock:
            cached = self._cache.get(selection)
            if cached and cached.expiresAt > datetime.now(timezone.utc) + timedelta(seconds=30):
                return cached.model_copy(deep=True)
            self._cache.pop(selection, None)
            response = self._http.post("/v2/platform/branch-sessions", json={"grantId": grant_id, "scopes": sorted(selection[2]), "ttlSeconds": 900})
            response.raise_for_status()
            result = _check(BranchSession.model_validate(response.json()), selection)
            self._cache[selection] = result
            return result.model_copy(deep=True)

    def branch_client(self, grant_id: int, *, environment: Environment, scopes: set[str]):
        from .client import KRAeTIMSClient
        capability = self.session(grant_id, environment=environment, scopes=scopes)
        return KRAeTIMSClient(api_key=capability.accessToken.get_secret_value(), base_url=self._base_url)

    def revoke(self, capability_id: int):
        if isinstance(capability_id, bool) or not isinstance(capability_id, int) or capability_id < 1:
            raise ValueError("Positive capability ID required")
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if v.capabilityId != capability_id}
            response = self._http.post(f"/v2/platform/branch-sessions/{capability_id}/revoke")
            response.raise_for_status()


class AsyncPlatformSessions:
    def __init__(self, platform_secret: str, *, base_url: str):
        if not platform_secret or not base_url:
            raise ValueError("Explicit platform credential and TaxID URL required")
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {platform_secret}"}, timeout=30)
        self._base_url = base_url
        self._cache = {}
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        async with self._lock:
            self._cache.clear()
            await self._http.aclose()

    async def session(self, grant_id: int, *, environment: Environment, scopes: set[str]) -> BranchSession:
        selection = _selection(grant_id, environment, scopes)
        async with self._lock:
            cached = self._cache.get(selection)
            if cached and cached.expiresAt > datetime.now(timezone.utc) + timedelta(seconds=30):
                return cached.model_copy(deep=True)
            self._cache.pop(selection, None)
            response = await self._http.post("/v2/platform/branch-sessions", json={"grantId": grant_id, "scopes": sorted(selection[2]), "ttlSeconds": 900})
            response.raise_for_status()
            result = _check(BranchSession.model_validate(response.json()), selection)
            self._cache[selection] = result
            return result.model_copy(deep=True)

    async def branch_client(self, grant_id: int, *, environment: Environment, scopes: set[str]):
        from .async_client import AsyncKRAeTIMSClient
        capability = await self.session(grant_id, environment=environment, scopes=scopes)
        return AsyncKRAeTIMSClient(api_key=capability.accessToken.get_secret_value(), base_url=self._base_url)

    async def revoke(self, capability_id: int):
        if isinstance(capability_id, bool) or not isinstance(capability_id, int) or capability_id < 1:
            raise ValueError("Positive capability ID required")
        async with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if v.capabilityId != capability_id}
            response = await self._http.post(f"/v2/platform/branch-sessions/{capability_id}/revoke")
            response.raise_for_status()
