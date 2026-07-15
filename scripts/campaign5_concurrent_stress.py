#!/usr/bin/env python3
"""
Campaign 5 — concurrent E2E stress test: SDK (AsyncKRAeTIMSClient) -> live
Railway middleware (api.taxid.co.ke) -> real KRA sandbox.

Unlike prior campaigns (Java House / Petrol Station day scripts), every
scenario here runs with genuine overlapping concurrency via asyncio.gather,
not a single sequential actor. Target: find what only shows up under real
concurrent load — idempotency races, credit-note-lock races, rate-limiter
behavior, and Decimal/rounding drift at volume.

Tenant: a single KRA-sandbox-registered device, the same one used by
javahouse_day.py / petrol_station_day.py. There is no second real tenant, so
no cross-tenant isolation scenario runs here (see memory: that dimension was
already covered in campaign 3/4 with the same tenant across time, not
concurrently across tenants).

Usage:
    export SANDBOX_SDK_KEY=...        # tenant-bound SDK key
    export SANDBOX_TIN=A000000000Z    # the sandbox device's KRA PIN
    export SANDBOX_BHF=00             # branch id
    python3 scripts/campaign5_concurrent_stress.py [--phase NAME ...]

Phases: warmup terminals petrol bulk_import b2b_export credit_storm
        idem_race rounding ratelimit close
Default: all, in order.
"""
import asyncio
import csv
import io
import json
import os
import random
import sys
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from kra_etims.async_client import AsyncKRAeTIMSClient
from kra_etims.exceptions import (
    CreditNoteExceedsOriginalError,
    CreditNoteConflictError,
    KRAeTIMSError,
)
from kra_etims.models import ItemSave, ItemType, TaxType

BASE_URL = os.environ.get("TIAAS_URL", "https://api.taxid.co.ke")
API_KEY = os.environ["SANDBOX_SDK_KEY"]
# Never hardcode a real KRA PIN in a public repo — it is identifying data.
TIN = os.environ.get("SANDBOX_TIN", "A000000000Z")
BHF = os.environ.get("SANDBOX_BHF", "00")
TODAY = datetime.now().strftime("%Y-%m-%d")

RATE_LIMIT_PER_MIN = 90  # headroom under the 100/min/key Bucket4J ceiling
TWO = Decimal("0.01")
RATE = {"E": Decimal("0.08"), "B": Decimal("0.16"), "A": Decimal("0"), "C": Decimal("0"), "D": Decimal("0")}

RESULTS = []          # flat log of every attempted request, for the transcript
RESULTS_LOCK = asyncio.Lock()
EXPECTED = {b: {"taxbl": Decimal("0"), "tax": Decimal("0"), "gross": Decimal("0")} for b in "ABCDE"}
EXPECTED_LOCK = asyncio.Lock()


def vat_split(gross: Decimal, band: str):
    rate = RATE[band]
    if rate == 0:
        return gross.quantize(TWO), Decimal("0.00")
    taxable = (gross / (Decimal("1") + rate)).quantize(TWO, ROUND_HALF_UP)
    return taxable, (gross - taxable).quantize(TWO)


