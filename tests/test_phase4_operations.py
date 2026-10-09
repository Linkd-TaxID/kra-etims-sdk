import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError
from kra_etims.client import KRAeTIMSClient
from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.platform_sessions import PlatformSessions, AsyncPlatformSessions
from kra_etims.remittance import RemittanceCase, RemittanceExport, RemittanceTransition

FIXTURE = Path(__file__).parent / "fixtures" / "phase4-remittance-case.json"


def case_data():
    return json.loads(FIXTURE.read_text(), parse_float=Decimal)


def test_exact_java_http_case_and_no_false_success():
    result = RemittanceCase.model_validate(case_data())
    assert result.state == "CERTIFICATE_RECORDED_UNVERIFIED"
    assert result.automaticKraRemittance is False
    assert result.evidenceVerification == "SUPPLIED_UNVERIFIED"
    assert result.reconciledSuppliedEvidence == Decimal("5.00")
    assert len(result.audit) == 6
    data = case_data()
    data["automaticKraRemittance"] = True
    with pytest.raises(ValidationError):
        RemittanceCase.model_validate(data)


@pytest.mark.parametrize("value", [5.0, True, "5.001", "NaN", "-5"])
def test_export_rejects_invalid_money(value):
    data = case_data()["export"]
    data["amount"] = value
    with pytest.raises(ValidationError):
        RemittanceExport.model_validate(data)


def test_transition_fields_are_action_specific():
    with pytest.raises(ValidationError):
        RemittanceTransition(operationId="p", action="RECORD_EVIDENCE", evidence="e", amount="5")
    with pytest.raises(ValidationError):
        RemittanceTransition(operationId="p", action="APPROVE", evidence="e", amount="5")
    with pytest.raises(ValidationError):
        RemittanceTransition(operationId="p", action="RECORD_CERTIFICATE", evidence="e", reference="cert", attachmentReference="vault:doc")


def test_sync_remittance_wire_and_approval_route():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"content-type": "application/json"})
    with KRAeTIMSClient(api_key="branch", base_url="https://taxid.test") as client:
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        result = client.withholding.prepare_remittance("receipt:1", RemittanceExport.model_validate(case_data()["export"]))
        client.withholding.get_remittance("receipt:1")
        client.withholding.remittance_action("receipt:1", RemittanceTransition(operationId="review", action="REVIEW", evidence="reviewed"))
        client.withholding.remittance_action("receipt:1", RemittanceTransition(operationId="approve", action="APPROVE", evidence="approved"))
        assert result.export.amount == Decimal("5")
    assert calls[0][2]["amount"] == "5.00"
    assert calls[2][1].endswith("/actions")
    assert calls[3][1].endswith("/approval")


def test_async_remittance_wire_parity():
    async def run():
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"content-type": "application/json"})
        async with AsyncKRAeTIMSClient(api_key="branch", base_url="https://taxid.test") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            result = await client.withholding.prepare_remittance("receipt", RemittanceExport.model_validate(case_data()["export"]))
            await client.withholding.get_remittance("receipt")
            await client.withholding.remittance_action("receipt", RemittanceTransition(operationId="approve", action="APPROVE", evidence="approved"))
            await client.withholding.remittance_action("receipt", RemittanceTransition(operationId="review", action="REVIEW", evidence="reviewed"))
            assert result.automaticKraRemittance is False
        assert calls[2].endswith("/approval") and calls[3].endswith("/actions")
    asyncio.run(run())


