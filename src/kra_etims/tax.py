"""
KRA eTIMS SDK — Zero-Math Tax Calculator
==========================================
Enterprise developers pass retail prices and a tax band.
This module produces a fully-validated, KRA-compliant ItemDetail with
all exclusive amounts, VAT splits, and totals computed to cent precision.

KRA Tax Band Reference — VSCU/OSCU Specification v2.0 §4.1
(confirmed by TIS Spec v2.0 §14 receipt printout sample)

  A  Exempt        —  0%  Supplies exempt from VAT (no input credit)
  B  Standard VAT  — 16%  Standard-rated goods and services
  C  Zero-Rated    —  0%  Exports, zero-rated supplies (input credit allowed)
  D  Non-VAT       —  0%  Supplies outside the VAT Act entirely
  E  Special Rate  —  8%  Petroleum products, LPG per Kenya VAT Act

IMPORTANT: A is NOT 16% standard. B is the 16% standard rate band.
This is counterintuitive but is explicit in §4.1 of both official specs.
"""

import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Union

from .models import ItemDetail, TaxType

# KRA eTIMS VSCU/OSCU Specification v2.0 §4.1 — authoritative rate table.
#
# Rates are loaded from environment variables at module import time.
# Override via ETIMS_TAX_RATE_{A-E} env vars when KRA's live selectCodes
# response (userDfnCd1 field) returns a value that differs from these defaults.
#
# A=0% Exempt, B=16% Standard, C=0% Zero-Rated, D=0% Non-VAT, E=8% Special


def _rate(band: str, default: str) -> Decimal:
    """Read rate from env var ETIMS_TAX_RATE_{BAND}, fall back to default."""
    raw = os.getenv(f"ETIMS_TAX_RATE_{band.upper()}", default).strip()
    return Decimal(raw)


_EXCLUSIVE_RATE: dict[str, Decimal] = {
    "A": _rate("A", "0.00"),   # Exempt — 0%
    "B": _rate("B", "0.16"),   # Standard VAT — 16%
    "C": _rate("C", "0.00"),   # Zero-Rated — 0%
    "D": _rate("D", "0.00"),   # Non-VAT — 0%
    "E": _rate("E", "0.08"),   # Special Rate — 8%
}

_INCLUSIVE_DIVISOR: dict[str, Decimal] = {
    band: Decimal("1") + rate
    for band, rate in _EXCLUSIVE_RATE.items()
}


def _q(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places using KRA-mandated ROUND_HALF_UP."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _qty(value: Decimal) -> Decimal:
    """Quantize quantity to 4 decimal places.

    KRA allows up to 6 significant figures for quantity.  Using 2dp (the
    monetary precision) silently truncates fuel (15.456L), weight (0.375kg),
    and pharmaceutical quantities, causing fiscal misrepresentation.
    """
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def calculate_item(
    name: str,
    item_code: str,
    total_price: Union[Decimal, float, int, str],
    tax_band: str,
    qty: Union[Decimal, float, int, str] = Decimal("1"),
    *,
    price_is_inclusive: bool = True,
    pkg_unit_cd: str = "UNT",
    qty_unit_cd: str = "U",
) -> ItemDetail:
    """
    Compute a KRA-compliant ``ItemDetail`` from a retail price and tax band.

    Parameters
    ----------
    name:
        Human-readable item name (e.g. "Maize Flour 2kg").
    item_code:
        eTIMS-registered item code (e.g. "HS110100").
    total_price:
        The price **per unit**.
        * When ``price_is_inclusive=True`` (default): the retail price already
          includes VAT (i.e. what the customer pays).
        * When ``price_is_inclusive=False``: the net price excluding VAT.
          The SDK will add VAT on top to derive ``totAmt``.
    tax_band:
        One of "A", "B", "C", "D", or "E".
    qty:
        Number of units.  Defaults to 1.
    price_is_inclusive:
        Pricing convention.  POS systems typically use inclusive (default).
    pkg_unit_cd / qty_unit_cd:
        KRA unit codes.  Defaults to "UNT" / "U".

    Returns
    -------
    ItemDetail
        A fully validated Pydantic model ready to embed in a SaleInvoice.

    Raises
    ------
    ValueError
        If tax_band is not one of A–E.

    Example
    -------
    >>> item = calculate_item("Maize", "HS110100", 5000, "A")
    >>> item.taxblAmt   # 4310.34
    >>> item.taxAmt     # 689.66
    >>> item.totAmt     # 5000.00
    """
    band = tax_band.upper().strip()
    if band not in _INCLUSIVE_DIVISOR:
        raise ValueError(
            f"Unknown tax_band '{tax_band}'. Must be one of: A, B, C, D, E."
        )

    price = _q(Decimal(str(total_price)))
    quantity = _qty(Decimal(str(qty)))
    rate = _EXCLUSIVE_RATE[band]

    if price_is_inclusive:
        # Retail price already includes VAT — back-calculate net amount.
        # KRA formula: taxblAmt = totAmt / (1 + rate)
        taxable_unit = _q(price / _INCLUSIVE_DIVISOR[band])
        tax_unit = _q(price - taxable_unit)
        gross_unit = price  # == taxable + tax by construction
    else:
        # Net (exclusive) price supplied — add VAT to arrive at gross.
        taxable_unit = price
        tax_unit = _q(price * rate)
        gross_unit = _q(price + tax_unit)

    # Line totals = unit amounts × quantity
    tot_amt = _q(gross_unit * quantity)
    taxbl_amt = _q(taxable_unit * quantity)
    tax_amt = _q(tax_unit * quantity)

    # Ensure rounding doesn't leave a 1-cent gap: assign residual to taxAmt.
    rounding_residual = tot_amt - taxbl_amt - tax_amt
    if rounding_residual != Decimal("0"):
        tax_amt = _q(tax_amt + rounding_residual)

    return ItemDetail(
        itemCd=item_code,
        itemNm=name,
        pkgUnitCd=pkg_unit_cd,
        pkg=quantity,
        qtyUnitCd=qty_unit_cd,
        qty=quantity,
        uprc=gross_unit,
        totAmt=tot_amt,
        taxTyCd=TaxType(band),
        taxblAmt=taxbl_amt,
        taxAmt=tax_amt,
    )


def build_invoice_totals(items: list[ItemDetail]) -> dict:
    """
    Aggregate line-item totals into the three top-level invoice fields.

    Returns a dict suitable for ``**``-unpacking into SaleInvoice:
    ``totItemCnt``, ``totTaxblAmt``, ``totTaxAmt``, ``totAmt``.
    """
    tot_taxbl = _q(sum((i.taxblAmt for i in items), Decimal("0")))
    tot_tax   = _q(sum((i.taxAmt   for i in items), Decimal("0")))
    tot_amt   = _q(sum((i.totAmt   for i in items), Decimal("0")))

    # Cross-band integrity: if per-line ROUND_HALF_UP accumulates a residual
    # at invoice level, assign it to tot_tax (mirrors the per-line convention
    # in calculate_item). Without this, KRA rejects the invoice with code 20.
    residual = tot_amt - tot_taxbl - tot_tax
    if residual != Decimal("0"):
        tot_tax = _q(tot_tax + residual)

    return {
        "totItemCnt":  len(items),
        "totTaxblAmt": tot_taxbl,
        "totTaxAmt":   tot_tax,
        "totAmt":      tot_amt,
    }
