import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kra_etims import platform_sessions
from kra_etims.platform_sessions import AsyncPlatformSessions, PlatformSessions


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 22, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(platform_sessions, "datetime", Clock)
    return Clock


def session_data(clock, **changes):
    return {
        "accessToken": "branch-token",
        "expiresAt": (clock.current + timedelta(seconds=600)).isoformat(),
        "capabilityId": 9,
        "grantId": 4,
        "environment": "SANDBOX",
        "scopes": ["sale:create"],
        **changes,
    }


@pytest.mark.parametrize("changes", [
    {"grantId": 5},
    {"environment": "PRODUCTION"},
    {"scopes": ["sale:read"]},
    {"expiresAt": "2026-10-22T00:00:00"},
    {"expiresAt": "2026-10-22T00:00:00Z"},
    {"expiresAt": "2026-10-22T00:00:30Z"},
])
def test_sync_rejects_unusable_exchange_without_caching(httpx_mock, clock, changes):
    httpx_mock.add_response(json=session_data(clock, **changes))
    httpx_mock.add_response(json=session_data(clock))
    with PlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        with pytest.raises(ValueError):
            sessions.session(4, environment="SANDBOX", scopes={"sale:create"})
        assert not sessions._cache
        assert sessions.session(4, environment="SANDBOX", scopes={"sale:create"}).capabilityId == 9


@pytest.mark.parametrize("changes", [
    {"grantId": 5},
    {"environment": "PRODUCTION"},
    {"scopes": ["sale:read"]},
    {"expiresAt": "2026-10-22T00:00:00"},
    {"expiresAt": "2026-10-22T00:00:00Z"},
    {"expiresAt": "2026-10-22T00:00:30Z"},
])
async def test_async_rejects_unusable_exchange_without_caching(httpx_mock, clock, changes):
    httpx_mock.add_response(json=session_data(clock, **changes))
    httpx_mock.add_response(json=session_data(clock))
    async with AsyncPlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        with pytest.raises(ValueError):
            await sessions.session(4, environment="SANDBOX", scopes={"sale:create"})
        assert not sessions._cache
        assert (await sessions.session(4, environment="SANDBOX", scopes={"sale:create"})).capabilityId == 9


def test_sync_refresh_denial_and_failed_revocation_recover(httpx_mock, clock):
    selection = dict(environment="SANDBOX", scopes={"sale:create"})
    httpx_mock.add_response(json=session_data(clock))
    with PlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        first = sessions.session(4, **selection)
        first.environment = "PRODUCTION"
        assert sessions.session(4, **selection).environment == "SANDBOX"
        clock.current += timedelta(seconds=570)
        httpx_mock.add_response(status_code=403)
        with pytest.raises(httpx.HTTPStatusError):
            sessions.session(4, **selection)
        assert not sessions._cache
        httpx_mock.add_response(json=session_data(clock, capabilityId=10))
        refreshed = sessions.session(4, **selection)
        httpx_mock.add_response(json=session_data(clock, grantId=5, capabilityId=11))
        other = sessions.session(5, **selection)
        httpx_mock.add_response(status_code=503)
        with pytest.raises(httpx.HTTPStatusError):
            sessions.revoke(refreshed.capabilityId)
        assert sessions.session(5, **selection) == other
        assert all(s.capabilityId != 10 for s in sessions._cache.values())
        httpx_mock.add_response(status_code=204)
        sessions.revoke(refreshed.capabilityId)
        httpx_mock.add_response(json=session_data(clock, capabilityId=12))
        assert sessions.session(4, **selection).capabilityId == 12
    requests = httpx_mock.get_requests()
    assert requests[0].url.path == "/v2/platform/branch-sessions"
    assert requests[0].headers["Authorization"] == "Bearer platform-secret"
    assert json.loads(requests[0].content) == {"grantId": 4, "scopes": ["sale:create"], "ttlSeconds": 900}
    assert requests[4].url.path == requests[5].url.path == "/v2/platform/branch-sessions/10/revoke"


async def test_async_refresh_denial_and_failed_revocation_recover(httpx_mock, clock):
    selection = dict(environment="SANDBOX", scopes={"sale:create"})
    httpx_mock.add_response(json=session_data(clock))
    async with AsyncPlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        first = await sessions.session(4, **selection)
        first.environment = "PRODUCTION"
        assert (await sessions.session(4, **selection)).environment == "SANDBOX"
        clock.current += timedelta(seconds=570)
        httpx_mock.add_response(status_code=403)
        with pytest.raises(httpx.HTTPStatusError):
            await sessions.session(4, **selection)
        assert not sessions._cache
        httpx_mock.add_response(json=session_data(clock, capabilityId=10))
        refreshed = await sessions.session(4, **selection)
        httpx_mock.add_response(json=session_data(clock, grantId=5, capabilityId=11))
        other = await sessions.session(5, **selection)
        httpx_mock.add_response(status_code=503)
        with pytest.raises(httpx.HTTPStatusError):
            await sessions.revoke(refreshed.capabilityId)
        assert await sessions.session(5, **selection) == other
        assert all(s.capabilityId != 10 for s in sessions._cache.values())
        httpx_mock.add_response(status_code=204)
        await sessions.revoke(refreshed.capabilityId)
        httpx_mock.add_response(json=session_data(clock, capabilityId=12))
        assert (await sessions.session(4, **selection)).capabilityId == 12
    requests = httpx_mock.get_requests()
    assert requests[0].url.path == "/v2/platform/branch-sessions"
    assert requests[0].headers["Authorization"] == "Bearer platform-secret"
    assert json.loads(requests[0].content) == {"grantId": 4, "scopes": ["sale:create"], "ttlSeconds": 900}
    assert requests[4].url.path == requests[5].url.path == "/v2/platform/branch-sessions/10/revoke"
