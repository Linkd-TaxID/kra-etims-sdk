#!/usr/bin/env python3
"""
Live KRA-sandbox simulation: one compressed trading day at a busy Nairobi
coffee house ("Java House — Adams Arcade"), driven through the taxid-etims
SDK against api.taxid.co.ke -> real VSCU JAR -> KRA sandbox.

Fidelity notes (researched 2026-07-07):
  - Casual-dining chain: dine-in waiter service, takeaway barista counter,
    delivery aggregators (Glovo/Uber Eats rider pickup), corporate orders.
  - Prepared food & beverages = standard-rated 16% -> Band B.
  - Ordinary (gluten) bread is VAT-EXEMPT (VAT Act First Schedule) -> Band A.
    This is the platform's first live Band A receipt.
  - 2% catering training levy (Tourism Fund) is filed separately and does not
    appear on the eTIMS receipt.
  - Menu prices from the 2026 published menu (VAT-inclusive).
Flow: catalog registration -> roastery/bakery delivery stock-in (M) ->
breakfast rush (incl. corporate B2B + pure Band A bread + mixed-band probe +
idempotency replay probe) -> mis-key + LIVE CREDIT NOTE -> X report ->
lunch/aggregator trade -> expired-loaf write-off (A) -> Z attempt (expected
409: the fiscal day was already closed earlier today).

Reconciliation is delta-based: X report is snapshotted before the first sale
and after the last; the per-band deltas must equal this script's independently
tracked expectations to the cent.
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
from kra_etims.exceptions import ZReportAlreadyIssuedError
from kra_etims.models import (
    ItemSave, ItemType, TaxType, SaleInvoice, ItemDetail,
    StockAdjustmentLine,
)

warnings.filterwarnings("ignore", category=UserWarning)

BASE_URL = os.environ.get("TIAAS_URL", "https://api.taxid.co.ke")
API_KEY  = os.environ["SANDBOX_SDK_KEY"]
TIN, BHF = "A008697103A", "00"
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
    print(f"[{ts}] {actor:<9} | {action:<15} | {detail} {('-> ' + str(rcpt)) if rcpt else ''}",
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
# Catalog — SKU -> (name, itemClsCd, band, menu price VAT-incl, item type)
# itemClsCd values verified present in the live KRA classification cache:
#   50200000 Beverages | 90100000 Restaurants and catering
#   50180000 Bread and bakery products
# ---------------------------------------------------------------------------
CATALOG = {
    "CAPP-DBL":   ("Double Cappuccino",            "50200000", "B", Decimal("350.00")),
    "LATTE":      ("Caffe Latte",                  "50200000", "B", Decimal("320.00")),
    "AMERICANO":  ("Americano",                    "50200000", "B", Decimal("280.00")),
    "OJ-LGE":     ("Fresh Orange Juice Large",     "50200000", "B", Decimal("360.00")),
    "SHAKE-VAN":  ("Classic Vanilla Shake",        "50200000", "B", Decimal("470.00")),
    "JAVA-BFAST": ("Full Java Breakfast",          "90100000", "B", Decimal("900.00")),
    "CHIC-BURG":  ("Grilled Chicken Burger",       "90100000", "B", Decimal("820.00")),
    "BEEF-BURG":  ("Classic Beef Burger",          "90100000", "B", Decimal("820.00")),
    "QTR-CHIPS":  ("Quarter Chicken and Chips",    "90100000", "B", Decimal("530.00")),
    "FILLET-STK": ("Grilled Fillet Steak",         "90100000", "B", Decimal("1220.00")),
    "CAKE-BF":    ("Black Forest Cake Slice",      "50180000", "B", Decimal("400.00")),
    "BREAD-ORD":  ("Ordinary Bread Loaf 400g",     "50180000", "A", Decimal("180.00")),
    "BEANS-500":  ("Roastery Coffee Beans 500g",   "50200000", "B", Decimal("1450.00")),
}

pos_counter = 7300          # POS receipt sequence (7100-block consumed by the pre-fix run;
                            # reusing keys would dedup into this morning's receipts)
sold = []                   # (purchaseId, gross, band) for credit-note pick + recon
expected = {b: {"taxbl": Decimal("0"), "tax": Decimal("0"), "gross": Decimal("0")}
            for b in "ABCDE"}
credit_notes = []
failures = []
probe_results = {}


def next_receipt():
    global pos_counter
    pos_counter += 1
    return f"JH-{TODAY.replace('-', '')}-{pos_counter}"


def make_invoice(rcpt_no, item_nm, band, gross, pmt="01"):
    """KRA-native SaleInvoice for the SDK flat path (single line)."""
    taxbl, tax = vat_split(gross, band)
    item = ItemDetail(
        itemCd="POS-LINE", itemNm=item_nm,
        qty=Decimal("1"), uprc=gross,
        splyAmt=gross, totAmt=gross,
        taxTyCd=TaxType(band), taxblAmt=taxbl, taxAmt=tax,
    )
    return SaleInvoice(
        tin=TIN, bhfId=BHF, invcNo=rcpt_no,
        confirmDt=datetime.now().strftime("%Y%m%d%H%M%S"),
        totItemCnt=1, totTaxblAmt=taxbl, totTaxAmt=tax, totAmt=gross,
        itemList=[item], pmtTyCd=pmt,
    )


def flat_sale(client, actor, item_nm, band, gross, note="", pmt="01"):
    """Counter sale rung as a plain amount (SDK submit_sale path)."""
    rcpt = next_receipt()
    inv = make_invoice(rcpt, item_nm, band, gross, pmt=pmt)
    try:
        resp = client.submit_sale(inv, idempotency_key=rcpt)
        _track(resp, gross, band)
        log(actor, "SALE (flat)", f"{item_nm} KSh {gross} {note}", resp)
        return resp, rcpt
    except Exception as e:
        failures.append((rcpt, str(e)))
        log(actor, "SALE FAILED", f"{item_nm} KSh {gross}: {e}")
        return None, rcpt


def itemized_sale(client, actor, lines, band, buyer=None, note="", track=True,
                  idem_key=None, pmt=None):
    """Ticket with registered SKU lines (middleware items[] path).

    lines: list of (sku, qty). `band` is the receipt-level band; per-line
    taxTyCd comes from the catalog (they may differ — see mixed-band probe).
    """
    rcpt = idem_key or next_receipt()
    items, gross, tax_total = [], Decimal("0"), Decimal("0")
    desc = []
    for sku, qty in lines:
        nm, cls, b, price = CATALOG[sku]
        line_tot = (qty * price).quantize(TWO, ROUND_HALF_UP)
        gross += line_tot
        _, line_tax = vat_split(line_tot, b)
        tax_total += line_tax
        items.append({
            "sku": sku, "itemNm": nm, "itemClsCd": cls, "taxTyCd": b,
            "qty": str(qty), "unitPrice": str(price),
        })
        desc.append(f"{nm} x{qty}")
    payload = {
        "supplierPin": TIN, "amount": str(gross), "invoiceDate": TODAY,
        "itemDescription": ", ".join(desc)[:200],
        "taxBand": band, "taxAmount": str(tax_total),
        "items": items,
    }
    if buyer:
        payload["buyerPin"], payload["buyerName"] = buyer
    if pmt:
        payload["pmtTyCd"] = pmt
    try:
        resp = client._request("POST", "/v2/etims/sale", json=payload,
                               idempotency_key=rcpt)
        if track:
            _track(resp, gross, band)
        log(actor, "SALE (ticket)", f"{'; '.join(desc)} = KSh {gross} {note}", resp)
        return resp, rcpt
    except Exception as e:
        failures.append((rcpt, str(e)))
        log(actor, "SALE FAILED", f"{'; '.join(desc)}: {e}")
        return None, rcpt


def _track(resp, gross, band):
    if resp and resp.get("purchaseId"):
        sold.append((resp["purchaseId"], gross, band))
        taxbl, tax = vat_split(gross, band)
        expected[band]["gross"] += gross
        expected[band]["taxbl"] += taxbl
        expected[band]["tax"]   += tax


def snapshot_x(client, label):
    x = client.reports.get_x_report(TODAY)
    TRANSCRIPT.append({f"x_report_{label}": x.model_dump(mode="json")})
    return x


def main():
    random.seed()
    client = KRAeTIMSClient("", "", api_key=API_KEY, base_url=BASE_URL)
    print(f"=== Java House — Adams Arcade — trading day {TODAY} ===", flush=True)

    # ---- 06:30 baseline: X snapshot BEFORE the first ticket ----------------
    try:
        x0 = snapshot_x(client, "baseline")
        log("SYSTEM", "X-BASELINE",
            f"receipts={x0.invoice_count} gross={x0.total_amount} vat={x0.total_vat}")
    except Exception as e:
        failures.append(("x-baseline", str(e)))
        log("SYSTEM", "X-BASELINE FAIL", str(e))
        x0 = None

    # ---- 06:35 POS boot: register the menu (Category 4, live saveItems) ----
    item_cds = {}
    if not RESUME:
        for sku, (nm, cls, band, price) in CATALOG.items():
            try:
                resp = client.save_item(ItemSave(
                    tin=TIN, bhfId=BHF, itemCd=sku, itemClsCd=cls, itemNm=nm,
                    itemTyCd=ItemType.GOODS,
                    taxTyCd=TaxType(band), uprc=price, qtyUnitCd="U",
                ))
                item_cds[sku] = resp.get("itemCd")
                log("POS", "ITEM REG", f"{sku} -> itemCd={resp.get('itemCd')} "
                    f"vscuRegistered={resp.get('vscuRegistered')}")
            except Exception as e:
                failures.append((f"item:{sku}", str(e)))
                log("POS", "ITEM REG FAIL", f"{sku}: {e}")
            pause(0.4, 1.2)

    # ---- 06:50 roastery + bakery delivery (Category 8, M = stock in) -------
    if not RESUME:
        try:
            resp = client.submit_stock_adjustment(
                lines=[
                    StockAdjustmentLine(itemCd=item_cds.get("BEANS-500", "BEANS-500"),
                                        itemNm=CATALOG["BEANS-500"][0], ioType="M",
                                        qty=Decimal("30"), prc=Decimal("1050.00"),
                                        taxTyCd=TaxType.B),
                    StockAdjustmentLine(itemCd=item_cds.get("BREAD-ORD", "BREAD-ORD"),
                                        itemNm=CATALOG["BREAD-ORD"][0], ioType="M",
                                        qty=Decimal("40"), prc=Decimal("115.00"),
                                        taxTyCd=TaxType.A),
                    StockAdjustmentLine(itemCd=item_cds.get("CAKE-BF", "CAKE-BF"),
                                        itemNm=CATALOG["CAKE-BF"][0], ioType="M",
                                        qty=Decimal("16"), prc=Decimal("290.00"),
                                        taxTyCd=TaxType.B),
                ],
                remark="Dagoretti roastery + central bakery van — delivery note DN-20441",
            )
            log("MUTHONI", "STOCK-IN", "30 bean bags, 40 loaves, 16 cake slices", resp)
        except Exception as e:
            failures.append(("stock-in", str(e)))
            log("MUTHONI", "STOCK-IN FAIL", str(e))
        pause(1.5, 3)

    # ---- 07:00-10:00 breakfast rush ----------------------------------------
    first_ticket, _ = itemized_sale(client, "ESTHER",
                  [("JAVA-BFAST", Decimal("2")), ("CAPP-DBL", Decimal("2"))],
                  "B", note="(table 4, couple)", pmt="06")
    if first_ticket:
        probe_results["self_sale_label"] = {
            "type": first_ticket.get("type"),
            "supplier": first_ticket.get("supplier"),
            "fixed": first_ticket.get("type") == "SALE",
        }
        log("SYSTEM", "TYPE CHECK", f"normal sale labelled type={first_ticket.get('type')} "
            f"supplier={first_ticket.get('supplier')!r} "
            f"({'FIXED' if first_ticket.get('type') == 'SALE' else 'STILL MISLABELLED'})")
    pause(1.5, 4)
    flat_sale(client, "BRIAN", "Caffe Latte — takeaway", "B", Decimal("320.00"),
              "(commuter, M-Pesa)", pmt="06")
    pause(1.5, 4)
    itemized_sale(client, "BRIAN", [("AMERICANO", Decimal("1")), ("CAKE-BF", Decimal("1"))],
                  "B", note="(laptop customer)")
    pause(1.5, 4)

    # Corporate B2B — office breakfast run, buyer PIN on the invoice
    itemized_sale(client, "ESTHER", [("JAVA-BFAST", Decimal("10"))], "B",
                  buyer=(TIN, "Acacia Advocates LLP — office account"),
                  note="(corporate order, PIN on invoice)")
    pause(1.5, 4)

    # FIRST LIVE BAND A RECEIPT — exempt ordinary bread, takeaway
    itemized_sale(client, "BRIAN", [("BREAD-ORD", Decimal("2"))], "A",
                  note="(2 loaves takeaway — VAT-EXEMPT Band A)")
    pause(1.5, 4)

    # MIXED-BAND PROBE — one ticket carrying an A line + a B line.
    # Pre-fix this signed and mis-booked the bread under band B (receipt /40).
    # Post-fix (taxID 5b44b38) the middleware must reject it with 400 before
    # anything is persisted or signed.
    probe, probe_key = itemized_sale(client, "BRIAN",
                             [("BREAD-ORD", Decimal("1")), ("CAPP-DBL", Decimal("1"))],
                             "B", note="(!! mixed-band probe: A loaf + B coffee)",
                             track=False)
    if probe and probe.get("purchaseId"):
        probe_results["mixed_band"] = {"rejected": False, "signed": probe}
        sold.append((probe["purchaseId"], Decimal("530.00"), "MIXED"))
        log("SYSTEM", "MIXED PROBE", "!!! mixed-band ticket SIGNED — guard NOT active")
    else:
        err = failures.pop() if failures and failures[-1][0] == probe_key else ("", "")
        # The SDK sanitizes unmapped 4xx to status-code-only messages (no server
        # body), so match on the 400 itself; the server-side detail is
        # "Mixed tax bands are not supported on one receipt" (curl to verify).
        rejected = "400" in err[1]
        probe_results["mixed_band"] = {"rejected": rejected, "error": err[1][:200]}
        log("SYSTEM", "MIXED PROBE", "rejected with 400 — guard active (fix verified)"
            if rejected else f"unexpected failure mode: {err[1][:120]}")
    pause(1.5, 4)

    # IDEMPOTENCY REPLAY PROBE — same key submitted twice; the second response
    # must reference the same purchase, with no second receipt signed.
    first, key = flat_sale(client, "BRIAN", "Caffe Latte — takeaway", "B",
                           Decimal("320.00"), "(idempotency probe, 1st)")
    pause(0.5, 1.5)
    if first:
        try:
            inv = make_invoice(key, "Caffe Latte — takeaway", "B", Decimal("320.00"))
            replay = client.submit_sale(inv, idempotency_key=key)
            same = replay.get("purchaseId") == first.get("purchaseId") and \
                   replay.get("cuInvoiceNumber") == first.get("cuInvoiceNumber")
            probe_results["idempotency"] = {"replayed": replay, "deduplicated": same}
            log("BRIAN", "IDEM REPLAY",
                f"same key -> same receipt: {same}", replay)
        except Exception as e:
            probe_results["idempotency"] = {"error": str(e)}
            log("BRIAN", "IDEM REPLAY", f"raised: {type(e).__name__}: {e}")
    pause(1.5, 4)

    itemized_sale(client, "ESTHER", [("OJ-LGE", Decimal("2")), ("JAVA-BFAST", Decimal("1"))],
                  "B", note="(table 9)")
    pause(1.5, 4)

    # ---- 11:40 the mis-key: 10 fillet steaks instead of 1 ------------------
    bad, _ = itemized_sale(client, "ESTHER", [("FILLET-STK", Decimal("10"))], "B",
                           note="(!! waiter mis-key: qty 10)")
    pause(2, 4)
    if bad and bad.get("purchaseId"):
        try:
            cn = client.issue_credit_note(
                bad["purchaseId"],
                reason="Waiter keyed quantity 10 instead of 1 — supervisor void, "
                       "table re-billed correctly",
            )
            credit_notes.append(cn)
            gross = Decimal("1220.00") * 10
            taxbl, tax = vat_split(gross, "B")
            expected["B"]["gross"] -= gross
            expected["B"]["taxbl"] -= taxbl
            expected["B"]["tax"]   -= tax
            log("MUTHONI", "CREDIT NOTE", f"reversing sale id={bad['purchaseId']} "
                f"(KSh {gross})", cn)
        except Exception as e:
            failures.append(("credit-note", str(e)))
            log("MUTHONI", "CN FAILED", str(e))
    pause(1.5, 3)
    itemized_sale(client, "ESTHER", [("FILLET-STK", Decimal("1"))], "B",
                  note="(correct re-ring)")
    pause(1.5, 4)

    # ---- 12:10 mid-day X report (read-only) --------------------------------
    try:
        x = snapshot_x(client, "midday")
        log("MUTHONI", "X-REPORT",
            f"receipts={x.invoice_count} gross={x.total_amount} vat={x.total_vat} "
            f"| B: taxbl={x.band_b.taxable_amount} vat={x.band_b.tax_amount} "
            f"| A: taxbl={x.band_a.taxable_amount}")
    except Exception as e:
        failures.append(("x-midday", str(e)))
        log("MUTHONI", "X-REPORT FAIL", str(e))
    pause(2, 5)

    # ---- 12:30-15:30 lunch + aggregator trade -------------------------------
    itemized_sale(client, "ESTHER", [("BEEF-BURG", Decimal("1")), ("CHIC-BURG", Decimal("1")),
                                     ("OJ-LGE", Decimal("2"))], "B", note="(table 2)")
    pause(1.5, 4)
    itemized_sale(client, "DELIVERY", [("CHIC-BURG", Decimal("2")), ("SHAKE-VAN", Decimal("2"))],
                  "B", note="(Glovo rider pickup — order GLV-83321)", pmt="05")
    pause(1.5, 4)
    itemized_sale(client, "ESTHER", [("QTR-CHIPS", Decimal("2"))], "B", note="(table 7)")
    pause(1.5, 4)
    itemized_sale(client, "DELIVERY", [("JAVA-BFAST", Decimal("1")), ("LATTE", Decimal("1"))],
                  "B", note="(Uber Eats — order UE-99102)")
    pause(1.5, 4)
    itemized_sale(client, "BRIAN", [("BEANS-500", Decimal("1"))], "B",
                  note="(retail: beans bag to go)")
    pause(1.5, 4)
    itemized_sale(client, "BRIAN", [("BREAD-ORD", Decimal("3"))], "A",
                  note="(3 loaves — school run mum)")
    pause(1.5, 4)

    # ---- 16:00 afternoon coffees --------------------------------------------
    itemized_sale(client, "ESTHER", [("CAKE-BF", Decimal("2")), ("LATTE", Decimal("2"))],
                  "B", note="(table 11, birthday)")
    pause(1.5, 4)
    flat_sale(client, "BRIAN", "Double Cappuccino", "B", Decimal("350.00"), "(walk-in)")
    pause(1.5, 4)

    # ---- 17:30 expired-loaf write-off (Category 8, A = adjustment out) ------
    try:
        resp = client.submit_stock_adjustment(
            lines=[StockAdjustmentLine(itemCd=item_cds.get("BREAD-ORD", "BREAD-ORD"),
                                       itemNm=CATALOG["BREAD-ORD"][0], ioType="A",
                                       qty=Decimal("6"), prc=Decimal("115.00"),
                                       taxTyCd=TaxType.A)],
            remark="End-of-day bakery waste — 6 loaves past sell-by, disposed",
        )
        log("MUTHONI", "STOCK-OUT", "Write-off 6 expired loaves", resp)
    except Exception as e:
        failures.append(("stock-out", str(e)))
        log("MUTHONI", "STOCK-OUT FAIL", str(e))
    pause(1.5, 3)

    # ---- 17:45 last takeaway, then close ------------------------------------
    flat_sale(client, "BRIAN", "Americano — last takeaway", "B", Decimal("280.00"),
              "(closing)")
    pause(1.5, 3)

    # ---- 18:00 Z attempt — the fiscal day was already closed at 10:26 by the
    #      petrol-station shift; this MUST raise ZReportAlreadyIssuedError.
    try:
        z = client.reports.get_daily_z(TODAY)
        probe_results["z_enforcement"] = {"violated": True,
                                          "z": z.model_dump(mode="json")}
        log("MUTHONI", "Z-REPORT", f"!!! second Z ISSUED — enforcement FAILED "
            f"(receipts={z.invoice_count})")
    except ZReportAlreadyIssuedError as e:
        probe_results["z_enforcement"] = {"violated": False, "raised": str(e)}
        log("MUTHONI", "Z-REPORT", "409 ZReportAlreadyIssuedError — single-issuance "
            "enforced (day was closed at 10:26 EAT)")
    except Exception as e:
        failures.append(("z-report", str(e)))
        log("MUTHONI", "Z-REPORT FAIL", f"{type(e).__name__}: {e}")

    # ---- final X snapshot + delta reconciliation -----------------------------
    print("\n=== DELTA RECONCILIATION (X_final - X_baseline vs tracked) ===", flush=True)
    try:
        x1 = snapshot_x(client, "final")
        if x0 is not None:
            bands = {"A": (x0.band_a, x1.band_a), "B": (x0.band_b, x1.band_b),
                     "C": (x0.band_c, x1.band_c), "D": (x0.band_d, x1.band_d),
                     "E": (x0.band_e, x1.band_e)}
            print(f"  receipts delta: {x1.invoice_count - x0.invoice_count} "
                  f"(sales {len(sold)} + CN {len(credit_notes)}; rejected mixed probe "
                  f"must NOT appear)", flush=True)
            all_match = True
            for b, (before, after) in bands.items():
                d_taxbl = after.taxable_amount - before.taxable_amount
                d_tax   = after.tax_amount - before.tax_amount
                if d_taxbl == 0 and d_tax == 0 and expected[b]["gross"] == 0:
                    continue
                ok = (d_taxbl == expected[b]["taxbl"] and d_tax == expected[b]["tax"])
                all_match &= ok
                print(f"  Band {b}: X-delta taxbl={d_taxbl} tax={d_tax} | tracked "
                      f"taxbl={expected[b]['taxbl']} tax={expected[b]['tax']} "
                      f"{'MATCH' if ok else 'MISMATCH'}", flush=True)
            probe_results["delta_reconciliation"] = "to the cent" if all_match else "MISMATCH"
    except Exception as e:
        failures.append(("x-final", str(e)))
        print(f"  final X failed: {e}", flush=True)

    print("\n=== EXPECTED PER-BAND TOTALS (tracked, net of credit note, excl. probe) ===",
          flush=True)
    for b in "ABCDE":
        if expected[b]["gross"]:
            print(f"  Band {b}: gross={expected[b]['gross']} taxbl={expected[b]['taxbl']} "
                  f"tax={expected[b]['tax']}", flush=True)
    print(f"\nSales signed: {len(sold)}  Credit notes: {len(credit_notes)}  "
          f"Failures: {len(failures)}", flush=True)
    for f in failures:
        print(f"  FAIL {f[0]}: {f[1][:300]}", flush=True)
    print(f"Probes: {json.dumps({k: v for k, v in probe_results.items() if k != 'mixed_band'}, default=str)[:500]}",
          flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"javahouse_day_transcript_{TODAY}.json")
    with open(out, "w") as fh:
        json.dump(TRANSCRIPT, fh, indent=2, default=str)
    print(f"Transcript: {out}", flush=True)
    client.close()


if __name__ == "__main__":
    main()
