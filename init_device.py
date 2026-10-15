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

Required environment variables (one of):
    TAXID_API_KEY       – API key (preferred; set this for all deployments)

Optional environment variables:
    TAXID_CLIENT_ID     – OAuth2 client ID  (fallback when no API key is set)
    TAXID_CLIENT_SECRET – OAuth2 client secret
    TAXID_API_URL       – Base URL of the middleware (default: https://api.taxid.co.ke)
"""

import os
from kra_etims.client import KRAeTIMSClient


def run_init_device() -> None:
    print("=" * 60)
    print(" KRA eTIMS SDK — Device Initialization Handshake")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # Authentication — API key preferred, OAuth2 as fallback.             #
    # ------------------------------------------------------------------ #
    api_key       = os.getenv("TAXID_API_KEY", "").strip() or None
    client_id     = os.getenv("TAXID_CLIENT_ID",     "").strip() or None
    client_secret = os.getenv("TAXID_CLIENT_SECRET", "").strip() or None
    base_url      = os.getenv("TAXID_API_URL", "https://api.taxid.co.ke").strip()

    if not api_key and not (client_id and client_secret):
        print(
            "\n[ERROR] No authentication credentials found.\n"
            "  Set TAXID_API_KEY for API key authentication (preferred):\n"
            "      export TAXID_API_KEY=your-key\n"
            "  Or set both OAuth2 credentials:\n"
            "      export TAXID_CLIENT_ID=your-id\n"
            "      export TAXID_CLIENT_SECRET=your-secret\n"
        )
        raise SystemExit(1)

    print("[INFO] Using configured authentication credentials")
    print("[INFO] Calling initialize_device_handshake()")
    try:
        with KRAeTIMSClient(
            client_id=client_id or "",
            client_secret=client_secret or "",
            api_key=api_key,
            base_url=base_url,
        ) as client:
            client.initialize_device_handshake()
        print("[SUCCESS] Handshake request completed.")
    except Exception as exc:
        print(f"[ERROR] Handshake failed ({type(exc).__name__}).")
        raise SystemExit(1) from None

    print("\n" + "=" * 60)
    print(" Initialization handshake complete.")
    print("=" * 60)


if __name__ == "__main__":
    run_init_device()
