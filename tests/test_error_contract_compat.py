"""Compatibility tests for TaxID's two supported v2 error representations."""

import httpx

from kra_etims._base_client import _BaseKRAeTIMSClient


def _response(status: int, body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.taxid.co.ke/v2/etims/sale")
    return httpx.Response(status, json=body, request=request)


def test_flat_error_fields_are_parsed() -> None:
    body, message, code = _BaseKRAeTIMSClient._error_fields(
        _response(422, {
            "status": "ERROR",
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "Use a new key.",
        })
    )

    assert body["status"] == "ERROR"
    assert message == "Use a new key."
    assert code == "IDEMPOTENCY_KEY_REUSED"


def test_problem_detail_fields_and_extensions_are_parsed() -> None:
    body, message, code = _BaseKRAeTIMSClient._error_fields(
        _response(503, {
            "type": "https://api.taxid.co.ke/problems/oscu-unavailable",
            "title": "OSCU unavailable",
            "status": 503,
            "detail": "The control unit did not respond.",
            "code": "OSCU_UNAVAILABLE",
            "oscu_code": "894",
        })
    )

    assert body["oscu_code"] == "894"
    assert message == "The control unit did not respond."
    assert code == "OSCU_UNAVAILABLE"


def test_non_json_error_has_bounded_fallback() -> None:
    request = httpx.Request("GET", "https://api.taxid.co.ke/v2/etims/items")
    response = httpx.Response(502, text="proxy failed", request=request)

    body, message, code = _BaseKRAeTIMSClient._error_fields(response)

    assert body == {}
    assert message == "proxy failed"
    assert code is None
