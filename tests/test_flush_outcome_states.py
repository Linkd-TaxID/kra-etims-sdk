"""Queue rows distinguish API success from a proven device receipt."""

import pytest

from kra_etims._base_client import _BaseKRAeTIMSClient
from kra_etims.exceptions import KRADuplicateInvoiceError, TIaaSAmbiguousStateError


RECEIPT = {
    "cuInvoiceNumber": "SIM01/1 NS",
    "sdcId": "SIM01",
    "receiptSignature": "sample-signature",
}


@pytest.mark.parametrize("state", [
    "PENDING_SYNC", "OUTCOME_UNKNOWN", "RECONCILIATION_REQUIRED", "FAILED",
    "NOT_SENT", "SYNCED", "success", None,
])
def test_only_explicit_signed_state_can_claim_a_receipt(state):
    body = {**RECEIPT, "status": state}
    row = _BaseKRAeTIMSClient._flush_outcome("source-1", "attempt-1", body)
    assert row["signed"] is False
    assert row["sale_status"] == state
    assert row["idempotency_key"] == "attempt-1"
    assert row["data"] is body


@pytest.mark.parametrize("field", list(RECEIPT))
@pytest.mark.parametrize("invalid", [None, "", "   ", 1])
def test_signed_state_requires_receipt_evidence(field, invalid):
    body = {"status": "SIGNED", **RECEIPT, field: invalid}
    row = _BaseKRAeTIMSClient._flush_outcome("source-1", "attempt-1", body)
    assert row["signed"] is False


def test_signed_state_with_receipt_fields_is_signed():
    row = _BaseKRAeTIMSClient._flush_outcome(
        "source-1", "attempt-1", {"status": "SIGNED", **RECEIPT}
    )
    assert row["status"] == "success"
    assert row["signed"] is True


@pytest.mark.parametrize("body", [{}, {"resultCd": "000", "data": {}}, None, []])
def test_legacy_or_missing_response_is_not_proof_of_signing(body):
    row = _BaseKRAeTIMSClient._flush_outcome("source-1", "attempt-1", body)
    assert row["status"] == "success"
    assert row["signed"] is False


def test_ambiguous_exception_preserves_original_exception_and_reference():
    error = TIaaSAmbiguousStateError(idempotency_key="attempt-1")
    row = _BaseKRAeTIMSClient._flush_outcome("source-1", "attempt-1", error)
    assert row["status"] == "error"
    assert row["ambiguous"] is True
    assert row["exception"] is error
    assert row["idempotency_key"] == "attempt-1"
