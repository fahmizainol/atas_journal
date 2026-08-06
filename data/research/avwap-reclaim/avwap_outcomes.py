"""Does the anchored-VWAP reclaim have a forward edge? — vs two matched nulls.

For every cached RTH day, build ONE VWAP anchored causally (no lookahead):
  - anchor 'pdl'   : prior RTH session's LOW, accumulated across the overnight
                     into today's RTH (known at the open; roll-boundary days skip)
  - anchor 'swing' : the session's first CONFIRMED swing low (zigzag), reclaims
                     only counted after the pivot is confirmed

A committed RECLAIM = a 500-tick bar closing back above the line from below,
after holding >= MIN_HOLD bars under it (BUFFER_T-tick dead-band to de-noise).
Enter long at the reclaim close; stop = line - STOP_BUF (floored MIN_RISK); a
2R:1R bracket, stop-first-within-bar (conservative), applied to REAL and NULL
alike. MFE/MAE/to-close reported bracket-free too.

TWO nulls, because a single-window winner proves nothing (cf. LVN candidate #3):
  - CROSS null : raw below->above crosses that are NOT committed reclaims (brief
                 pokes / "drifts"). Isolates whether the loss-and-HOLD matters
                 vs. any cross of the line.
  - RAND  null : random same-session longs, matched count (NDRAW per reclaim),
                 same stop/target. Isolates session drift ("buy any long today").
Real must beat BOTH, and be split-half stable by date, to be worth an engine A/B.

Usage: .venv/bin/python data/research/avwap-reclaim/avwap_outcomes.py [pdl|swing] [start] [end]
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import vwap as vwapmod

TICK = 0.25
TPB = 500
BUFFER_T = 2          # dead-band (ticks): a close must clear the line by this
MIN_HOLD = 3          # bars price must hold below before an up-cross is "committed"
STOP_BUF_T = 2        # stop sits this many ticks under the reclaim bar's low
MIN_RISK_PTS = 10.0   # risk floor
TARGET_MULT = 2.0     # 2R:1R bracket
FWD_BARS = 15         # bounded forward horizon (the trade's actual life, ~15 bars)
NDRAW = 3             # random-null draws per real reclaim
CONFIRM_PTS = 22.0    # zigzag confirmation for the swing anchor
SEED = 20260720
WARMUP = 3            # skip the first few bars (degenerate early VWAP)

ANCHOR = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ('pdl', 'swing') else 'pdl'
START = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2025, 2, 3)
END = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2026, 12, 31)
OUT = f'data/research/avwap-reclaim/avwap_outcomes_{ANCHOR}.parquet'

_rth_cache = {}


def rth_of(sym, day):
    k = (sym, day)
    if k not in _rth_cache:
        _rth_cache[k] = tickmod.cached_rth(sym, day)
    return _rth_cache[k]


def prior_session(sym, day, back=7):
    d = day - timedelta(days=1)
    for _ in range(back):
        if d.weekday() < 5 and tickmod.contract_for_cached('NQ', d) == sym:
            r = rth_of(sym, d)
            if r is not None and not r.empty:
                return d, r
        d -= timedelta(days=1)
    return None, None


def zigzag(hi, lo, confirm):
    n = len(hi); piv = []
    mx_i, mx, mn_i, mn, d = 0, hi[0], 0, lo[0], 0
    for i in range(1, n):
        if hi[i] > mx: mx, mx_i = hi[i], i
        if lo[i] < mn: mn, mn_i = lo[i], i
        if d >= 0 and mx - lo[i] >= confirm:
            piv.append(('H', mx_i, mx, i)); d, mn, mn_i = -1, lo[i], i
        elif d <= 0 and hi[i] - mn >= confirm:
            piv.append(('L', mn_i, mn, i)); d, mx, mx_i = 1, hi[i], i
    return piv


def build_bands(day):
    """Return per-bar arrays + the anchored-VWAP mid (NaN where not yet valid),
    or None if the day can't be anchored for this mode."""
    sym = tickmod.contract_for_cached('NQ', day)
    if not sym:
        return None
    rth = rth_of(sym, day)
    if rth is None or rth.empty:
        return None
    bars = barmod.tick_bars(rth, TPB)
    if len(bars) < 20:
        return None
    bh = bars['high'].to_numpy(); bl = bars['low'].to_numpy(); bc = bars['close'].to_numpy()
    bsi = bars['start_idx'].to_numpy(); bei = bars['end_idx'].to_numpy()
    et = pd.to_datetime(bars['ts_utc'], utc=True).dt.tz_convert('America/New_York')
    tod = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    n = len(bars)
    mid = np.full(n, np.nan)

    if ANCHOR == 'pdl':
        pday, prth = prior_session(sym, day)
        if prth is None:
            return None                       # roll boundary — keep pdl clean
        plow_pos = int(prth['price'].to_numpy().argmin())
        ptail = prth.iloc[plow_pos:]
        post = tickmod.cached_post(sym, pday)  # recovered 16:00-17:00 hour of pday
        post = post if (post is not None and not post.empty) else rth.iloc[:0]
        ov = tickmod.cached_overnight(sym, day)
        ov = ov if (ov is not None and not ov.empty) else rth.iloc[:0]
        frame = pd.concat([ptail, post, ov, rth], ignore_index=True)
        band = vwapmod.vwap_bands(frame)['mid'].to_numpy()
        off = len(ptail) + len(post) + len(ov)
        mid = band[off + bei]
        valid_from = WARMUP
    else:  # swing
        piv = zigzag(bh, bl, CONFIRM_PTS)
        lows = [p for p in piv if p[0] == 'L']
        if not lows:
            return None
        _, pbar, _, known = lows[0]           # first confirmed swing low
        atick = int(bsi[pbar])
        band = vwapmod.vwap_bands(rth.iloc[atick:])['mid'].to_numpy()
        m = len(band)
        for j in range(n):
            pos = int(bei[j]) - atick
            if 0 <= pos < m:
                mid[j] = band[pos]
        valid_from = max(known + 1, WARMUP)

    return dict(sym=sym, bh=bh, bl=bl, bc=bc, tod=tod, mid=mid, n=n, valid_from=valid_from)


