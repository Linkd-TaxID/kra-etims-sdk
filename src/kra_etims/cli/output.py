"""
Rich console helpers and JSON output.

Every command produces human-readable output via Rich by default.
Pass --json to any command to emit raw JSON to stdout (pipe-safe, bypasses Rich).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# JSON output (pipe-safe, bypasses Rich)
# ---------------------------------------------------------------------------

class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def print_json(data: Any) -> None:
    """Emit data as JSON to stdout. Bypasses Rich entirely — safe to pipe to jq."""
    print(json.dumps(data, cls=_DecimalEncoder, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def success_panel(message: str, title: str = "") -> Panel:
    return Panel(message, border_style="green", title=title or None, expand=False)


def err_panel(message: str, title: str = "Error") -> Panel:
    return Panel(message, border_style="red", title=title, expand=False)


def warn_panel(message: str, title: str = "Warning") -> Panel:
    return Panel(message, border_style="yellow", title=title, expand=False)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def kv_table(rows: list[tuple[str, str]], title: str = "") -> Table:
    """Two-column key-value detail table (no header)."""
    t = Table(box=box.SIMPLE, show_header=False, title=title or None, expand=False)
    t.add_column(style="dim", no_wrap=True, min_width=18)
    t.add_column()
    for k, v in rows:
        t.add_row(k, str(v))
    return t


def band_table() -> Table:
    """Authoritative KRA eTIMS tax band reference table."""
    t = Table(box=box.SIMPLE, title="KRA eTIMS Tax Bands — VSCU/OSCU Spec v2.0 §4.1")
    t.add_column("Band", style="bold", width=6)
    t.add_column("Rate", justify="right", width=6)
    t.add_column("Description")
    t.add_column("Notes", style="dim")

    rows = [
        ("A",  " 0%", "Exempt",        "No input credit allowed"),
        ("B",  "16%", "Standard VAT",  "Most goods & services ← common default"),
        ("C",  " 0%", "Zero-Rated",    "Exports; input credit allowed"),
        ("D",  " 0%", "Non-VAT",       "Outside the VAT Act entirely"),
        ("E",  " 8%", "Special Rate",  "Petroleum / LPG — verify Finance Act 2023"),
    ]
    for band, rate, desc, notes in rows:
        t.add_row(band, rate, desc, notes)
    return t


def report_band_table(
    band_a: Any,
    band_b: Any,
    band_c: Any,
    band_d: Any,
    band_e: Any,
) -> Table:
    """Per-band breakdown table for X/Z reports."""
    t = Table(box=box.SIMPLE)
    t.add_column("Band", style="bold", width=6)
    t.add_column("Taxable Amt", justify="right")
    t.add_column("Tax Amt", justify="right")

    for label, band in [
        ("A (Exempt)", band_a),
        ("B (16%)", band_b),
        ("C (Zero)", band_c),
        ("D (Non-VAT)", band_d),
        ("E (8%)", band_e),
    ]:
        t.add_row(
            label,
            f"KES {band.taxable_amount:,.2f}",
            f"KES {band.tax_amount:,.2f}",
        )
    return t
