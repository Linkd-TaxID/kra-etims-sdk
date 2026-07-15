#!/usr/bin/env python3
"""
Live KRA-sandbox simulation: one compressed trading day at a busy Nairobi
petrol station ("Jua Kali Energies — Kikuyu Road Service Station"), driven
through the taxid-etims SDK against api.taxid.co.ke -> real VSCU JAR -> KRA.

Fidelity notes (researched 2026-07-07):
  - EPRA price caps, Nairobi, 15 Jun-14 Jul 2026: PMS 214.03/L, AGO 222.86/L,
    kerosene 191.38/L.
  - VAT on petrol/diesel/kerosene temporarily cut to 8% (Band E) in April 2026,
    in force through 14 Jul 2026 -> fuel = Band E.
  - LPG zero-rated since Finance Act 2023 -> Band C.
  - Lubricants / shop goods standard-rated -> Band B (16%).
Flow: catalog registration -> tanker delivery stock-in (M) -> morning rush ->
mis-key + LIVE CREDIT NOTE -> X report -> afternoon trade -> shrinkage
write-off (A) -> Z close.
"""
import json
import os
import random
import sys
import time
import warnings
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from kra_etims.client import KRAeTIMSClient
from kra_etims.models import (
    ItemSave, ItemType, TaxType, SaleInvoice, ItemDetail,
    StockAdjustmentLine,
)

warnings.filterwarnings("ignore", category=UserWarning)

BASE_URL = os.environ.get("TIAAS_URL", "https://api.taxid.co.ke")
API_KEY  = os.environ["SANDBOX_SDK_KEY"]
TIN, BHF = "A000000000Z", "00"
TODAY    = datetime.now().strftime("%Y-%m-%d")

FAST   = "--fast" in sys.argv        # skip jitter for smoke runs
RESUME = "--resume" in sys.argv      # skip catalog + deliveries (already on ledger)
TRANSCRIPT = []

TWO = Decimal("0.01")
RATE = {"E": Decimal("0.08"), "B": Decimal("0.16"),
        "A": Decimal("0"), "C": Decimal("0"), "D": Decimal("0")}


def log(actor, action, detail, resp=None):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "actor": actor, "action": action, "detail": detail}
    if resp is not None:
        entry["response"] = resp
    TRANSCRIPT.append(entry)
    rcpt = ""
    if isinstance(resp, dict):
        rcpt = resp.get("cuInvoiceNumber") or resp.get("status") or ""
        if resp.get("purchaseId"):
            rcpt = f"{rcpt} (id={resp['purchaseId']})"
    print(f"[{ts}] {actor:<9} | {action:<14} | {detail} {('-> ' + str(rcpt)) if rcpt else ''}",
          flush=True)


def pause(lo, hi):
    if not FAST:
        time.sleep(random.uniform(lo, hi))


def vat_split(gross: Decimal, band: str):
    """VAT-inclusive split: returns (taxable, tax)."""
    rate = RATE[band]
    if rate == 0:
        return gross.quantize(TWO), Decimal("0.00")
    taxable = (gross / (Decimal("1") + rate)).quantize(TWO, ROUND_HALF_UP)
    return taxable, (gross - taxable).quantize(TWO)


# ---------------------------------------------------------------------------
# Catalog — SKU -> (name, itemClsCd, band, pump price, qty unit)
# itemClsCd values verified present in the live KRA classification cache.
# ---------------------------------------------------------------------------
CATALOG = {
    "PMS-SUPER":  ("Super Petrol (PMS)",        "15100000", "E", Decimal("214.03"), "LTR"),
    "AGO-DIESEL": ("Automotive Diesel (AGO)",   "15100000", "E", Decimal("222.86"), "LTR"),
    "IK-KERO":    ("Illuminating Kerosene",     "15100000", "E", Decimal("191.38"), "LTR"),
    "LPG-13KG":   ("LPG Refill 13kg Cylinder",  "15110000", "C", Decimal("3150.00"), "U"),
    "OIL-5W40-4L":("Engine Oil 5W-40 4L",       "15120000", "B", Decimal("4650.00"), "U"),
    "SODA-500ML": ("Soda 500ml",                "50200000", "B", Decimal("80.00"),  "U"),
    "WATER-1L":   ("Mineral Water 1L",          "50200000", "B", Decimal("100.00"), "U"),
    "CARWASH":    ("Executive Car Wash",        "76000000", "B", Decimal("500.00"), "U"),
}

