from datetime import datetime
from enum import Enum
from typing import List, Optional, Union
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, Field, model_validator, ConfigDict

# --- Consumable Enums ---

class ItemType(str, Enum):
    GOODS = "1"
    SERVICE = "2"

class TaxType(str, Enum):
    A = "A"  # 16%
    B = "B"  # 8%
    C = "C"  # 0% (Exempt)
    D = "D"  # 0% (Zero Rated)
    E = "E"  # 0% (Non-VAT)

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
    lastReqDt: str  # YYYYMMDDHHmmss

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
    pkgUnitCd: str = "UNT"
    pkg: Decimal = Decimal("1.0")
    qtyUnitCd: str = "U"
    qty: Decimal = Field(..., description="Quantity")
    uprc: Decimal = Field(..., description="Unit Price")
    totAmt: Decimal = Field(..., description="Total Amount (qty * uprc)")
    taxTyCd: TaxType = Field(..., description="Tax Type Code (A/B/C/D/E)")
    taxblAmt: Decimal = Field(..., description="Taxable Amount")
    taxAmt: Decimal = Field(..., description="Tax Amount")

    @model_validator(mode='after')
    def validate_math(self) -> 'ItemDetail':
        # 1. Total Amount = Qty * Price
        # We quantize the expected total to 2 decimal places to match KRA rounding
        expected_tot = (self.qty * self.uprc).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # Strict check: input must match expected_tot EXACTLY. 
        # This catches float drift because Decimal("300.3000...04") != Decimal("300.30")
        if self.totAmt != expected_tot:
             raise ValueError(f"Math Error: totAmt ({self.totAmt}) must be exactly qty * uprc = {expected_tot}. Detected precision drift or mismatch.")

        # 2. Taxable + Tax = Total
        if (self.taxblAmt + self.taxAmt).quantize(Decimal('0.01')) != self.totAmt.quantize(Decimal('0.01')):
            raise ValueError(f"Math Error: taxblAmt ({self.taxblAmt}) + taxAmt ({self.taxAmt}) must be exactly totAmt ({self.totAmt})")

        return self

class InvoiceBase(BaseSchema):
    tin: str
    bhfId: str
    invcNo: str
    orgInvcNo: Optional[str] = None
    custPin: Optional[str] = None
    custNm: str
    rcptTyCd: str = "S" # Sale
    pmtTyCd: str = "01" # Cash by default
    rcptLbel: ReceiptLabel = ReceiptLabel.NORMAL
    confirmDt: str # YYYYMMDDHHmmss
    totItemCnt: int
    totTaxblAmt: Decimal
    totTaxAmt: Decimal
    totAmt: Decimal
    itemList: List[ItemDetail]

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
    tin: str
    bhfId: str
    itemCd: str
    rsonCd: str # Reason Code
    qty: Decimal
    tin2: Optional[str] = None # For transfer
    bhfId2: Optional[str] = None

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
