"""
etims — KRA eTIMS CLI

Entry point: etims [group] [command] [options]

Groups registered here (each in its own section of this file):
  auth      — login / logout / status
  device    — init / status
  invoice   — submit / validate / list
  report    — x / z
  queue     — status / flush
  pin       — check / validate
  tax       — calculate / bands
  tcc       — check  (GavaConnect direct — no TIaaS subscription needed)
  sandbox   — ping
"""

from __future__ import annotations

import os
import sys
from datetime import date as _date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .config import (
    config_path,
    delete_api_key,
    delete_consumer_secret,
    get_api_key,
    get_consumer_secret,
    keyring_available,
    load_config,
    save_config,
    set_api_key,
    set_consumer_secret,
)
from .output import (
    band_table,
    console,
    err_console,
    err_panel,
    kv_table,
    print_json,
    report_band_table,
    success_panel,
    warn_panel,
)

# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="etims",
    help="KRA eTIMS CLI — manage invoices, devices, and reports via TIaaS.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=True,
)

# ---------------------------------------------------------------------------
# Sub-apps (command groups)
# ---------------------------------------------------------------------------

auth_app    = typer.Typer(help="Manage TIaaS credentials.", no_args_is_help=True)
device_app  = typer.Typer(help="Device initialisation and status.", no_args_is_help=True)
invoice_app = typer.Typer(help="Invoice submission and validation.", no_args_is_help=True)
report_app  = typer.Typer(help="X and Z fiscal reports.", no_args_is_help=True)
queue_app   = typer.Typer(help="Offline invoice queue.", no_args_is_help=True)
pin_app     = typer.Typer(help="KRA PIN utilities.", no_args_is_help=True)
tax_app     = typer.Typer(help="Tax calculation utilities (offline — no credentials needed).", no_args_is_help=True)
tcc_app     = typer.Typer(help="Tax Compliance Certificate checks via GavaConnect (free — no TIaaS subscription needed).", no_args_is_help=True)
sandbox_app = typer.Typer(help="Developer / sandbox tools.", no_args_is_help=True)

app.add_typer(auth_app,    name="auth")
app.add_typer(device_app,  name="device")
app.add_typer(invoice_app, name="invoice")
app.add_typer(report_app,  name="report")
app.add_typer(queue_app,   name="queue")
app.add_typer(pin_app,     name="pin")
app.add_typer(tax_app,     name="tax")
app.add_typer(tcc_app,     name="tcc")
app.add_typer(sandbox_app, name="sandbox")


# ===========================================================================
# auth
# ===========================================================================

