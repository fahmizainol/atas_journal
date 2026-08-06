"""Supplemental VP + S/R context for a drift-touch-fade run (globex-POC study).

Per trade, at entry time, all causal (levels via profile.levels_in_force — the
same reading the engine trades on; prior-day refs are finished sessions):

  Value-area geometry of the developing Globex profile:
    gx_va_width_t, entry_vs_vah_t / entry_vs_val_t (signed, + = entry above),
    poc_in_va (0=at VAL, 1=at VAH), poc_in_onrange, va_cover_onrange
  Level stability: poc_drift_30m_t / poc_drift_60m_t (abs POC relocation),
    poc_age_min (minutes since POC last moved > 2 ticks)
  S/R confluence (signed dist entry -> ref, in ticks, + = entry above ref):
    ONH, ONL, RTH open, prior-day RTH high/low/close and finished POC/VAH/VAL,
    plus nearest_ref_t = min abs distance over the non-traded refs (is the POC
    touch also a level cluster?)
  Gap context: rth open vs prior close (gap_open_t), entry vs gap fill.

Anchors by TIMESTAMP into the combined ON+RTH array (see the INDEX-BASE
WARNING in extract_structure_drift.py).

Usage: .venv/bin/python extract_vp_sr_drift.py <strategy> <run_dir> <tag>
"""
import sys, time
from datetime import timedelta
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod

STRAT, RUN, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
BASE = f'data/sims/{STRAT}/{RUN}'
OUT = f'data/research/market-structure/vpsr_{TAG}.parquet'
TICK = 0.25
TPB = 500

trades = pd.read_parquet(f'{BASE}/trades.parquet').reset_index(drop=True)


def prev_session_refs(day):
    """Prior cached RTH session: finished profile + close + high/low."""
    prev = day
    for _ in range(7):
        prev = prev - timedelta(days=1)
        if prev.weekday() >= 5:
            continue
        sym = tickmod.contract_for_cached('NQ', prev)
        if sym is None:
            continue
        rth = tickmod.cached_rth(sym, prev)
        if rth is None or rth.empty:
            continue
        pb = barmod.tick_bars(rth, TPB)
        if pb.empty:
            return {}
        prof = profmod.developing_profile(rth, pb, TICK)
        poc = next((v for v in prof.poc[::-1] if v == v), np.nan)
        vah = next((v for v in prof.vah[::-1] if v == v), np.nan)
        val = next((v for v in prof.val[::-1] if v == v), np.nan)
        return {'pdPOC': poc, 'pdVAH': vah, 'pdVAL': val,
                'pdClose': float(rth['price'].iloc[-1]),
                'pdHigh': float(rth['price'].max()),
                'pdLow': float(rth['price'].min())}
    return {}


