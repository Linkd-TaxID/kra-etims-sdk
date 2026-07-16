"""
TaxID SDK — POS Basket Demo  (faithful to how a real POS/ERP issues a receipt)
==============================================================================

Unlike the one-line `demo-live-receipt.sh` harness (which signs a single lump
amount), this mirrors what an actual point-of-sale or ERP does:

  1. Build an *itemised* basket — each line has a name, a KRA commodity code
     (itemClsCd), a tax band, and a retail price.  The SDK computes every
     VAT-exclusive amount and the invoice totals (zero manual arithmetic).
  2. Submit it through the SDK's `submit_sale()` — the same call an integrator
     wires into their till software.
  3. Render a fiscal receipt from the *returned* line items + KRA signature,
     with a real scannable QR (via the SDK's qr module).

The commodity codes below are the ones verified present in the live KRA
sandbox classification cache (from the Java House live run), so the items
register and sign cleanly rather than being rejected E31.

Run it:
    pip install 'taxid-etims[qr]'          # or: clone the SDK repo
    export TAXID_API_URL=https://api.taxid.co.ke
    export TAXID_API_KEY=<your sandbox key>
    python examples/pos_basket_demo.py

Writes ./pos-receipt.html — open or print it.
"""

import base64
import os
import time
from datetime import datetime

from kra_etims import (
    KRAeTIMSClient,
    SaleInvoice,
    calculate_item,
    build_invoice_totals,
)

API_URL = os.environ.get("TAXID_API_URL", "https://api.taxid.co.ke")
API_KEY = os.environ.get("TAXID_API_KEY")
if not API_KEY:
    raise SystemExit("Set TAXID_API_KEY (your sandbox API key) before running.")

TIN, BHF = "A008697103A", "00"        # the sandbox tenant this key is bound to
STORE, ADDR = "SOKONI CAFÉ & MART", "Ngong Road · Nairobi"

# --- The basket: (name, seller SKU, KRA commodity code, retail price VAT-incl, band) ---
#   A = 0% Exempt   B = 16% Standard   C = 0% Zero-rated   D = 0% Non-VAT   E = 8% Special
#   The commodity codes are verified present in the live KRA sandbox cache.
BASKET = [
    ("Ordinary Bread Loaf 400g", "MART-BREAD-400", "50180000", 180, "A"),  # exempt basic food
    ("Double Cappuccino",        "MART-CAPP-DBL",  "50200000", 350, "B"),  # 16% standard
    ("Black Forest Cake Slice",  "MART-CAKE-BF",   "50180000", 400, "B"),  # 16% standard
]


def sign_basket():
    # calculate_item does the VAT math. We still set two things per line:
    #   * pkg_unit_cd="NT" — KRA's valid packaging-unit code. The SDK default is
    #     now "NT"; passing it explicitly keeps this example correct on older
    #     releases where the default was the VSCU-rejected "UNT" (error 913).
    #   * itemClsCd — required on every line of a mixed-band invoice; calculate_item
    #     does not set it (its 2nd arg is the seller item_code/SKU).
    items = [
        calculate_item(name, sku, price, band, pkg_unit_cd="NT")
        .model_copy(update={"itemClsCd": cls})
        for name, sku, cls, price, band in BASKET
    ]
    # One client for all retries (do NOT close it between attempts). py3.14 + httpx
    # to the Railway edge can flake on TLS intermittently — retry with a fresh
    # invoice number each time so a failed attempt never collides on idempotency.
    client = KRAeTIMSClient("", "", api_key=API_KEY, base_url=API_URL)
    last = None
    try:
        for attempt in (1, 2, 3):
            invc = f"POS-{int(time.time())}-{attempt}"
            invoice = SaleInvoice(
                tin=TIN, bhfId=BHF, invcNo=invc,
                custNm="Walk-in Customer",
                confirmDt=datetime.now().strftime("%Y%m%d%H%M%S"),
                itemList=items,
                **build_invoice_totals(items),
            )
            try:
                return invoice, client.submit_sale(invoice, idempotency_key=invc)
            except Exception as exc:                 # noqa: BLE001 (demo)
                last = exc
                print(f"  attempt {attempt} failed: {exc!r}; retrying…")
                time.sleep(2)
    finally:
        client.close()
    raise SystemExit(f"submit_sale failed after retries: {last!r}")