@auth_app.command("login")
def auth_login(
    api_key: Annotated[
        Optional[str],
        typer.Option("--api-key", "-k", help="TIaaS API key (prompted if omitted when no GavaConnect flags given)."),
    ] = None,
    base_url: Annotated[
        Optional[str],
        typer.Option(help="Override TIaaS base URL."),
    ] = None,
    tin: Annotated[
        Optional[str],
        typer.Option(help="Default KRA TIN to store in config."),
    ] = None,
    bhf_id: Annotated[
        Optional[str],
        typer.Option(help="Default branch ID (default: 00)."),
    ] = None,
    consumer_key: Annotated[
        Optional[str],
        typer.Option("--consumer-key", help="GavaConnect consumer key (from developer.go.ke)."),
    ] = None,
    consumer_secret: Annotated[
        Optional[str],
        typer.Option("--consumer-secret", help="GavaConnect consumer secret."),
    ] = None,
    sandbox: Annotated[
        bool,
        typer.Option("--sandbox", help="Use GavaConnect sandbox endpoints (sbx.kra.go.ke)."),
    ] = False,
) -> None:
    """Store credentials for TIaaS and/or GavaConnect direct access.

    TIaaS mode (invoice submission, reports, device init):
      etims auth login --api-key YOUR_KEY

    GavaConnect mode (PIN validation, TCC checks — free, no TIaaS needed):
      etims auth login --consumer-key KEY --consumer-secret SECRET

    Both can be stored simultaneously.
    """
    if not keyring_available():
        err_console.print(err_panel(
            "No keyring backend available (headless environment).\n\n"
            "Credentials have [bold]not[/bold] been stored.\n"
            "Set them as environment variables instead:\n\n"
            "  TIaaS:       [bold]export TAXID_API_KEY=your-api-key[/bold]\n"
            "  GavaConnect: [bold]export GAVACONNECT_CONSUMER_KEY=key[/bold]\n"
            "               [bold]export GAVACONNECT_CONSUMER_SECRET=secret[/bold]"
        ))
        raise typer.Exit(1)

    stored: list[str] = []

    # --- TIaaS API key ---
    if consumer_key is None and consumer_secret is None:
        # Only prompt for TIaaS key when not in GavaConnect-only mode.
        if not api_key:
            api_key = typer.prompt("TIaaS API key", hide_input=True)

    if api_key is not None:
        if not api_key.strip():
            err_console.print(err_panel("TIaaS API key cannot be blank."))
            raise typer.Exit(1)
        if not set_api_key(api_key.strip()):
            err_console.print(err_panel("Keyring write failed for TIaaS API key."))
            raise typer.Exit(1)
        stored.append("TIaaS API key → OS keyring")

    # --- GavaConnect credentials ---
    if consumer_key or consumer_secret:
        if not consumer_key or not consumer_secret:
            err_console.print(err_panel(
                "[bold]--consumer-key[/bold] and [bold]--consumer-secret[/bold] "
                "must be supplied together."
            ))
            raise typer.Exit(1)
        if not consumer_key.strip() or not consumer_secret.strip():
            err_console.print(err_panel("GavaConnect consumer key and secret cannot be blank."))
            raise typer.Exit(1)
        if not set_consumer_secret(consumer_secret.strip()):
            err_console.print(err_panel("Keyring write failed for GavaConnect consumer secret."))
            raise typer.Exit(1)
        stored.append("GavaConnect consumer secret → OS keyring")

    if not stored:
        err_console.print(err_panel("Nothing to store. Pass --api-key or --consumer-key/--consumer-secret."))
        raise typer.Exit(1)

    # --- Non-sensitive config ---
    updates: dict = {}
    if base_url:
        updates["base_url"] = base_url.rstrip("/")
    if tin:
        updates["tin"] = tin
    if bhf_id:
        updates["bhf_id"] = bhf_id
    if consumer_key:
        updates["consumer_key"] = consumer_key.strip()
    if sandbox:
        updates["gavaconnect_sandbox"] = "true"
    if updates:
        save_config(updates)

    lines = "\n".join(f"  [green]✓[/green] {s}" for s in stored)
    console.print(success_panel(
        f"{lines}\n"
        f"  Config file: [dim]{config_path()}[/dim]"
    ))


@auth_app.command("logout")
def auth_logout(
    all_creds: Annotated[
        bool,
        typer.Option("--all", help="Remove both TIaaS and GavaConnect credentials."),
    ] = False,
    gavaconnect: Annotated[
        bool,
        typer.Option("--gavaconnect", help="Remove only GavaConnect credentials."),
    ] = False,
) -> None:
    """Remove stored credentials from the OS keyring."""
    removed: list[str] = []
    warned: list[str]  = []

    remove_tiaas      = not gavaconnect
    remove_gavaconnect = all_creds or gavaconnect

    if remove_tiaas:
        if delete_api_key():
            removed.append("TIaaS API key")
        else:
            warned.append("TIaaS API key (not found)")

    if remove_gavaconnect:
        if delete_consumer_secret():
            removed.append("GavaConnect consumer secret")
            save_config({"consumer_key": "", "gavaconnect_sandbox": ""})
        else:
            warned.append("GavaConnect consumer secret (not found)")

    if removed:
        lines = "\n".join(f"  [green]✓[/green] Removed: {r}" for r in removed)
        console.print(success_panel(lines))
    if warned:
        lines = "\n".join(f"  {w}" for w in warned)
        err_console.print(warn_panel(
            f"{lines}\n\n"
            "If you use environment variables, unset them manually."
        ))