class SlidingWindowRateLimiter:
    """Caps requests to `per_minute` in any trailing 60s window, shared across all callers."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._times = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < 60]
                if len(self._times) < self.per_minute:
                    self._times.append(now)
                    return
                wait = 60 - (now - self._times[0]) + 0.01
            await asyncio.sleep(wait)


RATE_LIMITER = SlidingWindowRateLimiter(RATE_LIMIT_PER_MIN)
_seq = 0
_seq_lock = asyncio.Lock()


_RUN_ID = f"{int(time.time()) % 100000:05d}"  # per-process salt — avoids idempotency-key
                                               # collisions with any earlier same-day run


async def next_receipt(prefix="C5"):
    global _seq
    async with _seq_lock:
        _seq += 1
        n = _seq
    return f"{prefix}-{_RUN_ID}-{n:05d}"


async def log(phase, actor, action, detail, resp=None, error=None, latency_ms=None):
    entry = {
        "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "phase": phase, "actor": actor, "action": action, "detail": detail,
        "latency_ms": latency_ms,
    }
    if resp is not None:
        entry["response"] = resp
    if error is not None:
        entry["error"] = f"{type(error).__name__}: {error}"
    async with RESULTS_LOCK:
        RESULTS.append(entry)
    tag = "OK " if error is None else "ERR"
    print(f"[{entry['ts']}] {phase:<12} {tag} {actor:<10} {action:<16} {detail}", flush=True)


async def track_expected(gross, band):
    taxbl, tax = vat_split(gross, band)
    async with EXPECTED_LOCK:
        EXPECTED[band]["gross"] += gross
        EXPECTED[band]["taxbl"] += taxbl
        EXPECTED[band]["tax"] += tax


async def itemized_sale(client, phase, actor, lines, band, buyer=None, note="", paced=True, track=True):
    """lines: list of (sku, itemClsCd, qty, unit_price, line_band). band = receipt-level label."""
    rcpt = await next_receipt()
    items, gross = [], Decimal("0")
    desc = []
    for sku, cls, qty, price, lband in lines:
        line_tot = (qty * price).quantize(TWO, ROUND_HALF_UP)
        gross += line_tot
        items.append({"sku": sku, "itemNm": sku, "itemClsCd": cls, "taxTyCd": lband,
                       "qty": str(qty), "unitPrice": str(price)})
        desc.append(f"{sku} x{qty}")
    payload = {
        "supplierPin": TIN, "amount": str(gross), "invoiceDate": TODAY,
        "itemDescription": ", ".join(desc)[:200], "taxBand": band,
        "items": items,
    }
    if buyer:
        payload["buyerPin"], payload["buyerName"] = buyer
    if paced:
        await RATE_LIMITER.acquire()
    t0 = time.monotonic()
    try:
        resp = await client._request("POST", "/v2/etims/sale", json=payload, idempotency_key=rcpt)
        ms = (time.monotonic() - t0) * 1000
        if track:
            await track_expected(gross, band)
        await log(phase, actor, "SALE", f"{'; '.join(desc)} = KSh {gross} {note}", resp, latency_ms=ms)
        return resp, rcpt
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        await log(phase, actor, "SALE FAILED", f"{'; '.join(desc)} = KSh {gross} {note}",
                   error=e, latency_ms=ms)
        return None, rcpt


# ---------------------------------------------------------------------------
# Catalog (reuses items already registered on this tenant — no re-registration)
# ---------------------------------------------------------------------------
JAVAHOUSE = [
    ("CAPP-DBL", "50200000", "B", Decimal("350.00")),
    ("LATTE", "50200000", "B", Decimal("320.00")),
    ("AMERICANO", "50200000", "B", Decimal("280.00")),
    ("JAVA-BFAST", "90100000", "B", Decimal("900.00")),
    ("CHIC-BURG", "90100000", "B", Decimal("820.00")),
    ("QTR-CHIPS", "90100000", "B", Decimal("530.00")),
    ("CAKE-BF", "50180000", "B", Decimal("400.00")),
    ("BREAD-ORD", "50180000", "A", Decimal("180.00")),
    ("BEANS-500", "50200000", "B", Decimal("1450.00")),
]
PETROL = [
    ("PMS-SUPER", "15100000", "E", Decimal("194.50")),
    ("AGO-DIESEL", "15100000", "E", Decimal("179.20")),
    ("LPG-13KG", "15110000", "C", Decimal("2850.00")),
    ("OIL-5W40-4L", "15120000", "B", Decimal("3200.00")),
]


async def phase_warmup(client):
    """Confirm live before firing load: X-report baseline."""
    x0 = await client.reports.get_x_report(TODAY)
    await log("warmup", "SYSTEM", "X-BASELINE",
               f"receipts={x0.invoice_count} gross={x0.total_amount}")
    return x0


async def terminal_worker(client, terminal_id, n_sales):
    for i in range(n_sales):
        sku, cls, band, price = random.choice(JAVAHOUSE)
        qty = Decimal(random.choice([1, 1, 1, 2, 2, 3]))
        await itemized_sale(client, "terminals", f"POS-{terminal_id}",
                             [(sku, cls, qty, price, band)], band,
                             note=f"(concurrent terminal {terminal_id}, txn {i+1}/{n_sales})")


async def phase_terminals(client, n_terminals=6, sales_per_terminal=5):
    """Concurrent multi-terminal Java House day — real overlap, not sequential."""
    await asyncio.gather(*[
        terminal_worker(client, t, sales_per_terminal) for t in range(1, n_terminals + 1)
    ])


async def pump_worker(client, pump_id, n_sales):
    for i in range(n_sales):
        sku, cls, band, price = random.choice(PETROL[:2])  # fuel pumps only
        litres = Decimal(random.choice([10, 15, 20, 25, 30]))
        await itemized_sale(client, "petrol", f"PUMP-{pump_id}",
                             [(sku, cls, litres, price, band)], band,
                             note=f"(pump {pump_id}, txn {i+1}/{n_sales})")


async def fleet_worker(client, n_sales):
    for i in range(n_sales):
        sku, cls, band, price = PETROL[1]  # diesel fleet fills
        litres = Decimal(random.choice([80, 100, 150]))
        await itemized_sale(client, "petrol", "FLEET",
                             [(sku, cls, litres, price, band)], band,
                             buyer=(TIN, f"Fleet Account #{i+1} — bulk diesel"),
                             note=f"(B2B fleet fill {i+1}/{n_sales})")


async def phase_petrol(client, n_pumps=4, sales_per_pump=5, n_fleet=4):
    """Concurrent pump terminals + concurrent B2B fleet sales, same tenant."""
    await asyncio.gather(
        *[pump_worker(client, p, sales_per_pump) for p in range(1, n_pumps + 1)],
        fleet_worker(client, n_fleet),
    )


async def phase_bulk_import(client, n_rows=220):
    """Bulk CSV import while concurrent sales hit newly-imported SKUs — races
    item-registration against sale submission."""
    bands = ["A", "B", "C", "D", "E"]
    rows = []
    for i in range(n_rows):
        band = bands[i % 5]
        rows.append({
            "sku": f"BULK-{i:04d}", "itemNm": f"Bulk Import Item {i:04d}",
            "itemClsCd": "50200000", "taxTyCd": band, "unitPrice": str(Decimal(100 + i)),
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["sku", "itemNm", "itemClsCd", "taxTyCd", "unitPrice"])
    writer.writeheader()
    writer.writerows(rows)
    csv_path = "/tmp/campaign5_bulk_import.csv"
    with open(csv_path, "w") as fh:
        fh.write(buf.getvalue())

    t0 = time.monotonic()
    try:
        resp = await client.bulk_import_items(csv_path)
        ms = (time.monotonic() - t0) * 1000
        await log("bulk_import", "SYSTEM", "BULK-IMPORT", f"{n_rows} rows", resp, latency_ms=ms)
    except Exception as e:
        await log("bulk_import", "SYSTEM", "BULK-IMPORT FAILED", f"{n_rows} rows", error=e)
        return

    # Immediately fire concurrent sales against freshly-imported SKUs — race
    # the item-registration-to-sale path, not a sequential settle-then-sell.
    sample = random.sample(rows, min(15, len(rows)))

    async def sell_new(row):
        price = Decimal(row["unitPrice"])
        await itemized_sale(client, "bulk_import", "POS-NEW",
                             [(row["sku"], row["itemClsCd"], Decimal("1"), price, row["taxTyCd"])],
                             row["taxTyCd"], note="(sale against just-imported SKU)")

    await asyncio.gather(*[sell_new(r) for r in sample])


NEW_EXPORT_ITEMS = [
    # itemClsCd reused from CATALOG (50200000 "Beverages") — verified present in the
    # live KRA classification cache by javahouse_day.py; raw UN/CEFACT codes guessed
    # off-catalog (09024000/09011100) 400'd on registration (not in the cache).
    ("EXP-TEA-50KG", "50200000", "C", Decimal("18500.00")),
    ("EXP-COFFEE-60KG", "50200000", "C", Decimal("24000.00")),
]


async def phase_b2b_export(client):
    """Register genuine export-labeled Band C items, sell them B2B with buyerPin
    concurrently with ongoing domestic Band A/B traffic."""
    for sku, cls, band, price in NEW_EXPORT_ITEMS:
        try:
            resp = await client.save_item(ItemSave(
                tin=TIN, bhfId=BHF, itemCd=sku, itemClsCd=cls, itemNm=sku.replace("-", " "),
                itemTyCd=ItemType.GOODS, taxTyCd=TaxType(band), uprc=price,
            ))
            await log("b2b_export", "SYSTEM", "ITEM REG", sku, resp)
        except Exception as e:
            await log("b2b_export", "SYSTEM", "ITEM REG FAILED", sku, error=e)

    async def export_sale(i):
        sku, cls, band, price = random.choice(NEW_EXPORT_ITEMS)
        qty = Decimal(random.choice([1, 2, 3]))
        await itemized_sale(client, "b2b_export", "EXPORT-DESK",
                             [(sku, cls, qty, price, band)], band,
                             buyer=(TIN, f"Overseas Buyer Co #{i} — export order"),
                             note=f"(export B2B {i})")

    async def domestic_filler(i):
        sku, cls, band, price = random.choice(JAVAHOUSE)
        await itemized_sale(client, "b2b_export", "POS-DOM",
                             [(sku, cls, Decimal("1"), price, band)], band,
                             note=f"(domestic filler {i}, concurrent w/ export)")

    await asyncio.gather(
        *[export_sale(i) for i in range(1, 6)],
        *[domestic_filler(i) for i in range(1, 6)],
    )


async def phase_credit_storm(client, n_concurrent=6):
    """One signed sale, then N concurrent FULL-reversal credit notes against it.
    Expect exactly one success; the rest must fail clean (409 conflict or 422
    exceeds-original), never a double-accepted over-reversal."""
    resp, rcpt = await itemized_sale(client, "credit_storm", "ESTHER",
                                      [("FILLET-STK", "90100000", Decimal("2"), Decimal("1220.00"), "B")],
                                      "B", note="(target sale for CN storm)", track=False)
    if not resp or not resp.get("purchaseId"):
        await log("credit_storm", "SYSTEM", "ABORT", "target sale did not sign; skipping storm")
        return
    pid = resp["purchaseId"]

    async def try_cn(i):
        t0 = time.monotonic()
        try:
            cn = await client.issue_credit_note(pid, reason=f"CN storm attempt {i}")
            ms = (time.monotonic() - t0) * 1000
            await log("credit_storm", f"CN-{i}", "CREDIT NOTE OK", f"sale id={pid}", cn, latency_ms=ms)
            return True
        except (CreditNoteExceedsOriginalError, CreditNoteConflictError) as e:
            ms = (time.monotonic() - t0) * 1000
            await log("credit_storm", f"CN-{i}", "CREDIT NOTE REJECTED (expected)",
                       f"sale id={pid}", error=e, latency_ms=ms)
            return False
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            await log("credit_storm", f"CN-{i}", "CREDIT NOTE UNEXPECTED ERROR",
                       f"sale id={pid}", error=e, latency_ms=ms)
            return None

    outcomes = await asyncio.gather(*[try_cn(i) for i in range(1, n_concurrent + 1)])
    successes = outcomes.count(True)
    await log("credit_storm", "SYSTEM", "STORM RESULT",
               f"sale id={pid}: {successes}/{n_concurrent} succeeded "
               f"({'PASS — exactly one winner' if successes == 1 else 'FAIL — race condition' if successes != 1 else ''})")
    if successes == 1:
        gross = Decimal("2440.00")
        taxbl, tax = vat_split(gross, "B")
        async with EXPECTED_LOCK:
            EXPECTED["B"]["gross"] += gross - gross  # net zero: full reversal cancels the tracked sale
            EXPECTED["B"]["taxbl"] += Decimal("0")
            EXPECTED["B"]["tax"] += Decimal("0")


async def phase_idem_race(client, n_concurrent=8):
    """N concurrent requests, identical idempotency key + identical body, fired
    at the same instant — true race on the atomic INSERT placeholder, not a
    sequential replay. Expect exactly one signed receipt."""
    key = await next_receipt(prefix="IDEMRACE")
    payload = {
        "supplierPin": TIN, "amount": "350.00", "invoiceDate": TODAY,
        "itemDescription": "Idempotency race probe",
        "taxBand": "B", "items": [{
            "sku": "CAPP-DBL", "itemNm": "CAPP-DBL", "itemClsCd": "50200000",
            "taxTyCd": "B", "qty": "1", "unitPrice": "350.00",
        }],
    }

    async def fire(i):
        t0 = time.monotonic()
        try:
            resp = await client._request("POST", "/v2/etims/sale", json=payload, idempotency_key=key)
            ms = (time.monotonic() - t0) * 1000
            await log("idem_race", f"RACER-{i}", "RACE RESULT", "", resp, latency_ms=ms)
            return resp.get("purchaseId")
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            await log("idem_race", f"RACER-{i}", "RACE ERROR", "", error=e, latency_ms=ms)
            return None

    results = await asyncio.gather(*[fire(i) for i in range(1, n_concurrent + 1)])
    distinct_ids = {r for r in results if r is not None}
    await log("idem_race", "SYSTEM", "RACE SUMMARY",
               f"key={key}: {len(distinct_ids)} distinct purchaseId(s) across "
               f"{n_concurrent} concurrent identical requests "
               f"({'PASS' if len(distinct_ids) <= 1 else 'FAIL — duplicate signed receipts'})")
    if len(distinct_ids) == 1:
        await track_expected(Decimal("350.00"), "B")


ROUNDING_PRICES = [Decimal(x) for x in [
    "99.99", "149.95", "233.33", "17.77", "8.88", "1234.01", "0.99", "300.03",
]]


async def phase_rounding(client, n_concurrent=20):
    async def one(i):
        band = random.choice(["A", "B", "C", "E"])
        price = random.choice(ROUNDING_PRICES)
        qty = Decimal(random.choice([1, 2, 3, 5, 7]))
        await itemized_sale(client, "rounding", f"ROUND-{i}",
                             [(f"ROUND-SKU", "50200000", qty, price, band)], band,
                             note=f"(rounding stress: {qty} x {price} band {band})")

    await asyncio.gather(*[one(i) for i in range(1, n_concurrent + 1)])


async def phase_ratelimit_probe(client, burst=130):
    """Deliberately exceed 100 req/min on this key with a tight, unpaced burst
    against a read-only endpoint. Confirms clean 429s + recovery, not fiscal
    state — uses GET so nothing gets signed."""
    async def one(i):
        t0 = time.monotonic()
        try:
            resp = await client._request("GET", "/v2/etims/items?page=0&size=1")
            ms = (time.monotonic() - t0) * 1000
            return ("ok", ms)
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            return (type(e).__name__, ms)

    results = await asyncio.gather(*[one(i) for i in range(burst)])
    ok = sum(1 for r, _ in results if r == "ok")
    other = {}
    for r, _ in results:
        if r != "ok":
            other[r] = other.get(r, 0) + 1
    await log("ratelimit", "SYSTEM", "BURST RESULT",
               f"{burst} unpaced requests: {ok} ok, breakdown={other}")

    await asyncio.sleep(15)
    t0 = time.monotonic()
    try:
        await client._request("GET", "/v2/etims/items?page=0&size=1")
        await log("ratelimit", "SYSTEM", "RECOVERY CHECK",
                   "single request after burst succeeded (PASS)")
    except Exception as e:
        await log("ratelimit", "SYSTEM", "RECOVERY CHECK FAILED", "", error=e)


async def phase_close(client, x0):
    print("\n=== DELTA RECONCILIATION (X_final - X_baseline vs tracked) ===", flush=True)
    x1 = await client.reports.get_x_report(TODAY)
    bands = {"A": (x0.band_a, x1.band_a), "B": (x0.band_b, x1.band_b),
             "C": (x0.band_c, x1.band_c), "D": (x0.band_d, x1.band_d),
             "E": (x0.band_e, x1.band_e)}
    all_match = True
    for b, (before, after) in bands.items():
        d_taxbl = after.taxable_amount - before.taxable_amount
        d_tax = after.tax_amount - before.tax_amount
        exp = EXPECTED[b]
        if d_taxbl == 0 and d_tax == 0 and exp["gross"] == 0:
            continue
        ok = (d_taxbl == exp["taxbl"] and d_tax == exp["tax"])
        all_match &= ok
        print(f"  Band {b}: X-delta taxbl={d_taxbl} tax={d_tax} | tracked "
              f"taxbl={exp['taxbl']} tax={exp['tax']} {'MATCH' if ok else 'MISMATCH'}", flush=True)
    print(f"  receipts delta: {x1.invoice_count - x0.invoice_count}", flush=True)
    print(f"  Overall reconciliation: {'MATCH TO THE CENT' if all_match else 'MISMATCH — investigate'}",
          flush=True)
    return all_match


PHASES = ["warmup", "terminals", "petrol", "bulk_import", "b2b_export",
          "credit_storm", "idem_race", "rounding", "ratelimit", "close"]


async def main():
    selected = sys.argv[1:] if len(sys.argv) > 1 else PHASES
    client = AsyncKRAeTIMSClient("", "", api_key=API_KEY, base_url=BASE_URL)
    print(f"=== Campaign 5 — concurrent stress — {TODAY} — phases: {selected} ===", flush=True)
    x0 = None
    try:
        if "warmup" in selected:
            x0 = await phase_warmup(client)
        if "terminals" in selected:
            await phase_terminals(client)
        if "petrol" in selected:
            await phase_petrol(client)
        if "bulk_import" in selected:
            await phase_bulk_import(client)
        if "b2b_export" in selected:
            await phase_b2b_export(client)
        if "credit_storm" in selected:
            await phase_credit_storm(client)
        if "idem_race" in selected:
            await phase_idem_race(client)
        if "rounding" in selected:
            await phase_rounding(client)
        if "ratelimit" in selected:
            await phase_ratelimit_probe(client)
        if "close" in selected and x0 is not None:
            await phase_close(client, x0)
    finally:
        errors = [r for r in RESULTS if "error" in r]
        print(f"\nTotal requests logged: {len(RESULTS)}  Errors: {len(errors)}", flush=True)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"campaign5_transcript_{TODAY}.json")
        with open(out, "w") as fh:
            json.dump(RESULTS, fh, indent=2, default=str)
        print(f"Transcript: {out}", flush=True)
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