rows = []
t0 = time.time()
sessions = sorted(trades.session.unique())
for si, sess in enumerate(sessions):
    day = pd.Timestamp(sess).date()
    sym = tickmod.contract_for('NQ', day)
    t = tickmod.get_day_ticks(sym, day, include_overnight=True)
    if t is None or t.empty:
        print('NO TICKS', sess)
        continue
    b = barmod.tick_bars(t, TPB)
    n = len(t)
    ts = t['ts_utc'].values.astype('datetime64[ns]')
    px = t['price'].to_numpy(dtype='float64')
    rth_i0 = int(t['ts_utc'].searchsorted(tickmod.session_bounds_utc(day)[0], side='left'))
    on_hi = float(px[:rth_i0].max()) if rth_i0 > 0 else np.nan
    on_lo = float(px[:rth_i0].min()) if rth_i0 > 0 else np.nan
    rth_open = float(px[rth_i0]) if rth_i0 < n else np.nan

    prof = profmod.developing_profile(t, b, TICK)
    poc = profmod.levels_in_force(prof, b, n, edge='poc')
    vah = profmod.levels_in_force(prof, b, n, edge='vah')
    val = profmod.levels_in_force(prof, b, n, edge='val')

    refs = prev_session_refs(day)
    sub = trades[trades.session == sess]
    for _, tr in sub.iterrows():
        d = 1.0 if tr.direction == 'Long' else -1.0
        et = pd.Timestamp(tr.entry_ts_utc).tz_localize(None).to_datetime64()
        ei = min(max(int(np.searchsorted(ts, et, side='right')) - 1, 0), n - 1)
        entry = float(tr.avg_entry)
        p_poc, p_vah, p_val = float(poc[ei]), float(vah[ei]), float(val[ei])
        rec = dict(idx=int(tr.name), session=sess, direction=tr.direction,
                   r=float(tr.r_multiple), net=float(tr.net_pnl),
                   exit_reason=tr.exit_reason,
                   entry_hm=pd.Timestamp(tr.entry_ts_utc).tz_convert(
                       'America/New_York').strftime('%H:%M'))
        vaw = (p_vah - p_val) / TICK if np.isfinite(p_vah) and np.isfinite(p_val) else np.nan
        rec['gx_va_width_t'] = vaw
        rec['entry_vs_vah_t'] = (entry - p_vah) / TICK
        rec['entry_vs_val_t'] = (entry - p_val) / TICK
        rec['entry_vs_poc_t'] = (entry - p_poc) / TICK
        rec['poc_in_va'] = ((p_poc - p_val) / (p_vah - p_val)
                            if vaw and vaw > 0 else np.nan)
        if np.isfinite(on_hi) and on_hi > on_lo:
            rec['poc_in_onrange'] = (p_poc - on_lo) / (on_hi - on_lo)
            rec['va_cover_onrange'] = (p_vah - p_val) / (on_hi - on_lo)
            rec['entry_in_onrange'] = (entry - on_lo) / (on_hi - on_lo)
        else:
            rec['poc_in_onrange'] = rec['va_cover_onrange'] = rec['entry_in_onrange'] = np.nan

        # POC stability into entry
        for L, tag in ((1800, '30m'), (3600, '60m')):
            j = int(np.searchsorted(ts, et - np.timedelta64(L, 's'), side='right')) - 1
            if j >= 0 and np.isfinite(poc[j]):
                rec[f'poc_drift_{tag}_t'] = abs(p_poc - float(poc[j])) / TICK
            else:
                rec[f'poc_drift_{tag}_t'] = np.nan
        # minutes since POC last differed from its entry value by > 2 ticks
        back = poc[:ei + 1]
        moved = np.nonzero(np.abs(back - p_poc) > 2 * TICK)[0]
        if len(moved):
            rec['poc_age_min'] = float((et - ts[moved[-1]]) / np.timedelta64(60, 's'))
        else:
            rec['poc_age_min'] = np.nan  # never elsewhere all session

        # S/R distances, signed + = entry above ref
        allrefs = {'ONH': on_hi, 'ONL': on_lo, 'Open': rth_open, **refs}
        near = []
        for name, v in allrefs.items():
            dt_ = (entry - v) / TICK if v == v else np.nan
            rec[f'd_{name}_t'] = dt_
            if dt_ == dt_:
                near.append(abs(dt_))
        rec['nearest_ref_t'] = min(near) if near else np.nan
        # does the traded POC itself sit on a prior-day/ON reference?
        pocnear = [abs((p_poc - v) / TICK) for v in allrefs.values() if v == v]
        rec['poc_nearest_ref_t'] = min(pocnear) if pocnear else np.nan
        rec['gap_open_t'] = ((rth_open - refs['pdClose']) / TICK
                             if refs.get('pdClose') else np.nan)
        # trade-favor distances to the adverse-side barrier (stop side S/R):
        # for a long (fading down onto POC) the adverse refs are those BELOW.
        adverse = [v for v in (on_lo if d > 0 else on_hi,
                               refs.get('pdLow' if d > 0 else 'pdHigh'),
                               refs.get('pdVAL' if d > 0 else 'pdVAH'))
                   if v is not None and v == v]
        if adverse:
            rec['cushion_t'] = min(d * (entry - v) / TICK for v in adverse)
        else:
            rec['cushion_t'] = np.nan
        rows.append(rec)
    if si % 20 == 0:
        print(f'{si + 1}/{len(sessions)}  {time.time() - t0:.0f}s', flush=True)

df = pd.DataFrame(rows)
df['is_stop'] = (df.exit_reason == 'stop').astype(int)
df['is_win'] = (df.r > 0).astype(int)
df.to_parquet(OUT)
print('WROTE', OUT, df.shape, f'{time.time() - t0:.0f}s')
