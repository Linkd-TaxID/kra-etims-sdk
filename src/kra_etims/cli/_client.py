"""
Client factory — the single place that builds KRAeTIMSClient from config + keyring.

Commands that need TIaaS call get_client().
Commands that are purely local (tax calculate, pin check, invoice validate)
never import this module — they work with zero credentials installed.

API key fallback chain:
  1. api_key argument  (--api-key flag on any command)
  2. TAXID_API_KEY     environment variable (Railway, Docker, CI/CD)
  3. OS keyring        (set via `etims auth login`)
  4. Abort with clear, actionable error message
"""

from __future__ import annotations

import os
from typing import Optional

import typer

from kra_etims import KRAeTIMSClient
from kra_etims.gavaconnect import GavaConnectClient
from .config import get_api_key, get_consumer_secret, keyring_available, load_config
from .output import err_console, err_panel


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """
    Walk the fallback chain and return the first API key found.
    Prints a clear error and exits with code 1 if nothing is configured.
    """
    if api_key:
        return api_key

    env_key = os.getenv("TAXID_API_KEY", "").strip()
    if env_key:
        return env_key

    stored = get_api_key()
    if stored:
        return stored

    if keyring_available():
        err_console.print(err_panel(
            "No API key configured.\n\n"
            "Run [bold cyan]etims auth login[/bold cyan] to store your key, "
            "or set [bold]TAXID_API_KEY[/bold] as an environment variable."
        ))
    else:
        err_console.print(err_panel(
            "No API key configured and no keyring backend is available "
            "(headless environment detected).\n\n"
            "Set the environment variable:\n\n"
            "  [bold]export TAXID_API_KEY=your-api-key[/bold]"
        ))
    raise typer.Exit(1)


def get_client(api_key: Optional[str] = None) -> KRAeTIMSClient:
    """
    Build and return a configured KRAeTIMSClient.

    client_id and client_secret are not used when an api_key is present —
    the base client skips OAuth2 entirely. Passed as empty strings here
    because the constructor signature requires them positionally.
    """
    cfg = load_config()
    key = resolve_api_key(api_key)

    # Respect TAXID_API_URL env var (handled inside _BaseKRAeTIMSClient),
    # falling back to the stored config value, then the SDK default.
    base_url: Optional[str] = cfg.get("base_url") or os.getenv("TAXID_API_URL") or None

    return KRAeTIMSClient(
        client_id="",
        client_secret="",
        api_key=key,
        base_url=base_url,
    )


def resolve_gavaconnect_creds(
    consumer_key: Optional[str] = None,
    consumer_secret: Optional[str] = None,
) -> tuple[str, str] | None:
    """
    Walk the GavaConnect credential fallback chain.
    Returns (consumer_key, consumer_secret) if both are found, else None.

    Fallback order:
      1. CLI flags --consumer-key / --consumer-secret
      2. GAVACONNECT_CONSUMER_KEY / GAVACONNECT_CONSUMER_SECRET env vars
      3. consumer_key from config.toml + consumer_secret from OS keyring
    """
    cfg = load_config()
    key = (
        consumer_key
        or os.getenv("GAVACONNECT_CONSUMER_KEY", "").strip()
        or cfg.get("consumer_key", "").strip()
    )
    secret = (
        consumer_secret
        or os.getenv("GAVACONNECT_CONSUMER_SECRET", "").strip()
        or get_consumer_secret()
        or ""
    )
    if key and secret:
        return key, secret
    return None


def get_gavaconnect_client(
    consumer_key: Optional[str] = None,
    consumer_secret: Optional[str] = None,
) -> GavaConnectClient:
    """
    Build and return a GavaConnectClient from stored credentials.
    Prints a clear error and exits with code 1 if credentials are missing.
    """
    creds = resolve_gavaconnect_creds(consumer_key, consumer_secret)
    if not creds:
        err_console.print(err_panel(
            "No GavaConnect credentials configured.\n\n"
            "Run [bold cyan]etims auth login --consumer-key KEY --consumer-secret SECRET[/bold cyan]\n"
            "or set:\n\n"
            "  [bold]export GAVACONNECT_CONSUMER_KEY=your-key[/bold]\n"
            "  [bold]export GAVACONNECT_CONSUMER_SECRET=your-secret[/bold]\n\n"
            "Register free at [bold]https://developer.go.ke[/bold]",
            title="GavaConnect not configured",
        ))
        raise typer.Exit(1)

    cfg = load_config()
    sandbox = (
        cfg.get("gavaconnect_sandbox", "") in ("true", "1", True)
        or os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
    )
    return GavaConnectClient(creds[0], creds[1], sandbox=sandbox)
