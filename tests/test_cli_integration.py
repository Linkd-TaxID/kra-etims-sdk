"""
CLI integration tests — full pipeline: CLI args → get_client() → KRAeTIMSClient → httpx.

Unlike the CLI unit tests (which mock get_client), these tests let the real
KRAeTIMSClient be constructed from the env var and intercept its httpx calls
with pytest-httpx's httpx_mock fixture.

This layer catches bugs that unit tests cannot:
  - resolve_api_key() → KRAeTIMSClient() constructor wiring
  - X-API-Key header actually sent in HTTP requests
  - Invoice JSON serialized correctly on the wire (field names, Decimal encoding)
  - HTTP error codes (401/403/500) mapped to non-zero CLI exit codes
  - URL construction (base URL + path) correct end-to-end

Setup:
  - TAXID_API_URL is set to a fixed fake base so URL assertions are deterministic.
  - TAXID_API_KEY is set directly (bypasses keyring entirely).
  - httpx_mock intercepts ALL httpx calls in the process — no network traffic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from kra_etims.cli.main import app

runner = CliRunner()

_BASE = "https://integration.test"

_VALID_PIN = "A000123456B"
_API_KEY   = "test-integration-key-abc"


# ---------------------------------------------------------------------------
# Autouse fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_integration_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the client at our fake base URL and inject a predictable API key."""
    monkeypatch.setenv("TAXID_API_URL", _BASE)
    monkeypatch.setenv("TAXID_API_KEY", _API_KEY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kra_ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"resultCd": "000", "resultMsg": "It is succeeded", "data": data}


# ---------------------------------------------------------------------------
# sandbox ping
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_sandbox_ping_integration(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/etims/init-handshake",
        json=_kra_ok({"status": "ok"}),
    )
    result = runner.invoke(app, ["sandbox", "ping"])
    assert result.exit_code == 0, result.output
    assert "reachable" in result.output.lower() or "latency" in result.output.lower()


@pytest.mark.integration
def test_api_key_sent_as_x_api_key_header(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/etims/init-handshake",
        json=_kra_ok({}),
    )
    runner.invoke(app, ["sandbox", "ping"])
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == _API_KEY


@pytest.mark.integration
def test_sandbox_ping_json_flag(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/etims/init-handshake",
        json=_kra_ok({"status": "ok"}),
    )
    result = runner.invoke(app, ["sandbox", "ping", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "latency_ms" in payload


@pytest.mark.integration
def test_sandbox_ping_server_error_exits_nonzero(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/etims/init-handshake",
        status_code=500,
    )
    result = runner.invoke(app, ["sandbox", "ping"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# device init
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_device_init_integration(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v2/etims/init",
        json=_kra_ok({"resultMsg": "Success"}),
    )
    result = runner.invoke(app, [
        "device", "init", "--tin", _VALID_PIN, "--serial", "VSCU001",
    ])
    assert result.exit_code == 0, result.output
    assert _VALID_PIN in result.output


@pytest.mark.integration
def test_device_init_request_body(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v2/etims/init",
        json=_kra_ok({}),
    )
    runner.invoke(app, [
        "device", "init",
        "--tin", _VALID_PIN,
        "--bhf-id", "01",
        "--serial", "SN-XYZ",
    ])
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body["tin"] == _VALID_PIN
    assert body["bhfId"] == "01"
    assert body["dvcSrlNo"] == "SN-XYZ"


# ---------------------------------------------------------------------------
# invoice submit
# ---------------------------------------------------------------------------

_ITEM_JSON = {
    "itemCd": "ITEM001",
    "itemNm": "Test Item",
    "qty": "1",
    "uprc": "1000.00",
    "totAmt": "1000.00",
    "taxTyCd": "A",
    "taxblAmt": "1000.00",
    "taxAmt": "0.00",
}

_INVOICE_JSON = {
    "tin": _VALID_PIN,
    "bhfId": "00",
    "invcNo": "INV-INTG-001",
    "confirmDt": "20260426142211",
    "totItemCnt": 1,
    "totTaxblAmt": "1000.00",
    "totTaxAmt": "0.00",
    "totAmt": "1000.00",
    "itemList": [_ITEM_JSON],
}

_SIGNING_RESPONSE = _kra_ok({
    "rcptNo": "KRACU0100000001/001 NS",
    "confirmDt": "20260426142211",
    "intrlData": "INTR0001ABCD",
    "rcptSign": "V249-J39C-FJ48-HE2W",
})


@pytest.mark.integration
def test_invoice_submit_integration(httpx_mock, tmp_path) -> None:
    f = tmp_path / "invoice.json"
    f.write_text(json.dumps(_INVOICE_JSON))
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v2/etims/sale",
        json=_SIGNING_RESPONSE,
    )
    result = runner.invoke(app, ["invoice", "submit", str(f)])
    assert result.exit_code == 0, result.output
    assert "KRACU" in result.output or "Signed" in result.output


@pytest.mark.integration
def test_invoice_submit_request_body_fields(httpx_mock, tmp_path) -> None:
    f = tmp_path / "invoice.json"
    f.write_text(json.dumps(_INVOICE_JSON))
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v2/etims/sale", json=_SIGNING_RESPONSE
    )
    runner.invoke(app, ["invoice", "submit", str(f)])
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body["tin"] == _VALID_PIN
    assert body["invcNo"] == "INV-INTG-001"
    assert len(body["itemList"]) == 1
    assert body["itemList"][0]["taxTyCd"] == "A"


@pytest.mark.integration
def test_invoice_submit_idempotency_key_in_header(httpx_mock, tmp_path) -> None:
    f = tmp_path / "invoice.json"
    f.write_text(json.dumps(_INVOICE_JSON))
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v2/etims/sale", json=_SIGNING_RESPONSE
    )
    runner.invoke(app, [
        "invoice", "submit", str(f), "--idempotency-key", "idem-test-abc",
    ])
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-tiaas-idempotency-key") == "idem-test-abc"


@pytest.mark.integration
def test_invoice_submit_401_exits_nonzero(httpx_mock, tmp_path) -> None:
    f = tmp_path / "invoice.json"
    f.write_text(json.dumps(_INVOICE_JSON))
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v2/etims/sale", status_code=401
    )
    result = runner.invoke(app, ["invoice", "submit", str(f)])
    assert result.exit_code != 0