def detect(mid, bc, valid_from):
    """(committed_reclaim_bars, raw_crossup_bars) — raw crosses exclude reclaims."""
    buf = BUFFER_T * TICK
    reclaims, crosses = [], []
    below = above = 0
    for j in range(valid_from, len(bc)):
        if not np.isfinite(mid[j]):
            continue
        # raw up-cross (no hold requirement)
        if j > 0 and np.isfinite(mid[j - 1]) and bc[j] > mid[j] and bc[j - 1] <= mid[j - 1]:
            crosses.append(j)
        if bc[j] > mid[j] + buf:
            if below >= MIN_HOLD:
                reclaims.append(j)
            above += 1; below = 0
        elif bc[j] < mid[j] - buf:
            below += 1; above = 0
    rec_set = set(reclaims)
    crosses = [c for c in crosses if c not in rec_set and (c - 1) not in rec_set
               and (c + 1) not in rec_set]
    return reclaims, crosses


def forward(bh, bl, bc, j):
    """Structural stop = reclaim bar's low - STOP_BUF (floored MIN_RISK). Reports
    the 2R:1R bracket outcome AND bracket-free horizons (to close, next FWD_BARS)."""
    entry = bc[j]
    stop = bl[j] - STOP_BUF_T * TICK
    risk = max(entry - stop, MIN_RISK_PTS)
    stop = entry - risk
    tgt = entry + TARGET_MULT * risk
    seg_hi, seg_lo = bh[j:], bl[j:]
    mfe = (seg_hi.max() - entry) / TICK
    mae = (entry - seg_lo.min()) / TICK
    R, hit = None, 0
    for k in range(j, len(bh)):
        if bl[k] <= stop:                     # stop wins ties
            R = -1.0; break
        if bh[k] >= tgt:
            R = TARGET_MULT; hit = 1; break
    if R is None:
        R = (bc[-1] - entry) / risk
    close_t = (bc[-1] - entry) / TICK
    kf = min(j + FWD_BARS, len(bc) - 1)
    fwd_t = (bc[kf] - entry) / TICK
    return mfe, mae, R, hit, close_t, fwd_t


