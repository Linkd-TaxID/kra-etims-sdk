from datetime import datetime
from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, Field

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

class DeviceInit(BaseModel):
    tin: str = Field(..., description="Taxpayer Identification Number")
    bhfId: str = Field(..., description="Branch ID")
    dvcSrlNo: str = Field(..., description="Device Serial Number")

# --- Category 2: Data Sync ---

class DataSyncRequest(BaseModel):
    tin: str
    bhfId: str
    lastReqDt: str  # YYYYMMDDHHmmss

# --- Category 3: Branch Management ---

class BranchInfo(BaseModel):
    tin: str
    bhfId: str
    bhfNm: str
    bhfOpenDt: str
    bhfSttsCd: str

# --- Category 4: Item Management ---

class ItemSave(BaseModel):
    tin: str
    bhfId: str
    itemCd: str
    itemClsCd: str
    itemNm: str
    itemTyCd: ItemType
    taxTyCd: TaxType
    uprc: float
    isUsed: str = "Y"

# --- Category 5: Import Information ---

class ImportItem(BaseModel):
    tin: str
    bhfId: str
    dclNo: str  # Declaration Number
    itemSeq: int
    itemCd: str
    qty: float
    prc: float

# --- Category 6 & 7: Transactional Core ---

class ItemDetail(BaseModel):
    itemCd: str = Field(..., description="Item Code")
    itemNm: str = Field(..., description="Item Name")
    pkgUnitCd: str = "UNT"
    pkg: float = 1.0
    qtyUnitCd: str = "U"
    qty: float = Field(..., description="Quantity")
    uprc: float = Field(..., description="Unit Price")
    totAmt: float = Field(..., description="Total Amount (qty * uprc)")
    taxTyCd: TaxType = Field(..., description="Tax Type Code (A/B/C/D/E)")
    taxblAmt: float = Field(..., description="Taxable Amount")
    taxAmt: float = Field(..., description="Tax Amount")

class InvoiceBase(BaseModel):
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
    totTaxblAmt: float
    totTaxAmt: float
    totAmt: float
    itemList: List[ItemDetail]

class SaleInvoice(InvoiceBase):
    """Category 6: Sales Invoice"""
    pass

class ReverseInvoice(InvoiceBase):
    """Category 7: Credit Note / Reverse"""
    orgInvcNo: str = Field(..., description="Original Invoice Number to reverse")

# --- Category 8: Stock Management ---

class StockItem(BaseModel):
    tin: str
    bhfId: str
    itemCd: str
    rsonCd: str # Reason Code
    qty: float
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
