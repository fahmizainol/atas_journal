"""Render worked BOS/CHoCH examples to a self-contained HTML companion for
docs/research/market-structure-events.md (renders in Lab -> Research).

Hand-rolled SVG candles + the causal zigzag with HH/HL/LH/LL labels + BOS/CHoCH
break markers + a per-bar bias strip. Four panels chosen to tell the study's
story visually: the vocabulary working (continuation), the null as whipsaw, and
the SAME CHoCH event preceding a textbook turn AND a textbook trap.
"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "data/research/market-structure")
import numpy as np
import pandas as pd
from datetime import date
from journal.sim.ticks import cached_rth
from journal.sim.regime import minute_bars
from structure_events import structure_events, causal_zigzag

ET = "America/New_York"


def panel(sym, sess, lo, hi, thr, title, note, tone):
    day = date.fromisoformat(sess)
    full = minute_bars(cached_rth(sym, day), "1min").reset_index(drop=True)
    _, ev = structure_events(full, thr_pts=thr)
    piv = causal_zigzag(full["high"].to_numpy(), full["low"].to_numpy(), thr)

    tset = pd.to_datetime(full["ts_utc"]).dt.tz_convert(ET)
    hm = tset.dt.strftime("%H:%M").to_numpy()
    mask = (tset.dt.strftime("%H:%M") >= lo) & (tset.dt.strftime("%H:%M") <= hi)
    idxs = np.where(mask.to_numpy())[0]
    i0, i1 = idxs[0], idxs[-1]
    loc = {int(g): k for k, g in enumerate(range(i0, i1 + 1))}
    B = full.iloc[i0:i1 + 1].reset_index(drop=True)
    o, h, l, c = (B[x].to_numpy() for x in ("open", "high", "low", "close"))

    # geometry
    W, H = 1020, 340
    pl, pr, pt, pb = 6, 52, 26, 22
    iw, ih = W - pl - pr, H - pt - pb
    ymin, ymax = float(l.min()), float(h.max())
    sp = (ymax - ymin) or 1
    ymin -= sp * .06; ymax += sp * .06
    Y = lambda v: pt + ih * (ymax - v) / (ymax - ymin)
    m = len(B); bw = iw / m
    X = lambda i: pl + bw * (i + .5)
    el = []

    # bias strip (per-bar state column, drawn)
    biasmap = {}
    b_state = "na"
    # replay bias per full-session bar from the event stream
    ev_sorted = ev.sort_values("bar")
    ptr = 0
    evrows = ev_sorted.to_dict("records")
    for gi in range(i0, i1 + 1):
        while ptr < len(evrows) and evrows[ptr]["bar"] <= gi:
            b_state = evrows[ptr]["bias_after"]
            ptr += 1
        biasmap[gi] = b_state
    for gi, k in loc.items():
        bs = biasmap[gi]
        col = "var(--up)" if bs == "up" else "var(--dn)" if bs == "down" else "var(--muted)"
        el.append(f'<rect x="{pl + bw*k:.1f}" y="{pt-6:.1f}" width="{bw+0.6:.1f}" '
                  f'height="4" fill="{col}" opacity=".55"/>')

    # grid + price axis
    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4; y = Y(v)
        el.append(f'<line class="gridln" x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}"/>')
        el.append(f'<text class="axistxt" x="{pl+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    for t in [lo, hi] + [f"{hh:02d}:{mm:02d}" for hh in range(9, 16) for mm in (0, 30)]:
        w = np.where(hm[i0:i1+1] == t)[0]
        if len(w):
            el.append(f'<text class="axistxt" x="{X(int(w[0])):.1f}" y="{H-6}" '
                      f'text-anchor="middle">{t}</text>')

    # zigzag polyline + pivot dots/labels
    pl_pts, labels = [], []
    lab_by_bar = {int(r["bar"]): r["label"] for _, r in ev.iterrows()
                  if r["type"].startswith("pivot")}
    for (pidx, price, kind, _conf) in piv:
        if i0 <= pidx <= i1:
            pl_pts.append((loc[pidx], price, kind, lab_by_bar.get(pidx, "")))
    if len(pl_pts) >= 2:
        pts = " ".join(f"{X(k):.1f},{Y(p):.1f}" for k, p, _, _ in pl_pts)
        el.append(f'<polyline class="zzline" points="{pts}"/>')
    for k, p, kind, lab in pl_pts:
        el.append(f'<circle class="zzdot" cx="{X(k):.1f}" cy="{Y(p):.1f}" r="2.4"/>')
        if lab:
            dy = -6 if kind == "H" else 12
            el.append(f'<text class="zztxt" x="{X(k):.1f}" y="{Y(p)+dy:.1f}" '
                      f'text-anchor="middle">{lab}</text>')

    # candles
    cw = max(bw * .62, 1.1)
    for i in range(m):
        cls = "candle-up" if c[i] >= o[i] else "candle-dn"
        xx = X(i)
        el.append(f'<line class="{cls}" x1="{xx:.1f}" y1="{Y(h[i]):.1f}" '
                  f'x2="{xx:.1f}" y2="{Y(l[i]):.1f}" stroke-width="1"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{xx-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')

    # break markers. BOS = faint unlabelled ticks (they show the trend leg);
    # only CHoCH gets a text label (the study's subject), staggered to de-collide.
    brk = ev[ev["type"].str.contains("BOS|CHoCH")]
    choch_n = 0
    for _, e in brk.iterrows():
        gi = int(e["bar"])
        if not (i0 <= gi <= i1):
            continue
        k = loc[gi]
        up = e["type"].endswith("_up")
        col = "var(--up)" if up else "var(--dn)"
        is_choch = e["type"].startswith("CHoCH")
        op = ".9" if is_choch else ".28"
        el.append(f'<line x1="{X(k):.1f}" y1="{pt}" x2="{X(k):.1f}" y2="{pt+ih:.1f}" '
                  f'stroke="{col}" stroke-width="1.2" stroke-dasharray="4 3" opacity="{op}"/>')
        yl = Y(e["level"])
        el.append(f'<line x1="{X(k)-12:.1f}" y1="{yl:.1f}" x2="{X(k)+12:.1f}" y2="{yl:.1f}" '
                  f'stroke="{col}" stroke-width="1.3" opacity="{op}"/>')
        if not is_choch:
            continue
        fwd = e["fwd_net"]
        arrow = "↑" if up else "↓"
        txt = f'CHoCH{arrow} {fwd:+.0f}pt' if pd.notna(fwd) else f'CHoCH{arrow}'
        anch = "start" if k < m * .82 else "end"
        dx = 4 if anch == "start" else -4
        yy = pt + 11 + 13 * (choch_n % 2)   # two-row stagger
        choch_n += 1
        el.append(f'<text x="{X(k)+dx:.1f}" y="{yy:.1f}" font-size="10" font-weight="700" '
                  f'fill="{col}" text-anchor="{anch}">{txt}</text>')

    svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'
    return f'''<div class="panel" style="grid-column:1/-1">
  <div class="phead"><div class="ptitle">{title} <span class="sess">{sym} {sess} · {lo}–{hi} ET · {thr:.0f}pt swings</span></div>
    <span class="chip {tone}">{tone.upper()}</span></div>
  <div class="pstats">{note}</div>{svg}</div>'''


PANELS = [
    dict(sym="NQH5", sess="2025-02-03", lo="09:30", hi="09:46", thr=10,
         title="1 · Reading the labels — the opening up-staircase",
         tone="read",
         note="Purely didactic: confirmed swings step up, each higher high tagged <b>HH</b> over a "
              "higher low <b>HL</b>, and every close above the prior swing high fires a <b>BOS↑</b> "
              "(continuation). The purple line and the four two-letter labels ARE market structure, "
              "as columns. No outcome claim here — panels 2–4 are where the money question lives."),
    dict(sym="NQH5", sess="2025-02-03", lo="14:58", hi="15:20", thr=5,
         title="2 · The null as whipsaw — three character-flips in ~15 minutes",
         tone="loss",
         note="At a 5pt swing threshold the bias flips <b>up→down→up</b> three times, each "
              "<b>CHoCH</b> worth a handful of points forward. Structure is being redefined almost every "
              "bar; “change of character” here is just two-sided rotation. This is what a ~49.5% "
              "win rate looks like on a chart."),
    dict(sym="NQM5", sess="2025-04-09", lo="10:28", hi="11:02", thr=10,
         title="3 · The dream — a CHoCH that marked a real turn",
         tone="win",
         note="An uptrend's higher-low is knifed: <b>CHoCH↓</b>, bias flips down, and price falls "
              "for the next 20 bars almost without a pullback (MAE ~1pt). This is the trade the SMC "
              "vocabulary promises. Keep panel 4 in view before you believe it."),
    dict(sym="NQM5", sess="2025-04-07", lo="09:55", hi="10:24", thr=10,
         title="4 · The trap — the SAME CHoCH signal, inverted outcome",
         tone="loss",
         note="Identical setup two days earlier: a <b>CHoCH↓</b> fires… and price rips the "
              "other way for hundreds of points against the flip. Panels 3 and 4 are the whole study: the "
              "same event precedes the best and the worst outcomes, so pooled it nets ~zero. The label is "
              "real; the edge is not."),
]

blocks = "\n".join(panel(**p) for p in PANELS)
SHELL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BOS / CHoCH — worked structure examples</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;
 --chipwin:#006300;--chiploss:#d03b3b;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;
 --zz:#9085e9;--chipwin:#0ca30c;--chiploss:#e66767;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;
 --chipwin:#0ca30c;--chiploss:#e66767;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;
 --chipwin:#006300;--chiploss:#d03b3b;}
body{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 margin:0;padding:28px 20px 60px;line-height:1.45;}
.wrap{max-width:1060px;margin:0 auto;}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em;}
.sub{color:var(--ink2);font-size:13.5px;max-width:76ch;margin:0 0 14px;}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--ink2);margin:12px 0 18px;}
.legend .k{display:inline-flex;align-items:center;gap:6px;}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block;}
.swline{width:16px;height:0;border-top:2px solid;display:inline-block;}
.panels{display:grid;grid-template-columns:1fr;gap:18px;}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px 8px;}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:3px;}
.ptitle{font-size:14px;font-weight:600;}
.ptitle .sess{color:var(--ink2);font-weight:400;font-size:12px;}
.chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:99px;border:1px solid var(--border);}
.chip.win{color:var(--chipwin);} .chip.loss{color:var(--chiploss);} .chip.read{color:var(--ink2);}
.pstats{font-size:12.5px;color:var(--ink2);margin:2px 0 8px;max-width:96ch;}
.pstats b{color:var(--ink);font-weight:600;}
svg{display:block;width:100%;height:auto;}
svg text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums;}
.gridln{stroke:var(--grid);stroke-width:1;} .axistxt{fill:var(--muted);font-size:10px;}
.candle-up{stroke:var(--up);fill:var(--up);} .candle-dn{stroke:var(--dn);fill:var(--dn);}
.zzline{stroke:var(--zz);stroke-width:1.5;fill:none;opacity:.8;}
.zzdot{fill:var(--zz);} .zztxt{fill:var(--zz);font-size:9.5px;font-weight:700;}
</style></head><body><div class="wrap">
<h1>BOS / CHoCH — market structure as data, on the chart</h1>
<p class="sub">Companion to <b>market-structure-events.md</b>. The purple line is the non-repainting causal
zigzag; dots are confirmed swing pivots labelled <b>HH/HL/LH/LL</b>. Dashed verticals are break events with
the broken swing drawn as a short horizontal tick and the forward-20-bar net beside them. The thin strip
along the top is the <b>bias state</b> per bar (blue = up, red = down). Everything shown is a column in
<code>structure_events.parquet</code> — no pixels were needed to compute it.</p>
<div class="legend">
 <span class="k"><span class="swline" style="border-color:var(--zz)"></span> causal zigzag</span>
 <span class="k"><span class="sw" style="background:var(--zz)"></span> swing pivot (HH/HL/LH/LL)</span>
 <span class="k"><span class="swline" style="border-color:var(--up)"></span> break ↑ / bias up</span>
 <span class="k"><span class="swline" style="border-color:var(--dn)"></span> break ↓ / bias down</span>
 <span class="k"><span class="sw" style="background:var(--up)"></span> up candle</span>
 <span class="k"><span class="sw" style="background:var(--dn)"></span> down candle</span>
</div>
<div class="panels">
__BLOCKS__
</div>
<p class="sub" style="margin-top:22px">Panels 3 and 4 use different sessions but the identical rule; the swing
threshold is set to 10pt for legibility (5pt in panel 2 to show the noise floor). The forward-outcome null holds
at every threshold — see §4 of the write-up.</p>
</div></body></html>'''.replace("__BLOCKS__", blocks)

open("docs/research/market-structure-events-examples.html", "w").write(SHELL)
print("wrote docs/research/market-structure-events-examples.html")
