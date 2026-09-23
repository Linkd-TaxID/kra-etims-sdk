import re
from enum import Enum
from typing import List, Optional
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

_LAST_REQ_DT_RE = re.compile(r'^\d{14}$')  # YYYYMMDDHHmmss — exactly 14 digits

# ---------------------------------------------------------------------------
# Single source-of-truth for KRA PIN format.
# Mirrors KraTinConstraintValidator.PATTERN in the TIaaS middleware.
# Pattern: one uppercase letter + 9 digits + one uppercase letter (e.g. A000123456B)
# ---------------------------------------------------------------------------
KRA_TIN_PATTERN = re.compile(r'^[A-Z]\d{9}[A-Z]$')

# --- Consumable Enums ---

class ItemType(str, Enum):
    GOODS = "1"
    SERVICE = "2"

class TaxType(str, Enum):
    # Source: KRA VSCU/OSCU Specification v2.0 §4.1 (both documents identical).
    # Confirmed by TIS Spec v2.0 §14 receipt sample: Band B prints "TOTAL B-16.00%".
    # A≠16% standard. B is the 16% standard rate band.
    A = "A"  #  0%  Exempt (supplies exempt from VAT; no input credit)
    B = "B"  # 16%  Standard VAT (most goods and services)
    C = "C"  #  0%  Zero-Rated (exports, certain food; input credit allowed)
    D = "D"  #  0%  Non-VAT (outside the VAT Act entirely)
    E = "E"  #  8%  Special Rate (petroleum products, LPG per Kenya VAT Act)

class ReceiptLabel(str, Enum):
    NORMAL = "NS"  # Normal Sale
    COPY = "CS"    # Copy Sale
    TRAINING = "TS" # Training Sale
    PROFORMA = "PS" # Proforma

# --- Category 1: Initialization ---
class BaseSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')

class DeviceInit(BaseSchema):
    tin: str = Field(..., description="Taxpayer Identification Number")
    bhfId: str = Field(..., description="Branch ID")
    dvcSrlNo: str = Field(..., description="Device Serial Number")

# --- Category 2: Data Sync ---

class DataSyncRequest(BaseSchema):
    tin: str
    bhfId: str
    lastReqDt: str  # YYYYMMDDHHmmss — enforced by validator below

    @field_validator('lastReqDt', mode='before')
    @classmethod
    def validate_last_req_dt(cls, v: str) -> str:
        if not isinstance(v, str) or not _LAST_REQ_DT_RE.match(v):
            raise ValueError(
                f"lastReqDt '{v}' is not in YYYYMMDDHHmmss format (expected 14 digits, e.g. '20260419000000'). "
                "The VSCU JAR returns error E31 on malformed lastReqDt."
            )
        return v

# --- Category 3: Branch Management ---

class BranchInfo(BaseSchema):
    tin: str
    bhfId: str
    bhfNm: str
    bhfOpenDt: str
    bhfSttsCd: str

# --- Category 4: Item Management ---

class ItemSave(BaseSchema):
    tin: str
    bhfId: str
    itemCd: str
    itemClsCd: str
    itemNm: str
    itemTyCd: ItemType
    taxTyCd: TaxType
    uprc: Decimal
    isUsed: str = "Y"
    # Optional KRA unit codes (VSCU Spec v2.0 §4.5 / §4.6). The middleware
    # defaults both to "U" when omitted; override for weighed or bulk goods
    # (e.g. qtyUnitCd="LTR" for fuel sold by the litre).
    pkgUnitCd: Optional[str] = None
    qtyUnitCd: Optional[str] = None
    bcd: Optional[str] = None

# --- Category 5: Import Information ---

class ImportItem(BaseSchema):
    tin: str
    bhfId: str
    dclNo: str  # Declaration Number
    itemSeq: int
    itemCd: str
    qty: Decimal
    prc: Decimal

# --- Category 6 & 7: Transactional Core ---

