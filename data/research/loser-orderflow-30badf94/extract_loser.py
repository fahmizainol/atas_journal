"""Loser-focused order-flow extraction for run 30badf94 (all Long).

Mirror of the winners/big-prints study, anchored where losers live:
  1. TOUCH anchors: first tick at -0.25R / -0.4R / -0.7R underwater -> tape in the
     60s ending at that touch (live-decidable: only uses data up to the touch).
  2. EXIT anchors: pre-exit 60/180s tape (capitulation?) and post-exit 60/300/900s
     (does price v-reverse after our stop? do big buyers print at our stop?).

Conventions from prior study: buy aggressor = 'B', size MUST cast int64,
entry_idx/exit_idx positional into ticks.cached_rth, ts-based windows.
NQ tick = 0.25 pt.
"""
import sys, json, time
sys.path.insert(0, 'src')
import pandas as pd, numpy as np
from journal.sim import ticks

BASE = 'data/sims/vwap-upper-band-bounce/20250201-20260630-v8-30badf94'
TICK = 0.25
SP = '/tmp/claude-1000/-home-afahmi-repos-atas-journal/8cd1ee6c-1406-45bd-a71e-c26cca627300/scratchpad/'

trades = pd.read_parquet(f'{BASE}/trades.parquet').reset_index(drop=True)

def tape_stats(prefix, m, sd, sz, A_sz, B_sz, rec, dur_s):
    """Window tape features incl. big-print composition. dur_s for rate-normalizing."""
    n = int(m.sum())
    vol = int(sz[m].sum()) if n else 0
    rec[f'{prefix}_cvd'] = float(sd[m].sum()) if n else 0.0
    rec[f'{prefix}_vol'] = vol
    rec[f'{prefix}_sellvol'] = int(A_sz[m].sum()) if n else 0
    rec[f'{prefix}_volrate'] = vol / max(dur_s, 1e-9)
    for thr in (10, 20, 50):
        bm = m & (sz >= thr)
        bv = int(sz[bm].sum()) if bm.any() else 0
        rec[f'{prefix}_big{thr}_vol'] = bv
        rec[f'{prefix}_big{thr}_sd'] = float(sd[bm].sum()) if bm.any() else 0.0
        rec[f'{prefix}_big{thr}_part'] = bv / vol if vol else np.nan
        # sell-side big-lot share: big-lot sell volume / all sell volume
        sv = rec[f'{prefix}_sellvol']
        rec[f'{prefix}_big{thr}_sellpart'] = (int(A_sz[bm].sum()) / sv) if sv else np.nan