def day_rows(day, rng):
    d = build_bands(day)
    if d is None:
        return []
    bh, bl, bc, mid = d['bh'], d['bl'], d['bc'], d['mid']
    tod, n, vf = d['tod'], d['n'], d['valid_from']
    reclaims, crosses = detect(mid, bc, vf)
    net_t = (bc[-1] - bc[vf]) / TICK if vf < n else 0.0

    def emit(j, kind):
        mfe, mae, R, hit, close_t, fwd_t = forward(bh, bl, bc, j)
        return dict(day=str(day), sym=d['sym'], kind=kind, tod=int(tod[j]),
                    bar=int(j), net_day_t=round(net_t, 1),
                    mfe_t=round(mfe, 1), mae_t=round(mae, 1),
                    R=round(R, 3), hit2R=hit,
                    close_t=round(close_t, 1), fwd_t=round(fwd_t, 1))

    rows = [emit(j, 'reclaim') for j in reclaims]
    rows += [emit(j, 'cross') for j in crosses]
    # random same-session longs, matched count
    pool = [j for j in range(vf, n - 1) if np.isfinite(mid[j])]
    if pool and reclaims:
        picks = rng.choice(pool, size=min(len(pool), NDRAW * len(reclaims)), replace=False)
        rows += [emit(int(j), 'rand') for j in picks]
    return rows


# --- run --------------------------------------------------------------------
rng = np.random.default_rng(SEED)
days = tickmod.session_dates(START, END)
all_rows, done = [], 0
for dd in days:
    try:
        all_rows.extend(day_rows(dd, rng))
    except Exception as e:
        print(f'  ! {dd}: {e}')
    done += 1
    if done % 40 == 0:
        print(f'  ...{done}/{len(days)} days, {len(all_rows)} rows')

df = pd.DataFrame(all_rows)
if df.empty:
    sys.exit('no rows')
df.to_parquet(OUT)


def summ(g, label):
    n = len(g)
    if n == 0:
        print(f'{label:24} n=   0'); return
    print(f'{label:24} n={n:5d}  fwd15_med={g.fwd_t.median():+5.0f}t  %fwd>0={(g.fwd_t > 0).mean():.2f}  '
          f'MFE_med={g.mfe_t.median():5.0f}t  MAE_med={g.mae_t.median():5.0f}t  '
          f'R_mean={g.R.mean():+.3f}  hit2R={g.hit2R.mean():.2f}')


real = df[df.kind == 'reclaim']
cross = df[df.kind == 'cross']
rand = df[df.kind == 'rand']

print(f'\n=== aVWAP reclaim forward outcomes [{ANCHOR}] — {START}..{END} — '
      f'{df.day.nunique()} days ===')
summ(real, 'REAL reclaim')
summ(cross, 'NULL raw cross')
summ(rand, 'NULL random long')
print(f'\nedge vs cross-null:  d_fwd15 = {real.fwd_t.mean() - cross.fwd_t.mean():+6.1f}t   '
      f'dR = {real.R.mean() - cross.R.mean():+.3f}')
print(f'edge vs rand-null:   d_fwd15 = {real.fwd_t.mean() - rand.fwd_t.mean():+6.1f}t   '
      f'dR = {real.R.mean() - rand.R.mean():+.3f}')

print('\n-- split-half by date (REAL − null, on fwd15 ticks) --')
udays = sorted(df.day.unique())
mid_day = udays[len(udays) // 2]
for half, lab in ((df.day < mid_day, f'H1 <{mid_day}'), (df.day >= mid_day, f'H2 >={mid_day}')):
    h = df[half]
    r_ = h[h.kind == 'reclaim']; c_ = h[h.kind == 'cross']; a_ = h[h.kind == 'rand']
    dc = (r_.fwd_t.mean() - c_.fwd_t.mean()) if len(r_) and len(c_) else float('nan')
    da = (r_.fwd_t.mean() - a_.fwd_t.mean()) if len(r_) and len(a_) else float('nan')
    print(f'  {lab:16} n_real={len(r_):4d}  fwd15_real={r_.fwd_t.mean():+6.1f}t  '
          f'd_cross={dc:+6.1f}t  d_rand={da:+6.1f}t')

print('\n-- by time of day (REAL) --')
summ(real[real.tod < 690], '  morning <11:30')
summ(real[real.tod >= 690], '  afternoon >=11:30')
print('-- by day direction (REAL) --')
summ(real[real.net_day_t > 0], '  up days')
summ(real[real.net_day_t <= 0], '  down days')
print(f'\nwrote {OUT}')