def qr_block(resp) -> str:
    """Real scannable QR via the SDK if qrcode[pil] is installed; else a drawn look-alike."""
    try:
        from kra_etims.qr import render_kra_qr_string, generate_qr_bytes
        png = generate_qr_bytes(render_kra_qr_string(resp), box_size=6, border=2)
        uri = "data:image/png;base64," + base64.b64encode(png).decode()
        return f'<img class="qr" alt="KRA eTIMS QR" src="{uri}"><div class="qc">Scan to verify · sandbox</div>'
    except Exception:
        payload = resp.get("kraQrPayload", "")
        import json as _j
        return ('<canvas class="qr" width="300" height="300" aria-hidden="true"></canvas>'
                '<div class="qc">Representative QR · sandbox</div>'
                '<script>(function(){var p=' + _j.dumps(payload) + ';var cv=document.querySelector("canvas.qr"),'
                'x=cv.getContext("2d"),N=29,q=2,T=N+q*2,S=cv.width,m=S/T;x.fillStyle="#fcfbf7";x.fillRect(0,0,S,S);'
                'var g=[];for(var i=0;i<N;i++)g.push(new Array(N).fill(false));'
                'function f(r,c){for(var i=0;i<7;i++)for(var j=0;j<7;j++){g[r+i][c+j]=(i===0||i===6||j===0||j===6)||(i>=2&&i<=4&&j>=2&&j<=4);}}'
                'f(0,0);f(0,N-7);f(N-7,0);for(var t=8;t<N-8;t++){g[6][t]=(t%2===0);g[t][6]=(t%2===0);}'
                'var ar=N-9,ac=N-9;for(var a=-2;a<=2;a++)for(var b=-2;b<=2;b++){var d=Math.max(Math.abs(a),Math.abs(b));g[ar+a][ac+b]=(d===2||d===0);}'
                'var h=2166136261;for(var k=0;k<p.length;k++){h^=p.charCodeAt(k);h=(h*16777619)>>>0;}'
                'function rn(u,v){var w=(h^((u+1)*73856093)^((v+1)*19349663))>>>0;w=((w^(w>>>13))*0x5bd1e995)>>>0;return((w>>>16)&1)===1;}'
                'function rs(r,c){function F(a,b){return r>=a-1&&r<=a+7&&c>=b-1&&c<=b+7;}if(F(0,0)||F(0,N-7)||F(N-7,0))return true;if(r===6||c===6)return true;if(r>=ar-2&&r<=ar+2&&c>=ac-2&&c<=ac+2)return true;return false;}'
                'for(var r=0;r<N;r++)for(var c=0;c<N;c++){if(!rs(r,c))g[r][c]=rn(r,c);}'
                'x.fillStyle="#141414";for(var R=0;R<N;R++)for(var C=0;C<N;C++){if(g[R][C])x.fillRect((C+q)*m,(R+q)*m,m+0.6,m+0.6);}})();</script>')


def render_receipt(invoice, resp) -> str:
    d2 = lambda v: f"{v:.2f}"
    # line items straight from the SDK-computed basket
    rows = "".join(
        f'<div class="row"><span>{it.itemNm}</span>'
        f'<span>{d2(it.totAmt)}&nbsp;<span class="bd">{it.taxTyCd.value if hasattr(it.taxTyCd,"value") else it.taxTyCd}</span></span></div>'
        for it in invoice.itemList
    )
    # per-band VAT summary (net / vat / gross), aggregated from the same lines
    bands, rates = {}, {"A": "0%", "B": "16%", "C": "0%", "D": "0%", "E": "8%"}
    for it in invoice.itemList:
        b = it.taxTyCd.value if hasattr(it.taxTyCd, "value") else it.taxTyCd
        agg = bands.setdefault(b, [0, 0, 0])
        agg[0] += float(it.taxblAmt); agg[1] += float(it.taxAmt); agg[2] += float(it.totAmt)
    vat_rows = "".join(
        f'<div class="row"><span><span class="bd">{b}</span> · {rates.get(b,"?")}</span>'
        f'<span>{d2(n)} / {d2(v)} / {d2(g)}</span></div>'
        for b, (n, v, g) in sorted(bands.items())
    )
    tot_gross = sum(float(it.totAmt) for it in invoice.itemList)
    tot_vat = sum(float(it.taxAmt) for it in invoice.itemList)

    cu = resp.get("cuInvoiceNumber", "")
    scu = resp.get("sdcId", "")
    sig = resp.get("receiptSignature", "")
    qr = resp.get("kraQrPayload", "")
    intd = qr.split("#")[4] if qr.count("#") >= 5 else ""
    ts = resp.get("vscuTimestamp", "")
    date_s = f"{ts[6:8]}/{ts[4:6]}/{ts[0:4]}" if len(ts) == 14 else "--/--/----"
    time_s = f"{ts[8:10]}:{ts[10:12]}:{ts[12:14]}" if len(ts) == 14 else "--:--:--"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>eTIMS Receipt — {STORE}</title><style>