pos_counter = 5400          # POS receipt sequence (5200-block used by the aborted first shift)
sold = []                   # (purchaseId, gross, band) for credit-note pick + recon
expected = {b: {"taxbl": Decimal("0"), "tax": Decimal("0"), "gross": Decimal("0")}
            for b in "ABCDE"}
credit_notes = []
failures = []


def next_receipt():
    global pos_counter
    pos_counter += 1
    return f"JKE-{TODAY.replace('-', '')}-{pos_counter}"


def make_invoice(rcpt_no, item_nm, band, gross, qty=None, uprc=None):
    """KRA-native SaleInvoice for the SDK flat path (single line)."""
    if qty is None:
        qty, uprc = Decimal("1"), gross
    taxbl, tax = vat_split(gross, band)
    item = ItemDetail(
        itemCd="POS-LINE", itemNm=item_nm,
        qty=qty, uprc=uprc,
        splyAmt=gross, totAmt=gross,
        taxTyCd=TaxType(band), taxblAmt=taxbl, taxAmt=tax,
    )
    return SaleInvoice(
        tin=TIN, bhfId=BHF, invcNo=rcpt_no,
        confirmDt=datetime.now().strftime("%Y%m%d%H%M%S"),
        totItemCnt=1, totTaxblAmt=taxbl, totTaxAmt=tax, totAmt=gross,
        itemList=[item],
    )


def flat_sale(client, actor, item_nm, band, gross, note=""):
    """Preset-amount pump sale — the amount is the primitive (pump preset)."""
    rcpt = next_receipt()
    inv = make_invoice(rcpt, item_nm, band, gross)
    try:
        resp = client.submit_sale(inv, idempotency_key=rcpt)
        _track(resp, gross, band)
        log(actor, "SALE (preset)", f"{item_nm} KSh {gross} {note}", resp)
        return resp
    except Exception as e:
        failures.append((rcpt, str(e)))
        log(actor, "SALE FAILED", f"{item_nm} KSh {gross}: {e}")
        return None


def itemized_sale(client, actor, lines, band, buyer=None, note=""):
    """Metered/itemized sale — quantity is the primitive (pump meter reading).

    lines: list of (sku, qty) tuples. Single tax band per receipt.
    """
    rcpt = next_receipt()
    items, gross = [], Decimal("0")
    desc = []
    for sku, qty in lines:
        nm, cls, b, price, unit = CATALOG[sku]
        line_tot = (qty * price).quantize(TWO, ROUND_HALF_UP)
        gross += line_tot
        items.append({
            "sku": sku, "itemNm": nm, "itemClsCd": cls, "taxTyCd": b,
            "qty": str(qty), "unitPrice": str(price), "qtyUnitCd": unit,
        })
        desc.append(f"{nm} x{qty}")
    taxbl, tax = vat_split(gross, band)
    payload = {
        "supplierPin": TIN, "amount": str(gross), "invoiceDate": TODAY,
        "itemDescription": ", ".join(desc)[:200],
        "taxBand": band, "taxAmount": str(tax),
        "items": items,
    }
    if buyer:
        payload["buyerPin"], payload["buyerName"] = buyer
    try:
        resp = client._request("POST", "/v2/etims/sale", json=payload,
                               idempotency_key=rcpt)
        _track(resp, gross, band)
        log(actor, "SALE (metered)", f"{'; '.join(desc)} = KSh {gross} {note}", resp)
        return resp
    except Exception as e:
        failures.append((rcpt, str(e)))
        log(actor, "SALE FAILED", f"{'; '.join(desc)}: {e}")
        return None


def _track(resp, gross, band):
    if resp and resp.get("purchaseId"):
        sold.append((resp["purchaseId"], gross, band))
        taxbl, tax = vat_split(gross, band)
        expected[band]["gross"] += gross
        expected[band]["taxbl"] += taxbl
        expected[band]["tax"]   += tax


