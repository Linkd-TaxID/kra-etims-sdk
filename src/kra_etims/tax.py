"""
KRA eTIMS SDK — Zero-Math Tax Calculator
==========================================
Enterprise developers pass retail prices and a tax band.
This module produces a fully-validated, KRA-compliant ItemDetail with
all exclusive amounts, VAT splits, and totals computed to cent precision.

KRA Tax Band Reference (eTIMS v2.0):
  A  Standard Rate — 16% VAT (inclusive)
  B  Petroleum Products — 8% VAT (inclusive)
  C  Exempt — 0% (no VAT credit; e.g., basic foodstuffs)
  D  Zero-Rated — 0% (VAT credit allowed; e.g., exports)
  E  Non-VAT — outside the scope of VAT (e.g., salary deductions)
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

from .models import ItemDetail, TaxType

# Multiplier stored as (1 + rate) to compute taxable from inclusive price.
_INCLUSIVE_DIVISOR: dict[str, Decimal] = {
    "A": Decimal("1.16"),
    "B": Decimal("1.08"),
    "C": Decimal("1.00"),
    "D": Decimal("1.00"),
    "E": Decimal("1.00"),
}

_EXCLUSIVE_RATE: dict[str, Decimal] = {
    "A": Decimal("0.16"),
    "B": Decimal("0.08"),
    "C": Decimal("0.00"),
    "D": Decimal("0.00"),
    "E": Decimal("0.00"),
}


def _q(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places using KRA-mandated ROUND_HALF_UP."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    quantity = _q(Decimal(str(qty)))
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
    return {
        "totItemCnt":  len(items),
        "totTaxblAmt": tot_taxbl,
        "totTaxAmt":   tot_tax,
        "totAmt":      tot_amt,
    }