def session_handler(calls):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer platform-secret"
        calls.append(request)
        if request.url.path.endswith("/revoke"):
            return httpx.Response(204)
        body = json.loads(request.content)
        return httpx.Response(200, json={"accessToken": f"capability-{body['grantId']}-{len(calls)}", "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), "capabilityId": len(calls), "grantId": body["grantId"], "environment": "PRODUCTION" if body["grantId"] == 3 else "SANDBOX", "scopes": body["scopes"]})
    return handler


def test_sync_scope_cache_concurrency_refresh_and_revoke():
    calls = []
    with PlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        sessions._http.close()
        sessions._http = httpx.Client(base_url="https://taxid.test", headers={"Authorization": "Bearer platform-secret"}, transport=httpx.MockTransport(session_handler(calls)))
        selection = dict(environment="SANDBOX", scopes={"sale:create"})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: sessions.session(1, **selection), range(8)))
        assert len(calls) == 1
        assert len({r.accessToken.get_secret_value() for r in results}) == 1
        creator2 = sessions.session(2, **selection)
        assert creator2.accessToken != results[0].accessToken
        sessions.session(1, environment="SANDBOX", scopes={"sale:read"})
        sessions.session(3, environment="PRODUCTION", scopes={"sale:create"})
        assert len(calls) == 4
        result = sessions.session(1, **selection)
        assert "capability-" not in repr(result)
        assert "platform-secret" not in repr(sessions)
        with sessions.branch_client(1, **selection) as fiscal:
            assert fiscal._api_key == result.accessToken.get_secret_value()
            assert fiscal._api_key != "platform-secret"
        cached_key = (1, "SANDBOX", frozenset({"sale:create"}))
        sessions._cache[cached_key].expiresAt = datetime.now(timezone.utc)
        refreshed = sessions.session(1, **selection)
        assert refreshed.accessToken != result.accessToken
        sessions.revoke(refreshed.capabilityId)
        assert cached_key not in sessions._cache
        sessions.session(1, **selection)
        assert len(calls) == 7


def test_wrong_environment_and_server_denial_fail_closed():
    calls = []
    with PlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
        sessions._http.close()
        sessions._http = httpx.Client(base_url="https://taxid.test", headers={"Authorization": "Bearer platform-secret"}, transport=httpx.MockTransport(session_handler(calls)))
        with pytest.raises(ValueError, match="does not match"):
            sessions.session(1, environment="PRODUCTION", scopes={"sale:create"})
        assert not sessions._cache
        sessions._http.close()
        sessions._http = httpx.Client(base_url="https://taxid.test", transport=httpx.MockTransport(lambda _: httpx.Response(403)))
        with pytest.raises(httpx.HTTPStatusError):
            sessions.session(1, environment="SANDBOX", scopes={"sale:create"})
        assert not sessions._cache


def test_async_sessions_concurrency_revocation_and_fiscal_secret_separation():
    async def run():
        calls = []
        async with AsyncPlatformSessions("platform-secret", base_url="https://taxid.test") as sessions:
            await sessions._http.aclose()
            sessions._http = httpx.AsyncClient(base_url="https://taxid.test", headers={"Authorization": "Bearer platform-secret"}, transport=httpx.MockTransport(session_handler(calls)))
            selection = dict(environment="SANDBOX", scopes={"sale:create"})
            results = await asyncio.gather(*(sessions.session(1, **selection) for _ in range(8)))
            assert len(calls) == 1
            await sessions.session(2, **selection)
            await sessions.session(3, environment="PRODUCTION", scopes={"sale:create"})
            async with await sessions.branch_client(1, **selection) as fiscal:
                assert fiscal._api_key == results[0].accessToken.get_secret_value()
            await sessions.revoke(results[0].capabilityId)
            await sessions.session(1, **selection)
            assert len(calls) == 5
    asyncio.run(run())


def test_gateway_status_preserves_explicit_gate_and_legacy_unknown():
    from kra_etims.gateway import SupplierGatewayStatus
    status = SupplierGatewayStatus.from_api({"requestId": 12, "status": "CONFIRMED", "failureReason": "CONSENT_RECORDED_SUBMISSION_GATED", "fiscalSubmissionAvailable": False, "purchaseId": None})
    assert status.fiscal_submission_available is False
    assert status.failure_reason == "CONSENT_RECORDED_SUBMISSION_GATED"
    assert status.purchase_id is None
    legacy = SupplierGatewayStatus.from_api({"requestId": 12, "status": "CONFIRMED"})
    assert legacy.fiscal_submission_available is None
