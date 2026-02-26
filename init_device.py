""" # noqa: D400
init_device.py
--------------
Triggers the middleware's eTIMS device wake-up / initialization handshake.

The /v2/etims/init-handshake endpoint instructs the middleware to:
  1. Call the KRA Sandbox API to retrieve the cmcKey for the configured tenant.
  2. Encrypt the key with AES-256 via the JPA AesEncryptor converter.
  3. Persist the encrypted key in the TenantDevice record.

Usage:
    python init_device.py

Optional environment variables:
    TAXID_CLIENT_ID     – OAuth2 client ID  (default: uses env or falls back to value below)
    TAXID_CLIENT_SECRET – OAuth2 client secret
    TAXID_API_URL       – Base URL of the middleware (default: https://taxid-production.up.railway.app)
"""

import os
import time
import json
from kra_etims.client import KRAeTIMSClient


def run_init_device() -> None:
    print("=" * 60)
    print(" KRA eTIMS SDK — Device Initialization Handshake")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # Configuration — reads from environment variables when available.    #
    # ------------------------------------------------------------------ #
    client_id     = os.getenv("TAXID_CLIENT_ID",     "YOUR_CLIENT_ID")
    client_secret = os.getenv("TAXID_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
    base_url      = os.getenv("TAXID_API_URL",       "https://taxid-production.up.railway.app")

    # 1. Instantiate the SDK client
    print(f"\n[INFO] Connecting to middleware at: {base_url}")
    client = KRAeTIMSClient(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
    )

    # ----------------------------------------------------------------------- #
    # TEMPORARY: Bypass OAuth2 until /oauth/token is implemented in middleware #
    # The middleware has no auth server yet. This mirrors the pattern used in   #
    # every unit test (e.g. test_resilience.py, test_idempotency.py, etc.).    #
    # REMOVE these two lines once API Key auth is wired up (see plan below).   #
    # ----------------------------------------------------------------------- #
    client._access_token = "DUMMY_INIT_TOKEN"   # noqa: SLF001
    client._token_expiry = time.time() + 3600   # noqa: SLF001
    print("[INFO] Auth bypass active (middleware has no /oauth/token yet).")


    print("[INFO] Calling initialize_device_handshake() → GET /v2/etims/init-handshake …\n")
    try:
        response = client.initialize_device_handshake()

        print("[SUCCESS] Handshake completed. Middleware response:")
        print(json.dumps(response, indent=2, default=str))

        # ---------------------------------------------------------------- #
        # Friendly confirmation based on common middleware response shapes. #
        # ---------------------------------------------------------------- #
        if isinstance(response, dict):
            cmc_key = (
                response.get("cmcKey")
                or response.get("data", {}).get("cmcKey") if isinstance(response.get("data"), dict) else None
            )
            status = response.get("status") or response.get("resultCd") or response.get("resultMsg")

            if cmc_key:
                print(f"\n[VERIFIED] cmcKey retrieved and saved: {cmc_key[:8]}…")
            elif status:
                print(f"\n[INFO] Middleware status: {status}")
            else:
                print("\n[INFO] Response parsed — inspect the JSON above for handshake details.")

    except Exception as exc:
        print(f"[ERROR] Handshake failed: {exc}")
        raise

    print("\n" + "=" * 60)
    print(" Initialization handshake complete.")
    print("=" * 60)


if __name__ == "__main__":
    run_init_device()