def main():
    random.seed()
    client = KRAeTIMSClient("", "", api_key=API_KEY, base_url=BASE_URL)
    print(f"=== Jua Kali Energies — Kikuyu Rd — trading day {TODAY} ===", flush=True)

    # ---- 05:55 POS boot: register the catalog (Category 4, live saveItems) ----
    item_cds = {}
    for sku, (nm, cls, band, price, unit) in CATALOG.items():
        try:
            resp = client.save_item(ItemSave(
                tin=TIN, bhfId=BHF, itemCd=sku, itemClsCd=cls, itemNm=nm,
                itemTyCd=ItemType.SERVICE if sku == "CARWASH" else ItemType.GOODS,
                taxTyCd=TaxType(band), uprc=price, qtyUnitCd=unit,
            ))
            item_cds[sku] = resp.get("itemCd")
            log("POS", "ITEM REG", f"{sku} -> itemCd={resp.get('itemCd')} "
                f"vscuRegistered={resp.get('vscuRegistered')}", None)
        except Exception as e:
            failures.append((f"item:{sku}", str(e)))
            log("POS", "ITEM REG FAIL", f"{sku}: {e}")
        pause(0.5, 1.5)

    # ---- 06:10 tanker delivery: 12,000 L PMS + 8,000 L AGO (Category 8, M) ----
    try:
        if RESUME:
            raise RuntimeError("SKIP")
        resp = client.submit_stock_adjustment(
            lines=[
                StockAdjustmentLine(itemCd=item_cds.get("PMS-SUPER", "PMS-SUPER"),
                                    itemNm=CATALOG["PMS-SUPER"][0], ioType="M",
                                    qty=Decimal("12000"), prc=Decimal("199.20"),
                                    taxTyCd=TaxType.E, qtyUnitCd="LTR"),
                StockAdjustmentLine(itemCd=item_cds.get("AGO-DIESEL", "AGO-DIESEL"),
                                    itemNm=CATALOG["AGO-DIESEL"][0], ioType="M",
                                    qty=Decimal("8000"), prc=Decimal("208.50"),
                                    taxTyCd=TaxType.E, qtyUnitCd="LTR"),
            ],
            remark="KPC Nairobi depot delivery — tanker KBY 442T, waybill W-88231",
        )
        log("SUPERVISOR", "STOCK-IN", "Tanker: 12,000L PMS + 8,000L AGO", resp)
    except Exception as e:
        if str(e) == "SKIP":
            log("SUPERVISOR", "STOCK-IN", "tanker delivery already on ledger (resume)", None)
        else:
            failures.append(("stock-in-fuel", str(e)))
            log("SUPERVISOR", "STOCK-IN FAIL", f"tanker: {e}")
    pause(2, 4)

    # ---- 06:20 shop van delivery: oil, sodas, water, LPG refill stock ----
    try:
        if RESUME:
            raise RuntimeError("SKIP")
        resp = client.submit_stock_adjustment(
            lines=[
                StockAdjustmentLine(itemCd=item_cds.get("OIL-5W40-4L", "OIL-5W40-4L"),
                                    itemNm=CATALOG["OIL-5W40-4L"][0], ioType="M",
                                    qty=Decimal("24"), prc=Decimal("3900.00"),
                                    taxTyCd=TaxType.B),
                StockAdjustmentLine(itemCd=item_cds.get("SODA-500ML", "SODA-500ML"),
                                    itemNm=CATALOG["SODA-500ML"][0], ioType="M",
                                    qty=Decimal("120"), prc=Decimal("55.00"),
                                    taxTyCd=TaxType.B),
                StockAdjustmentLine(itemCd=item_cds.get("LPG-13KG", "LPG-13KG"),
                                    itemNm=CATALOG["LPG-13KG"][0], ioType="M",
                                    qty=Decimal("40"), prc=Decimal("2700.00"),
                                    taxTyCd=TaxType.C),
            ],
            remark="Shop replenishment — Highlands distributors INV-20441",
        )
        log("SUPERVISOR", "STOCK-IN", "Shop van: 24 oil, 120 soda, 40 LPG", resp)
    except Exception as e:
        if str(e) == "SKIP":
            log("SUPERVISOR", "STOCK-IN", "shop van already on ledger (resume)", None)
        else:
            failures.append(("stock-in-shop", str(e)))
            log("SUPERVISOR", "STOCK-IN FAIL", f"shop van: {e}")
    pause(2, 5)

    # ---- 06:30-09:00 morning rush ----
    P = CATALOG
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 1", "E", Decimal("300.00"), "(boda, preset 300)")
    pause(2, 6)
    flat_sale(client, "OTIENO", "Automotive Diesel (AGO) — pump 3", "E", Decimal("2000.00"), "(matatu 14-seater)")
    pause(2, 6)
    itemized_sale(client, "WANJIKU", [("PMS-SUPER", Decimal("32.45"))], "E", note="(sedan fill-up, meter 32.45L)")
    pause(2, 6)
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 2", "E", Decimal("500.00"), "(boda, preset 500)")
    pause(2, 6)
    itemized_sale(client, "OTIENO", [("AGO-DIESEL", Decimal("64.7"))], "E",
                  buyer=(TIN, "Chandarana Foodplus Ltd — fleet"), note="(B2B fleet truck)")
    pause(2, 6)
    flat_sale(client, "OTIENO", "Automotive Diesel (AGO) — pump 4", "E", Decimal("3000.00"), "(lorry, preset 3000)")
    pause(2, 6)
    itemized_sale(client, "SHOP", [("SODA-500ML", Decimal("2")), ("WATER-1L", Decimal("1"))], "B", note="(driver + turnboy)")
    pause(2, 6)
    itemized_sale(client, "SHOP", [("IK-KERO", Decimal("5"))], "E", note="(kerosene jerrican 5L)")
    pause(2, 6)
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 1", "E", Decimal("1000.00"), "(preset 1000)")
    pause(2, 6)
    itemized_sale(client, "SHOP", [("LPG-13KG", Decimal("1"))], "C", note="(gas refill exchange)")
    pause(2, 6)

    # ---- 10:15 the mis-key: 40 units of oil instead of 4L single can ----
    bad = itemized_sale(client, "SHOP", [("OIL-5W40-4L", Decimal("40"))], "B",
                        note="(!! attendant mis-key: 40 cans)")
    pause(3, 6)
    if bad and bad.get("purchaseId"):
        try:
            cn = client.issue_credit_note(
                bad["purchaseId"],
                reason="Attendant keyed quantity 40 instead of 1 (4L can) — till supervisor void, customer re-billed correctly",
            )
            credit_notes.append(cn)
            # reverse the tracked expectation
            gross = Decimal("4650.00") * 40
            taxbl, tax = vat_split(gross, "B")
            expected["B"]["gross"] -= gross
            expected["B"]["taxbl"] -= taxbl
            expected["B"]["tax"]   -= tax
            log("SUPERVISOR", "CREDIT NOTE", f"reversing sale id={bad['purchaseId']} (KSh {gross})", cn)
        except Exception as e:
            failures.append(("credit-note", str(e)))
            log("SUPERVISOR", "CN FAILED", str(e))
    pause(2, 5)
    itemized_sale(client, "SHOP", [("OIL-5W40-4L", Decimal("1"))], "B", note="(correct re-ring)")
    pause(2, 6)

    # ---- 12:30 lunchtime X report (read-only, no reset) ----
    try:
        x = client.reports.get_x_report(TODAY)
        log("SUPERVISOR", "X-REPORT",
            f"receipts={x.invoice_count} gross={x.total_amount} vat={x.total_vat} "
            f"| E: taxbl={x.band_e.taxable_amount} vat={x.band_e.tax_amount} "
            f"| B: taxbl={x.band_b.taxable_amount} vat={x.band_b.tax_amount}", None)
        TRANSCRIPT.append({"x_report": x.model_dump(mode="json")})
    except Exception as e:
        failures.append(("x-report", str(e)))
        log("SUPERVISOR", "X-REPORT FAIL", str(e))
    pause(3, 8)

    # ---- 13:00-17:30 afternoon trade ----
    flat_sale(client, "OTIENO", "Automotive Diesel (AGO) — pump 3", "E", Decimal("1500.00"), "(pickup)")
    pause(4, 9)
    itemized_sale(client, "WANJIKU", [("PMS-SUPER", Decimal("47.62"))], "E", note="(SUV fill-up)")
    pause(4, 9)
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 2", "E", Decimal("200.00"), "(boda, preset 200)")
    pause(4, 9)
    itemized_sale(client, "SHOP", [("CARWASH", Decimal("1"))], "B", note="(car wash while fueling)")
    pause(4, 9)
    itemized_sale(client, "OTIENO", [("AGO-DIESEL", Decimal("120"))], "E",
                  buyer=(TIN, "Kikuyu Rd Sacco — generator account"), note="(B2B bulk drum 120L)")
    pause(4, 9)
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 1", "E", Decimal("700.00"), "(preset 700)")
    pause(4, 9)
    itemized_sale(client, "SHOP", [("SODA-500ML", Decimal("1"))], "B", note="(walk-in)")
    pause(3, 7)
    flat_sale(client, "OTIENO", "Illuminating Kerosene — pump 5", "E", Decimal("400.00"), "(preset 400)")
    pause(3, 7)

    # ---- 17:45 shrinkage write-off: meter calibration loss (A = adjustment out) ----
    try:
        resp = client.submit_stock_adjustment(
            lines=[StockAdjustmentLine(itemCd=item_cds.get("PMS-SUPER", "PMS-SUPER"),
                                       itemNm=CATALOG["PMS-SUPER"][0], ioType="A",
                                       qty=Decimal("18.4"), prc=Decimal("199.20"),
                                       taxTyCd=TaxType.E, qtyUnitCd="LTR")],
            remark="Daily dip-stick variance — evaporation + meter calibration (pump 1/2)",
        )
        log("SUPERVISOR", "STOCK-OUT", "Write-off 18.4L PMS shrinkage", resp)
    except Exception as e:
        failures.append(("stock-out", str(e)))
        log("SUPERVISOR", "STOCK-OUT FAIL", str(e))
    pause(2, 5)

    # ---- 18:05 last boda of the shift, then Z close ----
    flat_sale(client, "WANJIKU", "Super Petrol (PMS) — pump 1", "E", Decimal("250.00"), "(last boda)")
    pause(2, 5)

    try:
        z = client.reports.get_daily_z(TODAY)
        log("SUPERVISOR", "Z-REPORT",
            f"day closed: receipts={z.invoice_count} gross={z.total_amount} vat={z.total_vat} "
            f"| E: taxbl={z.band_e.taxable_amount} vat={z.band_e.tax_amount} "
            f"| B: taxbl={z.band_b.taxable_amount} vat={z.band_b.tax_amount} "
            f"| C: taxbl={z.band_c.taxable_amount}", None)
        TRANSCRIPT.append({"z_report": z.model_dump(mode="json")})
    except Exception as e:
        failures.append(("z-report", str(e)))
        log("SUPERVISOR", "Z-REPORT FAIL", str(e))

    # ---- reconciliation ----
    print("\n=== EXPECTED PER-BAND TOTALS (net of credit note) ===", flush=True)
    for b in "ABCDE":
        if expected[b]["gross"]:
            print(f"  Band {b}: gross={expected[b]['gross']} taxbl={expected[b]['taxbl']} "
                  f"tax={expected[b]['tax']}", flush=True)
    print(f"\nSales signed: {len(sold)}  Credit notes: {len(credit_notes)}  "
          f"Failures: {len(failures)}", flush=True)
    for f in failures:
        print(f"  FAIL {f[0]}: {f[1][:300]}", flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"petrol_day_transcript_{TODAY}.json")
    with open(out, "w") as fh:
        json.dump(TRANSCRIPT, fh, indent=2, default=str)
    print(f"Transcript: {out}", flush=True)
    client.close()


if __name__ == "__main__":
    main()
