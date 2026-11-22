"""Follow-up regressions for the SDK contract-drift campaign (P7-P13, D4-D7).

Every HTTP interaction is a synthetic httpx.MockTransport; no live endpoint is called.
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from kra_etims import AsyncKRAeTIMSClient, KRAeTIMSClient

ROOT = Path(__file__).resolve().parents[1]

RECEIPT = {"purchaseId": 7, "status": "SIGNED", "cuInvoiceNumber": "SIM01/7 NS",
           "sdcId": "SIM01", "receiptSignature": "synthetic-signature"}
STOCK = {"operationKey": "op-1", "movementId": 1, "sarNo": 1, "status": "SYNCED",
         "payloadSha256": "a" * 64, "totAmt": 10, "totTaxAmt": 0}
_QUEUED = {
    "tin": "A000123456B", "bhfId": "00", "invcNo": "INV-Q1", "confirmDt": "20260426142211",
    "totItemCnt": 0, "totTaxblAmt": "0.00", "totTaxAmt": "0.00", "totAmt": "0.00", "itemList": [],
}


async def invoke(client, method, *args, **kwargs):
    result = getattr(client, method)(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def make_client(async_mode, handler):
    raw_type, sdk_type = (httpx.AsyncClient, AsyncKRAeTIMSClient) if async_mode else (httpx.Client, KRAeTIMSClient)
    raw = raw_type(transport=httpx.MockTransport(handler))
    return sdk_type(api_key="synthetic", base_url="https://synthetic.invalid", http_client=raw), raw


def respond(status, body, headers=None):
    def handler(request):
        return httpx.Response(status, json=body, headers=headers or {})
    return handler


async def close(raw, async_mode):
    await invoke(raw, "aclose" if async_mode else "close")


# ---------------------------------------------------------------------------
# D5 — httpx floor
# ---------------------------------------------------------------------------

def test_httpx_floor_is_0_28():
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 3.10
        import tomli as tomllib
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    httpx_dep = next(d for d in deps if re.match(r"httpx\b", d))
    assert re.fullmatch(r"httpx>=0\.28(\.\d+)?(,<\d+)?", httpx_dep.replace(" ", "")), httpx_dep