@auth_app.command("status")
def auth_status() -> None:
    """Show current credentials and configuration for all transports."""
    cfg = load_config()

    # --- TIaaS ---
    env_key  = os.getenv("TAXID_API_KEY", "")
    ring_key = get_api_key()
    if env_key:
        tiaas_status, tiaas_source = "configured", "TAXID_API_KEY env var"
    elif ring_key:
        tiaas_status, tiaas_source = "configured", "OS keyring"
    else:
        tiaas_status, tiaas_source = "not configured", "not found"

    # --- GavaConnect ---
    gc_key_cfg = cfg.get("consumer_key", "")
    gc_env_key = os.getenv("GAVACONNECT_CONSUMER_KEY", "")
    gc_secret  = os.getenv("GAVACONNECT_CONSUMER_SECRET", "") or get_consumer_secret() or ""
    gc_key     = gc_env_key or gc_key_cfg

    if gc_key and gc_secret:
        gc_status = "configured"
    elif gc_key or gc_secret:
        gc_status = "partial (missing key or secret)"
    else:
        gc_status = "not configured"

    gc_mode = "sandbox" if (
        cfg.get("gavaconnect_sandbox") in ("true", "1", True)
        or os.getenv("GAVACONNECT_SANDBOX", "").lower() in ("true", "1")
    ) else "production"

    tiaas_rows = [
        ("API key",   f"{tiaas_status} ({tiaas_source})"),
        ("Base URL",  cfg.get("base_url") or os.getenv("TAXID_API_URL") or "(SDK default)"),
        ("TIN",       cfg.get("tin") or "(not set)"),
        ("Branch ID", cfg.get("bhf_id") or "(not set)"),
    ]
    gc_rows = [
        ("Consumer key",    f"{gc_key[:8]}…" if gc_key else "(not set)"),
        ("Consumer secret", "configured (keyring)" if gc_secret else "(not set)"),
        ("Mode",            gc_mode),
    ]
    shared_rows = [
        ("Config",  str(config_path())),
        ("Keyring", "available" if keyring_available() else "not available (use env var)"),
    ]

    console.print(kv_table(tiaas_rows, title="TIaaS"))
    console.print(kv_table(gc_rows,    title="GavaConnect (direct)"))
    console.print(kv_table(shared_rows))

    if tiaas_status == "not configured" and gc_status == "not configured":
        err_console.print(warn_panel(
            "No credentials configured.\n\n"
            "TIaaS (full feature set):\n"
            "  [bold cyan]etims auth login --api-key YOUR_KEY[/bold cyan]\n\n"
            "GavaConnect (PIN + TCC — free):\n"
            "  [bold cyan]etims auth login --consumer-key KEY --consumer-secret SECRET[/bold cyan]\n\n"
            "Register GavaConnect at [bold]https://developer.go.ke[/bold]"
        ))
        raise typer.Exit(1)


# ===========================================================================
# device
# ===========================================================================