class ItemDetail(BaseSchema):
    itemCd: str = Field(..., description="Item Code")
    itemNm: str = Field(..., description="Item Name")
    # UN/CEFACT commodity classification code (8–10 digits). Optional for the flat
    # single-band middleware payload, but REQUIRED on every line of a mixed-band
    # invoice so the middleware can book each line under its own band (V14 per-band
    # aggregation). Verify against the middleware's commodity_codes cache.
    itemClsCd: Optional[str] = Field(default=None, description="UN/CEFACT commodity classification code")
    # "NT" per spec §4.5; the VSCU rejects "UNT" with 913 on item auto-registration.
    pkgUnitCd: str = "NT"
    pkg: Decimal = Decimal("1.0")
    qtyUnitCd: str = "U"
    qty: Decimal = Field(..., description="Quantity")
    uprc: Decimal = Field(..., description="Unit Price")
    # Supply amount = qty × uprc before discount. Informational; the middleware derives it.
    splyAmt: Decimal = Field(default=Decimal("0.00"), description="Supply amount (qty * uprc, pre-discount)")
    # Line discount, as a percentage of qty × uprc and/or an amount in KES. When both are
    # set, dcAmt must equal round(qty × uprc × dcRt / 100, 2). Sent to the middleware as
    # items[].discount (or items[].discountRate when only dcRt is set).
    dcRt: Decimal = Field(default=Decimal("0.00"), description="Discount rate (%)")
    dcAmt: Decimal = Field(default=Decimal("0.00"), description="Discount amount (KES)")
    totAmt: Decimal = Field(..., description="Total Amount (qty * uprc - discount, tax-inclusive)")
    taxTyCd: TaxType = Field(..., description="Tax Type Code (A/B/C/D/E)")
    taxblAmt: Decimal = Field(..., description="Taxable Amount (net, VAT-exclusive)")
    taxAmt: Decimal = Field(..., description="Tax Amount")

    @field_validator('pkg', 'qty', 'uprc', 'splyAmt', 'dcRt', 'dcAmt',
                     'totAmt', 'taxblAmt', 'taxAmt', mode='before')
    @classmethod
    def reject_float(cls, v):
        # A float has already lost precision before pydantic sees it
        # (0.1 + 0.2 -> Decimal('0.3000000000000000444...')). Strings such as
        # "300.30" and ints are unaffected.
        if isinstance(v, float):
            raise ValueError(
                f"float {v!r} is not accepted for monetary/quantity fields; "
                "pass Decimal or str (e.g. Decimal('300.30'))."
            )
        return v

    def discount(self) -> Decimal:
        """Line discount in KES: ``dcAmt``, or ``qty × uprc × dcRt / 100`` when only a rate is set."""
        if self.dcAmt:
            return self.dcAmt
        gross = (self.qty * self.uprc).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return (gross * self.dcRt / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @model_validator(mode='after')
    def validate_math(self) -> 'ItemDetail':
        gross = (self.qty * self.uprc).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if self.dcRt < 0 or self.dcAmt < 0:
            raise ValueError("dcRt and dcAmt must not be negative.")
        if self.dcRt > 100:
            raise ValueError(f"dcRt ({self.dcRt}) must not exceed 100.")
        for name, value in (("dcRt", self.dcRt), ("dcAmt", self.dcAmt)):
            if value.normalize().as_tuple().exponent < -2:
                raise ValueError(f"{name} ({value}) must have at most 2 decimal places.")
        if self.dcAmt and self.dcRt:
            from_rate = (gross * self.dcRt / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if self.dcAmt != from_rate:
                raise ValueError(
                    f"dcAmt ({self.dcAmt}) must equal qty * uprc * dcRt / 100 = {from_rate}; "
                    "set only one of them to let the other be derived."
                )
        discount = self.discount()
        if discount > gross:
            raise ValueError(f"Discount {discount} exceeds the line amount qty * uprc = {gross}.")

        # Strict check: input must match the expected total EXACTLY.
        # This catches float drift because Decimal("300.3000...04") != Decimal("300.30")
        expected_tot = gross - discount
        if self.totAmt != expected_tot:
            rule = "qty * uprc" if not discount else f"qty * uprc - discount ({gross} - {discount})"
            raise ValueError(
                f"Math Error: totAmt ({self.totAmt}) must be exactly {rule} = {expected_tot}. "
                "Detected precision drift or mismatch."
            )

        # 2. Taxable + Tax = Total
        if (self.taxblAmt + self.taxAmt).quantize(Decimal('0.01')) != self.totAmt.quantize(Decimal('0.01')):
            raise ValueError(
                f"Math Error: taxblAmt ({self.taxblAmt}) + taxAmt ({self.taxAmt}) "
                f"must be exactly totAmt ({self.totAmt})"
            )

        return self

class InvoiceBase(BaseSchema):
    tin: str
    bhfId: str
    invcNo: str
    orgInvcNo: Optional[str] = None
    custPin: Optional[str] = None
    # B2C (retail) invoices have no identifiable customer. KRA eTIMS Spec v2.0 §4.1 requires
    # custNm to be present in the payload but does not mandate a specific string for B2C.
    # Community implementations (navariltd/kenya-compliance) and the KRA eTIMS Lite UI
    # use "N/A" as the de-facto standard for anonymous retail customers.
    # Override with the actual customer name for B2B sales.
    custNm: str = "N/A"
    rcptTyCd: str = "S" # Sale
    pmtTyCd: str = "01" # Cash by default
    rcptLbel: ReceiptLabel = ReceiptLabel.NORMAL
    confirmDt: str # YYYYMMDDHHmmss
    totItemCnt: int
    totTaxblAmt: Decimal
    totTaxAmt: Decimal
    totAmt: Decimal
    itemList: List[ItemDetail]

    @field_validator('custPin', mode='before')
    @classmethod
    def validate_cust_pin(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not KRA_TIN_PATTERN.match(v):
            raise ValueError(
                f"custPin '{v}' is not a valid KRA PIN. "
                "Expected format: A000000000B (1 uppercase letter + 9 digits + 1 uppercase letter)."
            )
        return v

    @model_validator(mode='after')
    def validate_invoice_totals(self) -> 'InvoiceBase':
        # sum of items
        sum_items_tot = sum((item.totAmt for item in self.itemList), Decimal("0"))
        if self.totAmt.quantize(Decimal('0.01')) != sum_items_tot.quantize(Decimal('0.01')):
            raise ValueError(f"Math Error: Invoice totAmt ({self.totAmt}) must match sum of items ({sum_items_tot})")
        
        return self

class SaleInvoice(InvoiceBase):
    """Category 6: Sales Invoice"""
    pass

class ReverseInvoice(InvoiceBase):
    """Category 7: Credit Note / Reverse"""
    orgInvcNo: str = Field(..., description="Original Invoice Number to reverse")

# --- Category 8: Stock Management ---

class StockItem(BaseSchema):
    """
    .. deprecated::
        Use :class:`StockAdjustmentLine` with :meth:`KRAeTIMSClient.submit_stock_adjustment`.
        This model targets the legacy ``/v2/etims/stock`` endpoint.
    """
    tin: str
    bhfId: str
    itemCd: str
    rsonCd: str # Reason Code
    qty: Decimal
    tin2: Optional[str] = None # For transfer
    bhfId2: Optional[str] = None


class StockAdjustmentLine(BaseSchema):
    """
    One line item in a stock adjustment batch.

    ``ioType`` must be one of:
    - ``M`` — Import / Goods Received (IN)
    - ``A`` — Adjustment (OUT, e.g. write-off, spoilage)
    - ``I`` — Issue / Transfer (OUT)

    Financial totals (``splyAmt``, ``taxblAmt``, ``taxAmt``, ``totAmt``) are
    computed server-side by the middleware.  Do not supply them — the server
    will reject any client-supplied totals to prevent tax manipulation.
    """
    itemCd: str = Field(..., description="Item code registered on eTIMS")
    itemNm: str = Field(..., description="Item name")
    ioType: str = Field(..., pattern=r'^[MAI]$', description="I/O type: M=Import, A=Adjustment, I=Issue")
    # Middleware validation requires both unit codes; None was rejected with HTTP 400.
    # Defaults per KRA VSCU Spec v2.0 §4.5 (Packaging Unit: NT=Net) and §4.6
    # (Quantity Unit: U=Pieces/Number). Override for weighed or bulk goods.
    pkgUnitCd: str = Field(default="NT", description="Packaging unit code (spec §4.5)")
    pkgQty: Decimal = Field(default=Decimal("1"), description="Package quantity")
    qtyUnitCd: str = Field(default="U", description="Quantity unit code (spec §4.6)")
    qty: Decimal = Field(..., description="Quantity", gt=Decimal("0"))
    prc: Decimal = Field(..., description="Unit price (KES)")
    totDcAmt: Decimal = Field(default=Decimal("0"), description="Total discount amount")
    taxTyCd: TaxType = Field(..., description="Tax type code (A/B/C/D/E)")
    barcode: Optional[str] = None


class StockAdjustmentRequest(BaseSchema):
    """
    Request body for ``POST /v2/etims/stock/adjustment``.

    The middleware assigns the ``sarNo`` (monotonic per-tenant sequence number),
    computes all financial totals, calls the VSCU JAR, and persists the signed
    record.  The client supplies only line-level facts (item, quantity, price).
    """
    lines: List[StockAdjustmentLine] = Field(..., min_length=1, description="At least one line required")
    custTin: Optional[str] = Field(None, description="Customer KRA TIN (optional — for B2B movements)")
    custNm: Optional[str] = Field(None, description="Customer name")
    remark: Optional[str] = Field(None, description="Free-text remark (max 400 chars)")
    orgSarNo: int = Field(default=0, description="Original SAR number (0 for new adjustments)")

    @field_validator('custTin', mode='before')
    @classmethod
    def validate_cust_tin(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not KRA_TIN_PATTERN.match(v):
            raise ValueError(
                f"custTin '{v}' is not a valid KRA PIN. "
                "Expected format: A000000000B (1 uppercase letter + 9 digits + 1 uppercase letter)."
            )
        return v


ETIMS_MODELS = {
    "1": DeviceInit,
    "2": DataSyncRequest,
    "3": BranchInfo,
    "4": ItemSave,
    "5": ImportItem,
    "6": SaleInvoice,
    "7": ReverseInvoice,
    "8": StockItem,
}


def to_middleware_item_payload(item: "ItemSave") -> dict:
    """
    Translate a KRA-native :class:`ItemSave` into the TIaaS middleware's
    ``POST /v2/etims/items`` request schema.

    The middleware's item registry keys items by ``sku`` (the caller's
    internal product identifier), generates the VSCU ``itemCd`` server-side,
    and requires ``qty``/``unitPrice`` for the registry row. Prior to v0.4.1
    the SDK posted the raw KRA-native schema to ``/v2/etims/item`` (singular),
    which the middleware rejected — the same wire-contract drift fixed for
    ``submit_sale`` in v0.4.0.

    Field derivation:

    - ``sku``       ← ``item.itemCd`` (caller's product identifier; the
      middleware derives the actual VSCU ``itemCd`` from SHA-256(tin+bhfId+sku))
    - ``unitPrice`` ← ``item.uprc``
    - ``qty``       ← ``1`` (registration default; sales carry real quantities)
    - unit codes / barcode pass through when set
    """
    payload = {
        "sku":       item.itemCd,
        "itemNm":    item.itemNm,
        "itemClsCd": item.itemClsCd,
        "taxTyCd":   str(getattr(item.taxTyCd, "value", item.taxTyCd)),
        "qty":       "1",
        "unitPrice": str(item.uprc),
    }
    if item.pkgUnitCd:
        payload["pkgUnitCd"] = item.pkgUnitCd
    if item.qtyUnitCd:
        payload["qtyUnitCd"] = item.qtyUnitCd
    if item.bcd:
        payload["bcd"] = item.bcd
    return payload


def to_middleware_sale_payload(invoice: "SaleInvoice") -> dict:
    """
    Translate a KRA-native :class:`SaleInvoice` into the TIaaS middleware's
    ``POST /v2/etims/sale`` request schema.

    The middleware exposes a deliberately flat contract (``supplierPin``,
    ``amount``, ``invoiceDate``, ``taxBand``, ``taxAmount``, ``buyerPin``,
    ``buyerName``) and constructs the spec-compliant ``TrnsSalesSaveWrReq``
    server-side (KRA VSCU Specification v2.0 §3.3.6). Prior to v0.3.1 the SDK
    posted the raw KRA-native schema, which the middleware rejected with
    HTTP 400 on every call — the two projects had drifted apart.

    Field derivation:

    - ``supplierPin``  ← ``invoice.tin`` (the selling taxpayer's PIN)
    - ``amount``       ← ``invoice.totAmt``
    - ``invoiceDate``  ← ``invoice.confirmDt`` (``yyyyMMddHHmmss`` → ISO date)
    - ``taxBand``      ← the single band shared by every line, for a single-band
      invoice. **Mixed-band or discounted invoices** send the ``items`` array
      instead (middleware V14 per-band aggregation; per-line ``discount`` /
      ``discountRate``), and omit the receipt-level ``taxBand``. Each line then
      requires ``itemClsCd`` — a :class:`ValueError` is raised if any is missing.
    - ``taxAmount``    ← ``invoice.totTaxAmt``
    - ``pmtTyCd``      ← ``invoice.pmtTyCd`` (KRA §4.7: 01 cash … 06 mobile money)
    - ``buyerPin`` / ``buyerName`` ← ``custPin`` / ``custNm`` (B2B only;
      omitted entirely for B2C, where ``custPin`` is ``None``)
    """
    dt = invoice.confirmDt
    invoice_date = f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]}"

    bands = {str(getattr(i.taxTyCd, "value", i.taxTyCd)) for i in invoice.itemList}
    description = ", ".join(i.itemNm for i in invoice.itemList[:3])[:200] or "General supply"

    payload = {
        "supplierPin":     invoice.tin,
        "amount":          str(invoice.totAmt),
        "invoiceDate":     invoice_date,
        "itemDescription": description,
        "taxAmount":       str(invoice.totTaxAmt),
        "pmtTyCd":         invoice.pmtTyCd,
    }

    discounted = any(i.dcRt or i.dcAmt for i in invoice.itemList)
    if len(bands) > 1 or discounted:
        # Itemised: each line is booked under its own band and carries its own
        # discount; the flat path has nowhere to put either. Receipt-level taxBand is
        # omitted (it is only a label for the flat path).
        reason = f"mixed-band ({sorted(bands)})" if len(bands) > 1 else "discounted"
        missing = [i.itemCd for i in invoice.itemList if not i.itemClsCd]
        if missing:
            raise ValueError(
                f"SaleInvoice {invoice.invcNo!r} is {reason} but line(s) {missing} have no "
                "itemClsCd. A commodity classification code is required on every line of an "
                "itemised (mixed-band or discounted) invoice."
            )
        payload["items"] = [_to_middleware_sale_line(i) for i in invoice.itemList]
    else:
        band = bands.pop() if bands else "B"
        payload["taxBand"] = band
        # Flat path: the middleware books and validates VAT on the receipt
        # total (amount x rate / (1 + rate), HALF_UP). Summed per-line
        # rounding drifts past its 0.02 tolerance on multi-line tickets.
        from .tax import _EXCLUSIVE_RATE, _q
        rate = _EXCLUSIVE_RATE[band]
        payload["taxAmount"] = str(_q(invoice.totAmt * rate / (1 + rate)))

    if invoice.custPin:
        payload["buyerPin"]  = invoice.custPin
        payload["buyerName"] = invoice.custNm
    return payload


def _to_middleware_sale_line(item: "ItemDetail") -> dict:
    """One entry in the middleware ``items`` array (``SaleItemDto``) for a sale line."""
    line = {
        "sku":       item.itemCd,
        "itemNm":    item.itemNm,
        "itemClsCd": item.itemClsCd,
        "taxTyCd":   str(getattr(item.taxTyCd, "value", item.taxTyCd)),
        "qty":       str(item.qty),
        "unitPrice": str(item.uprc),
    }
    if item.pkgUnitCd:
        line["pkgUnitCd"] = item.pkgUnitCd
    if item.qtyUnitCd:
        line["qtyUnitCd"] = item.qtyUnitCd
    # The middleware encodes the discount for the control unit: whole percents as
    # dcRt/dcAmt, anything else folded into a net unit price.
    if item.dcAmt:
        line["discount"] = str(item.dcAmt)
    elif item.dcRt:
        line["discountRate"] = str(item.dcRt)
    return line