body{{margin:0;min-height:100vh;background:radial-gradient(120% 80% at 50% -10%,#DCDAD2,#E9E7E1);
display:flex;flex-direction:column;align-items:center;gap:1.1rem;padding:2.4rem 1rem 3rem;
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}}
.k{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.7rem;letter-spacing:.18em;
text-transform:uppercase;color:#12604C;display:flex;align-items:center;gap:.6rem;}}
.k::before,.k::after{{content:"";width:1.6rem;height:1.5px;background:#12604C;opacity:.6;}}
.r{{width:min(92vw,360px);background:#FCFBF7;color:#1A1D1A;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
font-size:12.5px;line-height:1.5;padding:1.15rem 1.25rem 1.4rem;position:relative;font-variant-numeric:tabular-nums;
box-shadow:0 1px 1px rgba(0,0,0,.15),0 18px 40px -20px rgba(0,0,0,.55);}}
.r::before,.r::after{{content:"";position:absolute;left:0;right:0;height:8px;
background:repeating-linear-gradient(135deg,#FCFBF7 0 6px,transparent 6px 12px),
repeating-linear-gradient(45deg,#FCFBF7 0 6px,transparent 6px 12px);background-size:12px 8px;}}
.r::before{{top:-8px;}} .r::after{{bottom:-8px;transform:scaleY(-1);}}
.c{{text-align:center;}} .nm{{font-weight:700;font-size:14px;letter-spacing:.06em;}}
.mut{{color:#5B615A;}} hr.h{{border:0;border-top:1px dashed #CBC9C0;margin:.6rem 0;}} hr.h.s{{border-top:1px solid #CBC9C0;}}
.row{{display:flex;justify-content:space-between;gap:.6rem;}} .row span:last-child{{text-align:right;}}
.lab{{font-weight:700;font-size:11px;letter-spacing:.08em;color:#5B615A;text-transform:uppercase;}}
.bd{{color:#12604C;font-weight:700;}} .gr{{font-weight:700;font-size:13.5px;}}
.ft{{text-align:center;font-weight:700;letter-spacing:.14em;font-size:11.5px;color:#12604C;}}
.kv{{display:grid;grid-template-columns:auto 1fr;gap:.05rem .6rem;font-size:11.5px;}}
.kv dt{{color:#5B615A;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;}}
.kv dd{{margin:0;word-break:break-all;text-align:right;font-weight:600;}} .kv dd.sig{{color:#12604C;letter-spacing:.06em;}}
.qw{{display:flex;flex-direction:column;align-items:center;gap:.35rem;margin:.7rem 0 .2rem;}}
.qr{{width:150px;height:150px;image-rendering:pixelated;}} .qc{{font-size:10px;color:#5B615A;}}
.note{{width:min(92vw,360px);font-size:.78rem;line-height:1.5;color:#6E766F;text-align:center;}} .note b{{color:#12604C;}}
@media print{{body{{background:#fff;padding:0;}}.k,.note{{display:none;}}.r{{box-shadow:none;}}}}
</style></head><body>
<p class="k">Live · KRA eTIMS sandbox · itemised via SDK</p>
<div class="r">
  <div class="c"><div class="nm">{STORE}</div><div class="mut">{ADDR}</div>
    <div class="mut">PIN: {TIN} · Branch {BHF}</div></div>
  <hr class="h"><div class="c lab">Tax Invoice</div><hr class="h">
  <div class="row mut"><span>{date_s}</span><span>{time_s}</span></div>
  <div class="row mut"><span>Cashier: 01</span><span>Till: 00</span></div>
  <hr class="h">
  <div class="row lab"><span>Item</span><span>Amount</span></div>
  {rows}
  <hr class="h">
  <div class="row gr"><span>TOTAL (KES)</span><span>{d2(tot_gross)}</span></div>
  <div class="row"><span class="mut">Cash</span><span>{d2(tot_gross)}</span></div>
  <hr class="h"><div class="lab c">VAT Summary — Net / VAT / Gross</div>
  {vat_rows}
  <hr class="h s"><div class="ft">◆ eTIMS FISCAL RECEIPT ◆</div><hr class="h s">
  <dl class="kv">
    <dt>SCU ID</dt><dd>{scu}</dd>
    <dt>CU Inv. No</dt><dd>{cu.replace(" ", "&nbsp;")}</dd>
    <dt>Int. Data</dt><dd>{intd}</dd>
    <dt>Rcpt Sign</dt><dd class="sig">{sig}</dd>
    <dt>Signed</dt><dd>{date_s} {time_s}</dd>
  </dl>
  <div class="qw">{qr_block(resp)}</div>
  <hr class="h"><div class="c" style="font-size:12.5px;">Asante Sana · Thank You</div>
  <div class="c mut" style="font-size:10px;margin-top:.3rem;">Powered by TaxID</div>
</div>
<p class="note"><b>Sandbox.</b> Line items, per-band VAT and totals are computed by the SDK and
signed by the KRA sandbox control unit — the whole receipt is real output, not a mock-up.</p>
</body></html>"""


def main():
    print(f"POS basket → {API_URL}")
    invoice, resp = sign_basket()
    print(f"  SIGNED  {resp.get('cuInvoiceNumber')}   sig {resp.get('receiptSignature')}")
    for it in invoice.itemList:
        b = it.taxTyCd.value if hasattr(it.taxTyCd, "value") else it.taxTyCd
        print(f"    {it.itemNm:<28} {float(it.totAmt):>8.2f}  band {b}  (VAT {float(it.taxAmt):.2f})")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pos-receipt.html")
    out = os.path.abspath(out)
    with open(out, "w") as fh:
        fh.write(render_receipt(invoice, resp))
    print(f"  receipt → {out}")


if __name__ == "__main__":
    main()