@device_app.command("init")
def device_init(
    tin: Annotated[str, typer.Option(help="KRA TIN (e.g. A000123456B).")],
    bhf_id: Annotated[str, typer.Option(help="Branch ID (e.g. 00).")] = "00",
    serial: Annotated[str, typer.Option(help="Device serial number.")] = "VSCU001",
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Initialise a device/branch on eTIMS (one-time per branch)."""
    from ._client import get_client
    from kra_etims import DeviceInit

    client = get_client(api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Initialising device on eTIMS...", total=None)
        result = client.initialize_device(DeviceInit(tin=tin, bhfId=bhf_id, dvcSrlNo=serial))

    if as_json:
        print_json(result)
        return

    console.print(success_panel(f"Device initialised for TIN [bold]{tin}[/bold] branch [bold]{bhf_id}[/bold]."))
    console.print(kv_table([
        ("TIN",    tin),
        ("Branch", bhf_id),
        ("Serial", serial),
        ("Result", str(result.get("resultMsg", "OK"))),
    ]))


@device_app.command("status")
def device_status(
    pin: Annotated[str, typer.Option(help="KRA PIN to check compliance for.")],
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Check device compliance status for a given PIN."""
    from ._client import get_client

    client = get_client(api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Checking compliance...", total=None)
        result = client.check_compliance(pin)

    if as_json:
        print_json(result)
        return

    console.print(kv_table([
        ("PIN",    pin),
        ("Status", str(result.get("status") or result.get("resultMsg", "OK"))),
    ], title="Device Compliance"))


# ===========================================================================
# invoice
# ===========================================================================

@invoice_app.command("submit")
def invoice_submit(
    file: Annotated[
        Optional[Path],
        typer.Argument(help="JSON file to submit, or - to read from stdin."),
    ] = None,
    tin: Annotated[Optional[str], typer.Option(help="KRA TIN (inline mode).")] = None,
    idempotency_key: Annotated[Optional[str], typer.Option("--idempotency-key", "-i", help="Idempotency key.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate only — do not submit.")] = False,
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Submit an invoice from a JSON file or stdin (use - for stdin)."""
    import json as _json
    from kra_etims import SaleInvoice

    if file is None:
        err_console.print(err_panel("Provide a JSON file path or use [bold]-[/bold] to read from stdin."))
        raise typer.Exit(1)

    if str(file) == "-":
        raw = sys.stdin.read()
    else:
        if not file.exists():
            err_console.print(err_panel(f"File not found: {file}"))
            raise typer.Exit(1)
        raw = file.read_text(encoding="utf-8")

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as e:
        err_console.print(err_panel(f"Invalid JSON: {e}"))
        raise typer.Exit(1)

    try:
        invoice = SaleInvoice(**data)
    except Exception as e:
        err_console.print(err_panel(f"Invoice validation failed:\n{e}"))
        raise typer.Exit(1)

    if dry_run:
        console.print(success_panel(
            f"Invoice [bold]{invoice.invcNo}[/bold] is valid — {len(invoice.itemList)} item(s), "
            f"total KES {invoice.totAmt:,.2f}. (dry-run — not submitted)"
        ))
        return

    from ._client import get_client
    client = get_client(api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Signing with KRA...", total=None)
        result = client.submit_sale(invoice, idempotency_key=idempotency_key)

    if as_json:
        print_json(result)
        return

    data_out = result.get("data", result)
    console.print(success_panel("Invoice signed"))
    console.print(kv_table([
        ("Control Number", str(data_out.get("rcptNo") or data_out.get("controlNumber") or "—")),
        ("Timestamp",      str(data_out.get("confirmDt") or data_out.get("timestamp") or "—")),
        ("Signature",      str(data_out.get("intrlData") or data_out.get("signature") or "—")[:60]),
        ("QR",             str(data_out.get("rcptSign") or data_out.get("qr") or "—")[:60]),
    ]))


@invoice_app.command("validate")
def invoice_validate(
    file: Annotated[Path, typer.Argument(help="JSON file to validate (no API call).")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit result as JSON.")] = False,
) -> None:
    """
    Validate an invoice file against KRA rules (offline — no credentials needed).
    Runs all Pydantic validators including math checks. Does not submit to TIaaS.
    """
    import json as _json
    from kra_etims import SaleInvoice

    if not file.exists():
        console.print(err_panel(f"File not found: {file}"))
        raise typer.Exit(1)

    try:
        data = _json.loads(file.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        err_console.print(err_panel(f"Invalid JSON: {e}"))
        raise typer.Exit(1)

    try:
        invoice = SaleInvoice(**data)
    except Exception as e:
        if as_json:
            print_json({"valid": False, "error": str(e)})
        else:
            err_console.print(err_panel(f"Validation failed:\n\n{e}"))
        raise typer.Exit(1)

    summary = {
        "valid": True,
        "invoice_no": invoice.invcNo,
        "items": len(invoice.itemList),
        "total_kes": str(invoice.totAmt),
        "total_vat_kes": str(invoice.totTaxAmt),
    }
    if as_json:
        print_json(summary)
        return

    console.print(success_panel(
        f"[bold]{file.name}[/bold] is valid\n"
        f"  Invoice No: {invoice.invcNo}\n"
        f"  Items:      {len(invoice.itemList)}\n"
        f"  Total:      KES {invoice.totAmt:,.2f}\n"
        f"  VAT:        KES {invoice.totTaxAmt:,.2f}"
    ))


# ===========================================================================
# report
# ===========================================================================

@report_app.command("x")
def report_x(
    date: Annotated[
        Optional[str],
        typer.Option(help="Report date YYYY-MM-DD (default: today)."),
    ] = None,
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Fetch the interim X-report (safe — does not reset VSCU counters)."""
    from ._client import get_client

    report_date = date or str(_date.today())
    client = get_client(api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Fetching X-report for {report_date}...", total=None)
        report = client.reports.get_x_report(report_date)

    if as_json:
        print_json(report.model_dump(exclude={"raw"}))
        return

    console.print(kv_table([
        ("Date",     report.report_date),
        ("TIN",      report.tin),
        ("Branch",   report.branch_id),
        ("Invoices", str(report.invoice_count)),
        ("Total",    f"KES {report.total_amount:,.2f}"),
        ("VAT",      f"KES {report.total_vat:,.2f}"),
    ], title=f"X-Report — {report_date}"))
    console.print(report_band_table(
        report.band_a, report.band_b, report.band_c, report.band_d, report.band_e
    ))


@report_app.command("z")
def report_z(
    date: Annotated[
        Optional[str],
        typer.Option(help="Report date YYYY-MM-DD (default: today)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Close the fiscal day and submit the Z-report (IRREVERSIBLE).
    Resets the VSCU day counter — call once per day after last transaction.
    """
    from ._client import get_client
    from kra_etims.exceptions import ZReportAlreadyIssuedError

    report_date = date or str(_date.today())

    if not yes:
        console.print(warn_panel(
            f"Z-Report for [bold]{report_date}[/bold] — "
            "this resets the VSCU day counter and is [bold red]irreversible[/bold red]."
        ))
        typer.confirm("Are you sure?", abort=True)

    client = get_client(api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Submitting Z-report for {report_date}...", total=None)
        try:
            report = client.reports.get_daily_z(report_date)
        except ZReportAlreadyIssuedError:
            console.print(warn_panel(
                f"Z-Report for [bold]{report_date}[/bold] was already submitted. "
                "The VSCU day counter has already been reset."
            ))
            raise typer.Exit(0)

    if as_json:
        print_json(report.model_dump(exclude={"raw"}))
        return

    console.print(success_panel(f"Z-Report submitted for {report_date}"))
    console.print(kv_table([
        ("Date",             report.report_date),
        ("TIN",              report.tin),
        ("Branch",           report.branch_id),
        ("Invoices",         str(report.invoice_count)),
        ("Total",            f"KES {report.total_amount:,.2f}"),
        ("VAT",              f"KES {report.total_vat:,.2f}"),
        ("Period No.",       str(report.period_number or "—")),
        ("VSCU Acknowledged", "Yes" if report.vscu_acknowledged else "No"),
    ], title=f"Z-Report — {report_date}"))
    console.print(report_band_table(
        report.band_a, report.band_b, report.band_c, report.band_d, report.band_e
    ))


# ===========================================================================
# queue
# ===========================================================================

@queue_app.command("status")
def queue_status(
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Show the count and state of queued offline invoices (offline — no credentials needed)."""
    from .queue import Queue

    q = Queue()
    counts = q.status()

    if as_json:
        print_json(counts)
        return

    console.print(kv_table([
        ("Pending",    str(counts["pending"])),
        ("Failed",     str(counts["failed"])),
        ("Completed",  str(counts["completed"])),
        ("Total",      str(counts["total"])),
        ("Queue file", str(counts["path"])),
    ], title="Offline Queue"))


@queue_app.command("flush")
def queue_flush(
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Submit all pending offline invoices to TIaaS."""
    from ._client import get_client
    from .queue import Queue

    q = Queue()
    pending = q.pending()

    if not pending:
        console.print(success_panel("Queue is empty — nothing to flush."))
        return

    client = get_client(api_key)
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Flushing {len(pending)} invoice(s)...", total=len(pending))
        for row in pending:
            result = q.flush_one(client, row)
            results.append(result)
            progress.advance(task)

    if as_json:
        print_json(results)
        return

    ok = sum(1 for r in results if r["status"] in ("success", "already_processed"))
    failed = len(results) - ok
    console.print(success_panel(
        f"Flushed {ok}/{len(results)} invoice(s)."
        + (f" [red]{failed} failed[/red] — re-run to retry." if failed else "")
    ))


# ===========================================================================
# pin
# ===========================================================================

@pin_app.command("check")
def pin_check(
    pin: Annotated[str, typer.Argument(help="KRA PIN to validate (e.g. A000123456B).")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Validate KRA PIN format locally (offline — no credentials needed).
    Checks the pattern A000000000B (letter + 9 digits + letter).
    """
    from kra_etims import KRA_TIN_PATTERN

    valid = bool(KRA_TIN_PATTERN.match(pin))

    if as_json:
        print_json({"pin": pin, "valid": valid})
        if not valid:
            raise typer.Exit(1)
        return

    if valid:
        console.print(f"[green]✓[/green] [bold]{pin}[/bold] — valid format")
    else:
        console.print(f"[red]✗[/red] [bold]{pin}[/bold] — not a valid KRA PIN")
        console.print("[dim]Expected format: A000000000B (1 letter + 9 digits + 1 letter)[/dim]")
        raise typer.Exit(1)


@pin_app.command("validate")
def pin_validate(
    pin: Annotated[str, typer.Argument(help="KRA PIN to validate (e.g. A000123456B).")],
    consumer_key: Annotated[Optional[str], typer.Option("--consumer-key", help="GavaConnect consumer key.")] = None,
    consumer_secret: Annotated[Optional[str], typer.Option("--consumer-secret", help="GavaConnect consumer secret.")] = None,
    api_key: Annotated[Optional[str], typer.Option(help="TIaaS API key (fallback if GavaConnect not configured).")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Validate a KRA PIN against the taxpayer registry (live — requires credentials).

    Uses GavaConnect direct (free) if consumer key/secret are configured.
    Falls back to TIaaS if only a TIaaS API key is available.

    Register for free GavaConnect access at https://developer.go.ke
    """
    from kra_etims import KRA_TIN_PATTERN

    if not KRA_TIN_PATTERN.match(pin):
        err_console.print(err_panel(
            f"[bold]{pin}[/bold] is not a valid KRA PIN format.\n"
            "Run [bold cyan]etims pin check {pin}[/bold cyan] for format details."
        ))
        raise typer.Exit(1)

    from ._client import get_gavaconnect_client, get_client, resolve_gavaconnect_creds
    from kra_etims.gavaconnect import GavaConnectPINNotFoundError

    gc_creds = resolve_gavaconnect_creds(consumer_key, consumer_secret)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        if gc_creds:
            progress.add_task(f"Validating {pin} via GavaConnect (direct)...", total=None)
            gc_client = get_gavaconnect_client(consumer_key, consumer_secret)
            try:
                result = gc_client.lookup_pin(pin)
            except GavaConnectPINNotFoundError as e:
                err_console.print(err_panel(str(e)))
                raise typer.Exit(1)

            if as_json:
                print_json(result)
                return

            pin_data = result.get("PINDATA", {})
            console.print(kv_table([
                ("PIN",           pin_data.get("KRAPIN", pin)),
                ("Name",          pin_data.get("Name", "—")),
                ("Taxpayer type", pin_data.get("TypeOfTaxpayer", "—")),
                ("Status",        pin_data.get("StatusOfPIN", "—")),
                ("Source",        "GavaConnect (direct)"),
            ], title="PIN Lookup"))
        else:
            progress.add_task(f"Validating {pin} via TIaaS...", total=None)
            client = get_client(api_key)
            result = client.lookup_pin(pin)

            if as_json:
                print_json(result)
                return

            data = result.get("data", result)
            console.print(kv_table([
                ("PIN",    pin),
                ("Name",   str(data.get("taxpayerName") or data.get("name") or "—")),
                ("Status", str(data.get("status") or "—")),
                ("Active", "Yes" if data.get("registered") or data.get("active") else "No"),
                ("Source", "TIaaS"),
            ], title="PIN Lookup"))


# ===========================================================================
# tax
# ===========================================================================

@tax_app.command("calculate")
def tax_calculate(
    price: Annotated[str, typer.Option(help="Price per unit (e.g. 5800).")],
    band: Annotated[str, typer.Option(help="Tax band: A, B, C, D, or E.")],
    name: Annotated[str, typer.Option(help="Item name.")] = "Item",
    code: Annotated[str, typer.Option(help="Item code.")] = "ITEM001",
    qty: Annotated[str, typer.Option(help="Quantity (default 1).")] = "1",
    exclusive: Annotated[
        bool,
        typer.Option("--exclusive", help="Price is VAT-exclusive (default: inclusive)."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Calculate KRA-compliant tax splits for an item (offline — no credentials needed).
    Uses calculate_item() from the SDK — same math as a live invoice submission.
    """
    from kra_etims import calculate_item

    try:
        item = calculate_item(
            name=name,
            item_code=code,
            total_price=price,
            tax_band=band,
            qty=qty,
            price_is_inclusive=not exclusive,
        )
    except ValueError as e:
        err_console.print(err_panel(str(e)))
        raise typer.Exit(1)

    if as_json:
        print_json(item.model_dump(mode="json"))
        return

    rate_label = {"A": "0% Exempt", "B": "16% Standard VAT", "C": "0% Zero-Rated",
                  "D": "0% Non-VAT", "E": "8% Special Rate"}.get(band.upper(), band)

    console.print(kv_table([
        ("Band",         f"{band.upper()} — {rate_label}"),
        ("Retail Price", f"KES {item.totAmt:,.2f}  ({'inclusive' if not exclusive else 'exclusive'})"),
        ("Taxable Amt",  f"KES {item.taxblAmt:,.2f}"),
        ("VAT",          f"KES {item.taxAmt:,.2f}"),
        ("Qty",          str(item.qty)),
        ("Unit Price",   f"KES {item.uprc:,.2f}"),
    ], title=f"Tax Calculation — {name}"))


@tax_app.command("bands")
def tax_bands(
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Show the KRA eTIMS tax band reference table (offline — no credentials needed).
    Source: KRA VSCU/OSCU Specification v2.0 §4.1.
    """
    if as_json:
        print_json([
            {"band": "A", "rate": "0%",  "description": "Exempt",       "notes": "No input credit allowed"},
            {"band": "B", "rate": "16%", "description": "Standard VAT", "notes": "Most goods & services"},
            {"band": "C", "rate": "0%",  "description": "Zero-Rated",   "notes": "Exports; input credit allowed"},
            {"band": "D", "rate": "0%",  "description": "Non-VAT",      "notes": "Outside the VAT Act entirely"},
            {"band": "E", "rate": "8%",  "description": "Special Rate", "notes": "Petroleum/LPG — verify Finance Act 2023"},
        ])
        return

    console.print(band_table())
    console.print(
        "\n[dim]Note: A is NOT the 16% band. B is Standard VAT at 16%.[/dim]\n"
        "[dim]Source: KRA VSCU/OSCU Specification v2.0 §4.1[/dim]"
    )


# ===========================================================================
# tcc
# ===========================================================================

@tcc_app.command("check")
def tcc_check(
    pin: Annotated[str, typer.Option(help="KRA PIN of the taxpayer (e.g. A000123456B).")],
    tcc_number: Annotated[str, typer.Option("--tcc-number", help="TCC number to validate.")],
    consumer_key: Annotated[Optional[str], typer.Option("--consumer-key", help="GavaConnect consumer key.")] = None,
    consumer_secret: Annotated[Optional[str], typer.Option("--consumer-secret", help="GavaConnect consumer secret.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """
    Validate a Tax Compliance Certificate (TCC) against KRA via GavaConnect.

    Free — no TIaaS subscription required. Requires GavaConnect credentials
    from https://developer.go.ke (registration is free).

    Use this to verify a supplier or counterparty is currently tax-compliant
    before transacting.
    """
    from kra_etims import KRA_TIN_PATTERN
    from ._client import get_gavaconnect_client
    from kra_etims.gavaconnect import GavaConnectTCCError

    if not KRA_TIN_PATTERN.match(pin):
        err_console.print(err_panel(
            f"[bold]{pin}[/bold] is not a valid KRA PIN format.\n"
            "Run [bold cyan]etims pin check {pin}[/bold cyan] for format details."
        ))
        raise typer.Exit(1)

    gc_client = get_gavaconnect_client(consumer_key, consumer_secret)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Validating TCC {tcc_number}...", total=None)
        try:
            result = gc_client.check_tcc(pin, tcc_number)
        except GavaConnectTCCError as e:
            err_console.print(err_panel(str(e)))
            raise typer.Exit(1)

    if as_json:
        print_json(result)
        return

    tcc_data = result.get("TCCData", {})
    console.print(success_panel(
        f"TCC [bold]{tcc_number}[/bold] is valid for PIN [bold]{pin}[/bold]."
    ))
    console.print(kv_table([
        ("PIN",        tcc_data.get("KRAPIN", pin)),
        ("TCC Number", tcc_number),
        ("Status",     result.get("Status", "OK")),
    ], title="TCC Validation"))


# ===========================================================================
# sandbox
# ===========================================================================

@sandbox_app.command("ping")
def sandbox_ping(
    api_key: Annotated[Optional[str], typer.Option(help="Override API key.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON.")] = False,
) -> None:
    """Health check: verify TIaaS is reachable and measure latency."""
    import time
    from ._client import get_client

    client = get_client(api_key)

    start = time.perf_counter()
    try:
        result = client._request("GET", "/v2/etims/init-handshake")
        ok = True
    except Exception as e:
        result = {"error": str(e)}
        ok = False
    elapsed = (time.perf_counter() - start) * 1000

    if as_json:
        print_json({"ok": ok, "latency_ms": round(elapsed, 1), "response": result})
        return

    if ok:
        console.print(success_panel(
            f"TIaaS is reachable\n"
            f"  Latency: [bold]{elapsed:.0f}ms[/bold]"
        ))
    else:
        err_console.print(err_panel(
            f"TIaaS unreachable ({elapsed:.0f}ms)\n"
            f"  {result.get('error', 'Unknown error')}"
        ))
        raise typer.Exit(1)


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    app()


if __name__ == "__main__":
    main()