rows = []
sessions = sorted(trades.session.unique())
t0 = time.time()
for si, sess in enumerate(sessions):
    day = pd.Timestamp(sess).date()
    sym = ticks.contract_for('NQ', day)
    tk = ticks.cached_rth(sym, day)
    if tk is None or len(tk) == 0:
        print('NO TICKS', sess); continue
    ts = tk.ts_utc.values.astype('datetime64[ns]')
    price = tk.price.values.astype('float64')
    sz = tk['size'].values.astype('int64')
    sidev = tk.side.values.astype('U1')
    is_A = sidev == 'A'; is_B = sidev == 'B'
    sd = np.where(is_B, sz, 0).astype('int64') - np.where(is_A, sz, 0).astype('int64')
    A_sz = np.where(is_A, sz, 0).astype('int64')
    B_sz = np.where(is_B, sz, 0).astype('int64')

    sub = trades[trades.session == sess]
    for _, tr in sub.iterrows():
        ei, xi = int(tr.entry_idx), int(tr.exit_idx)
        ei = min(ei, len(tk) - 1); xi = min(xi, len(tk) - 1)
        et, xt = ts[ei], ts[xi]
        entry = float(tr.avg_entry)
        risk = float(tr.avg_entry - tr.stop_price)  # long-only; points
        if risk <= 0: risk = np.nan
        rec = dict(idx=int(tr.name), session=sess, r=float(tr.r_multiple),
                   net=float(tr.net_pnl), exit_reason=tr.exit_reason,
                   mfe_r=float(tr.mfe_r), mae_r=float(tr.mae_r),
                   dur_s=float(tr.duration_s), risk_pts=risk)

        hold = slice(ei, xi + 1)
        hpx = price[hold]
        depth_r = (entry - hpx) / risk  # + = underwater, in R

        # ---- TOUCH anchors ----
        for thr_r, tag in ((0.25, 't25'), (0.40, 't40'), (0.70, 't70')):
            hit = np.nonzero(depth_r >= thr_r)[0]
            if len(hit) == 0:
                rec[f'{tag}_hit'] = 0
                continue
            ti = ei + int(hit[0])
            tt = ts[ti]
            rec[f'{tag}_hit'] = 1
            rec[f'{tag}_secs'] = float((tt - et) / np.timedelta64(1, 's'))
            m = (ts >= tt - np.timedelta64(60, 's')) & (ts <= tt)
            tape_stats(tag, m, sd, sz, A_sz, B_sz, rec, 60)
            # descent speed: R per minute over the last 60s into the touch
            w = np.nonzero(m)[0]
            rec[f'{tag}_dropspeed'] = float((price[w[0]] - price[ti]) / risk) if len(w) else 0.0
            # ABSORPTION into the touch: sell lots per downtick of actual progress.
            # high = sellers hammering but price barely falling (absorbed -> should recover)
            if len(w):
                downticks = max(0.0, (price[w].max() - price[ti]) / TICK)
                rec[f'{tag}_absorp'] = float(A_sz[m].sum()) / (1.0 + downticks)
            else:
                rec[f'{tag}_absorp'] = 0.0
            # SELLER EXHAUSTION into the touch: sell rate last 20s vs prior 40s (<1 = drying up)
            m20 = (ts >= tt - np.timedelta64(20, 's')) & (ts <= tt)
            m40 = (ts >= tt - np.timedelta64(60, 's')) & (ts < tt - np.timedelta64(20, 's'))
            r20 = A_sz[m20].sum() / 20.0
            r40 = A_sz[m40].sum() / 40.0
            rec[f'{tag}_exh_ratio'] = float(r20 / (r40 + 1e-9))
            # CVD DIVERGENCE at the touch (price is at a new hold low by construction):
            # CVD now minus CVD's own minimum so far this hold; >0 = delta higher-low (bullish)
            hsd = sd[ei:ti + 1]
            hcvd = np.cumsum(hsd)
            rec[f'{tag}_cvd_div'] = float(hcvd[-1] - hcvd.min()) if len(hcvd) else 0.0

        # ---- EXIT anchors ----
        for W in (60, 180):
            m = (ts >= xt - np.timedelta64(W, 's')) & (ts <= xt)
            tape_stats(f'x{W}', m, sd, sz, A_sz, B_sz, rec, W)
            w = np.nonzero(m)[0]
            if len(w):
                downticks = max(0.0, (price[w].max() - price[xi]) / TICK)
                rec[f'x{W}_absorp'] = float(A_sz[m].sum()) / (1.0 + downticks)
        # seller exhaustion into the exit print
        m20 = (ts >= xt - np.timedelta64(20, 's')) & (ts <= xt)
        m40 = (ts >= xt - np.timedelta64(60, 's')) & (ts < xt - np.timedelta64(20, 's'))
        rec['x_exh_ratio'] = float((A_sz[m20].sum() / 20.0) / (A_sz[m40].sum() / 40.0 + 1e-9))
        # hold-average volume rate for capitulation ratio
        mh = (ts >= et) & (ts <= xt)
        hold_secs = max(float((xt - et) / np.timedelta64(1, 's')), 1.0)
        rec['hold_volrate'] = int(sz[mh].sum()) / hold_secs
        rec['hold_big10_part'] = (int(sz[mh & (sz >= 10)].sum()) / int(sz[mh].sum())) if sz[mh].sum() else np.nan

        # ---- POST-EXIT ----
        exit_px = float(tr.avg_exit)
        for W in (60, 300, 900):
            m = (ts > xt) & (ts <= xt + np.timedelta64(W, 's'))
            tape_stats(f'p{W}', m, sd, sz, A_sz, B_sz, rec, W)
            if m.any():
                wpx = price[m]
                rec[f'p{W}_maxrec_r'] = float((wpx.max() - exit_px) / risk)   # bounce after exit
                rec[f'p{W}_maxadv_r'] = float((exit_px - wpx.min()) / risk)  # continued fall
                rec[f'p{W}_end_r'] = float((wpx[-1] - exit_px) / risk)
            else:
                rec[f'p{W}_maxrec_r'] = np.nan; rec[f'p{W}_maxadv_r'] = np.nan; rec[f'p{W}_end_r'] = np.nan
        # time to regain entry price after exit (capped 900s)
        m9 = (ts > xt) & (ts <= xt + np.timedelta64(900, 's'))
        w = np.nonzero(m9 & (price >= entry))[0]
        rec['regain_entry_s'] = float((ts[w[0]] - xt) / np.timedelta64(1, 's')) if len(w) else np.nan
        rows.append(rec)
    if si % 40 == 0:
        print(f'{si+1}/{len(sessions)} sessions  {time.time()-t0:.0f}s  rows={len(rows)}', flush=True)

df = pd.DataFrame(rows)
out = SP + 'loser_features.parquet'
df.to_parquet(out)
print('WROTE', out, df.shape, f'{time.time()-t0:.0f}s')
