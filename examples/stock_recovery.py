"""Inspect native synthetic stock and replay its exact completed master request.
Requires a private manifest from the isolated stock-recovery runner; never accepts remote origins.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from decimal import Decimal
from kra_etims import KRAeTIMSClient
from kra_etims.models import StockMasterRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-manifest", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.private_manifest.read_text())
    parsed = urlparse(state["origin"])
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or state.get("isolatedSimulator") is not True:
        raise ValueError("Example requires the isolated synthetic loopback runner")
    for mode, branch in state["branches"].items():
        with KRAeTIMSClient(api_key=branch["apiKey"], base_url=state["origin"]) as client:
            original = client.inspect_stock_operation(branch["originalIoKey"])
            assert original.status == "SYNCED" and not original.kraAcknowledged
            request = StockMasterRequest(originalIoKey=original.operationKey,
                itemCd=branch["itemCd"], rsdQty=Decimal("10.25"))
            result = client.submit_stock_master(request, idempotency_key=branch["masterKey"])
            assert result.status == "SYNCED" and not result.kraAcknowledged
            assert client.inspect_stock_master(branch["masterKey"]).payloadSha256 == result.payloadSha256
            assert client.device_health()["controlUnitType"] == mode
    print("PASS: both mode identities retained; no replacement or fiscal replay")


if __name__ == "__main__":
    main()
