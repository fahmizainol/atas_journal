"""The four worked BOS/CHoCH examples on the ATR×4 MAJOR tier (~11-17 swings a
session). Full-RTH panels for the trend/whippy overviews, focused half-day
windows for the dream/trap. Companion for market-structure-events.md §7.

Same detector and code as the ATR×2 companion; only MULT and the panel set
change. The forward-null persists on the major tier — a rotational day still
whipsaws major CHoCH near-zero, and the same major CHoCH marks a clean turn on
one ordinary day and a trap on another.
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
MULT = 4.0


def median_atr(b, n=14):
    h, l, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(pd.Series(tr).rolling(n).mean().median())


def panel(sym, sess, lo, hi, title, note, tone):
    day = date.fromisoformat(sess)
    full = minute_bars(cached_rth(sym, day), "1min").reset_index(drop=True)
    atr = median_atr(full)
    thr = round(MULT * atr, 1)
    _, ev = structure_events(full, thr_pts=thr)
    piv = causal_zigzag(full["high"].to_numpy(), full["low"].to_numpy(), thr)

    tset = pd.to_datetime(full["ts_utc"]).dt.tz_convert(ET)
    hm = tset.dt.strftime("%H:%M")
    mask = (hm >= lo) & (hm <= hi)
    idxs = np.where(mask.to_numpy())[0]
    i0, i1 = idxs[0], idxs[-1]
    loc = {int(g): k for k, g in enumerate(range(i0, i1 + 1))}
    B = full.iloc[i0:i1 + 1].reset_index(drop=True)
    o, h, l, c = (B[x].to_numpy() for x in ("open", "high", "low", "close"))
    hmv = hm.to_numpy()

    W, H = 1020, 330
    pl, pr, pt, pb = 6, 52, 26, 22
    iw, ih = W - pl - pr, H - pt - pb
    ymin, ymax = float(l.min()), float(h.max())
    sp = (ymax - ymin) or 1; ymin -= sp * .06; ymax += sp * .06
    Y = lambda v: pt + ih * (ymax - v) / (ymax - ymin)
    m = len(B); bw = iw / m
    X = lambda i: pl + bw * (i + .5)
    el = []

    biasmap = {}; b_state = "na"; ptr = 0
    evrows = ev.sort_values("bar").to_dict("records")
    for gi in range(i0, i1 + 1):
        while ptr < len(evrows) and evrows[ptr]["bar"] <= gi:
            b_state = evrows[ptr]["bias_after"]; ptr += 1
        biasmap[gi] = b_state
    for gi, k in loc.items():
        bs = biasmap[gi]
        col = "var(--up)" if bs == "up" else "var(--dn)" if bs == "down" else "var(--muted)"
        el.append(f'<rect x="{pl + bw*k:.1f}" y="{pt-6:.1f}" width="{bw+0.6:.1f}" '
                  f'height="4" fill="{col}" opacity=".55"/>')

    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4; y = Y(v)
        el.append(f'<line class="gridln" x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}"/>')
        el.append(f'<text class="axistxt" x="{pl+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    for t in [lo, hi] + [f"{hh:02d}:{mm:02d}" for hh in range(9, 16) for mm in (0, 30)]:
        w = np.where(hmv[i0:i1+1] == t)[0]
        if len(w):
            el.append(f'<text class="axistxt" x="{X(int(w[0])):.1f}" y="{H-6}" '
                      f'text-anchor="middle">{t}</text>')

    lab_by_bar = {int(r["bar"]): r["label"] for _, r in ev.iterrows()
                  if r["type"].startswith("pivot")}
    pl_pts = [(loc[pi], p, k, lab_by_bar.get(pi, "")) for (pi, p, k, _c) in piv
              if i0 <= pi <= i1]
    if len(pl_pts) >= 2:
        pts = " ".join(f"{X(k):.1f},{Y(p):.1f}" for k, p, _, _ in pl_pts)
        el.append(f'<polyline class="zzline" points="{pts}"/>')
    for k, p, kind, lab in pl_pts:
        el.append(f'<circle class="zzdot" cx="{X(k):.1f}" cy="{Y(p):.1f}" r="2.6"/>')
        if lab:
            dy = -6 if kind == "H" else 12
            el.append(f'<text class="zztxt" x="{X(k):.1f}" y="{Y(p)+dy:.1f}" '
                      f'text-anchor="middle">{lab}</text>')

    cw = max(bw * .62, 1.0)
    for i in range(m):
        cls = "candle-up" if c[i] >= o[i] else "candle-dn"
        xx = X(i)
        el.append(f'<line class="{cls}" x1="{xx:.1f}" y1="{Y(h[i]):.1f}" x2="{xx:.1f}" '
                  f'y2="{Y(l[i]):.1f}" stroke-width="1"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{xx-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')

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
        yy = pt + 11 + 13 * (choch_n % 2)
        choch_n += 1
        el.append(f'<text x="{X(k)+dx:.1f}" y="{yy:.1f}" font-size="10" font-weight="700" '
                  f'fill="{col}" text-anchor="{anch}">{txt}</text>')

    svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'
    return f'''<div class="panel" style="grid-column:1/-1">
  <div class="phead"><div class="ptitle">{title}
    <span class="sess">{sym} {sess} · {lo}–{hi} ET · ATR14≈{atr:.0f}pt · thr=ATR×4≈{thr:.0f}pt</span></div>
    <span class="chip {tone}">{tone.upper()}</span></div>
  <div class="pstats">{note}</div>{svg}</div>'''


PANELS = [
    dict(sym="NQM5", sess="2025-04-08", lo="09:30", hi="16:00", tone="read",
         title="1 · Reading the labels — a clean trend day, the whole session in a few swings",
         note="On the ×4 major tier an entire RTH session is a handful of pivots. This one is a pure "
              "downtrend: <b>lower highs over lower lows</b>, <b>BOS↓</b> continuation, and <b>zero CHoCH</b> "
              "— the character never changes because the trend never breaks. Market structure as data at "
              "its most legible: four labels tell the day."),
    dict(sym="NQM6", sess="2026-05-15", lo="09:30", hi="16:00", tone="loss",
         title="2 · The null survives the major tier — a rotational day, going nowhere",
         note="Net −47pt on the day, but the major bias flips <b>six times</b> (CHoCH↑↔CHoCH↓), every one "
              "near-zero forward. The major tier strips the minor wiggle, not the null — on a balance day "
              "even the significant swings just rotate around value."),
    dict(sym="NQH6", sess="2026-01-02", lo="09:30", hi="12:15", tone="win",
         title="3 · The dream — a major CHoCH that marked the turn",
         note="Price puts in a morning high, then breaks its major higher-low: <b>CHoCH↓ +204pt</b> "
              "(≈13×ATR, MAE ~5pt) and rolls over for the rest of the morning, a <b>BOS↓</b> extending the "
              "new downtrend. The turn the vocabulary promises, on an ordinary ~15pt-ATR day."),
    dict(sym="NQH6", sess="2026-03-02", lo="09:55", hi="13:00", tone="loss",
         title="4 · The trap — the same major CHoCH, inverted outcome",
         note="Same grammar, opposite result: <b>CHoCH↓ −178pt</b> fires near the low and price rips "
              "straight back up (≈−10×ATR, MAE +181pt), flipping to <b>CHoCH↑</b> minutes later. Going to a "
              "coarser, ‘more significant’ swing didn't help — a break still sits at a spot of maximum "
              "uncertainty at every scale (§4)."),
]

blocks = "\n".join(panel(**p) for p in PANELS)
SHELL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BOS / CHoCH on the ATR×4 major tier</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
 --border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;--chipwin:#006300;--chiploss:#d03b3b;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;
 --chipwin:#0ca30c;--chiploss:#e66767;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;--chipwin:#0ca30c;--chiploss:#e66767;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;--chipwin:#006300;--chiploss:#d03b3b;}
body{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 margin:0;padding:28px 20px 60px;line-height:1.45;}
.wrap{max-width:1060px;margin:0 auto;}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em;} .sub{color:var(--ink2);font-size:13.5px;max-width:80ch;margin:0 0 14px;}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--ink2);margin:12px 0 18px;}
.legend .k{display:inline-flex;align-items:center;gap:6px;}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block;} .swline{width:16px;height:0;border-top:2px solid;display:inline-block;}
.panels{display:grid;grid-template-columns:1fr;gap:18px;}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px 8px;}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:3px;}
.ptitle{font-size:14px;font-weight:600;} .ptitle .sess{color:var(--ink2);font-weight:400;font-size:12px;}
.chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:99px;border:1px solid var(--border);}
.chip.win{color:var(--chipwin);} .chip.loss{color:var(--chiploss);} .chip.read{color:var(--ink2);}
.pstats{font-size:12.5px;color:var(--ink2);margin:2px 0 8px;max-width:96ch;} .pstats b{color:var(--ink);font-weight:600;}
svg{display:block;width:100%;height:auto;} svg text{font-family:system-ui,sans-serif;font-variant-numeric:tabular-nums;}
.gridln{stroke:var(--grid);stroke-width:1;} .axistxt{fill:var(--muted);font-size:10px;}
.candle-up{stroke:var(--up);fill:var(--up);} .candle-dn{stroke:var(--dn);fill:var(--dn);}
.zzline{stroke:var(--zz);stroke-width:1.6;fill:none;opacity:.82;} .zzdot{fill:var(--zz);} .zztxt{fill:var(--zz);font-size:9.5px;font-weight:700;}
</style></head><body><div class="wrap">
<h1>BOS / CHoCH on the ATR×4 major tier</h1>
<p class="sub">The four worked examples on the <b>major</b> swing tier — <code>threshold = 4 × median ATR14</code>,
≈11–17 swings a session. Panels 1–2 span the full RTH day; 3–4 zoom to the marquee event. Purple = causal
zigzag; dots = confirmed pivots <b>HH/HL/LH/LL</b>; dashed verticals = breaks (CHoCH labelled, forward-20-bar
net); top strip = per-bar <b>bias</b>. Coarser and more significant than ATR×2 — but §4's null still holds.</p>
<div class="legend">
 <span class="k"><span class="swline" style="border-color:var(--zz)"></span> causal zigzag (ATR×4)</span>
 <span class="k"><span class="sw" style="background:var(--zz)"></span> swing pivot</span>
 <span class="k"><span class="swline" style="border-color:var(--up)"></span> break ↑ / bias up</span>
 <span class="k"><span class="swline" style="border-color:var(--dn)"></span> break ↓ / bias down</span>
</div>
<div class="panels">
__BLOCKS__
</div></div></body></html>'''.replace("__BLOCKS__", blocks)
open("docs/research/market-structure-events-examples-atr4.html", "w").write(SHELL)
print("wrote docs/research/market-structure-events-examples-atr4.html")