@pytest.mark.integration
def test_invoice_submit_from_stdin(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v2/etims/sale", json=_SIGNING_RESPONSE
    )
    result = runner.invoke(app, ["invoice", "submit", "-"], input=json.dumps(_INVOICE_JSON))
    assert result.exit_code == 0, result.output
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# report x
# ---------------------------------------------------------------------------

_X_REPORT_RESPONSE = _kra_ok({
    "reportDate": "2026-04-26",
    "tin": _VALID_PIN,
    "branchId": "00",
    "generatedAt": "2026-04-26T14:00:00Z",
    "summary": {
        "totalCount": 5,
        "totalAmount": 50000.0,
        "totalTaxAmount": 6896.55,
        "taxBands": {
            "B": {"taxableAmount": 43103.45, "taxAmount": 6896.55, "totalAmount": 50000.0}
        },
    },
})


@pytest.mark.integration
def test_report_x_integration(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/reports/daily-x?date=2026-04-26",
        json=_X_REPORT_RESPONSE,
    )
    result = runner.invoke(app, ["report", "x", "--date", "2026-04-26"])
    assert result.exit_code == 0, result.output
    assert _VALID_PIN in result.output
    assert "50,000" in result.output or "50000" in result.output


@pytest.mark.integration
def test_report_x_json_integration(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/reports/daily-x?date=2026-04-26",
        json=_X_REPORT_RESPONSE,
    )
    result = runner.invoke(app, ["report", "x", "--date", "2026-04-26", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report_date"] == "2026-04-26"
    assert "raw" not in payload


# ---------------------------------------------------------------------------
# Offline commands work with no TAXID_API_URL / TAXID_API_KEY set
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_offline_tax_bands_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)
    monkeypatch.delenv("TAXID_API_URL", raising=False)
    result = runner.invoke(app, ["tax", "bands", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 5


@pytest.mark.integration
def test_offline_invoice_validate_needs_no_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)
    monkeypatch.delenv("TAXID_API_URL", raising=False)
    f = tmp_path / "invoice.json"
    f.write_text(json.dumps(_INVOICE_JSON))
    result = runner.invoke(app, ["invoice", "validate", str(f), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["valid"] is True


@pytest.mark.integration
def test_offline_pin_check_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXID_API_KEY", raising=False)
    monkeypatch.delenv("TAXID_API_URL", raising=False)
    result = runner.invoke(app, ["pin", "check", _VALID_PIN, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["valid"] is True


# ---------------------------------------------------------------------------
# TAXID_API_KEY env var flows all the way to X-API-Key header
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_env_var_api_key_sent_in_header(httpx_mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXID_API_KEY", "env-key-xyz-999")
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/v2/etims/init-handshake",
        json=_kra_ok({}),
    )
    runner.invoke(app, ["sandbox", "ping"])
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "env-key-xyz-999"
