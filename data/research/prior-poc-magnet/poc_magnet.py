"""Is the prior-day POC a magnet? — non-engine geometry check on drift-fade trades.

Candidate #6 (playbook-scouting-tradezella.md §6) proposes exiting at the
prior-balance POC. Before adding a `prior_poc` target_mode, ask the cheap
question first: on the drift-fade's actual fills, where do the FAVORABLE moves
top out relative to the prior-day POC?

For each trade we know direction, entry, and MFE (max favorable excursion). The
price where the move stalled is  stall = entry ± mfe  (± by direction). We compute
the prior RTH session's POC (+ VAH/VAL/mid as named nulls, + random in-range
levels as a blind null) and ask:

  - usability : is the prior POC even on the PROFIT side of entry? (else it can
                never be a target for that trade)
  - reach     : mfe / distance-to-POC  →  <0.9 undershoot, 0.9-1.1 STALL AT poc,
                >1.1 overshoot.  "runs to vs over/undershoots", the user's question
  - magnet    : does the stall price sit CLOSER to the POC than to null levels?
                (stall within +/-BAND pts of the level, POC vs VAH/VAL/mid/random)

This is geometry, not a re-sim. If there's no magnet here, `prior_poc` as a target
can't help and we skip the engine A/B. If there is, the A/B is the next rung.

Usage: .venv/bin/python data/research/prior-poc-magnet/poc_magnet.py
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import profile as profmod

TICK = 0.25
RUN = sys.argv[1] if len(sys.argv) > 1 else \
    'data/sims/drift-touch-fade-entry-stop/20250203-20260630-v2-b0c570aa/trades.parquet'
LABEL = sys.argv[2] if len(sys.argv) > 2 else 'drift-fade b0c570aa'
BANDS = [6.0, 10.0, 15.0]      # "stall at the level" tolerance (points)
MIN_MFE = 8.0                  # a move must be this favorable to count as a "move"
N_RANDNULL = 20                # blind null levels per trade
SEED = 20260720

_pdlevel_cache = {}
_rth_cache = {}


def stall_from_ticks(day, entry_ts, exit_ts, pdir):
    """Reconstruct where a trade's favorable move peaked (max price for a long /
    min for a short, between entry and exit) — for runs that predate mfe_points."""
    if day not in _rth_cache:
        sym = tickmod.contract_for_cached('NQ', day)
        _rth_cache[day] = tickmod.cached_rth(sym, day) if sym else None
    rth = _rth_cache[day]
    if rth is None or rth.empty:
        return None
    ts_ns = pd.to_datetime(rth['ts_utc'], utc=True).astype('int64').to_numpy()
    a = int(np.searchsorted(ts_ns, pd.Timestamp(entry_ts).value, 'left'))
    b = int(np.searchsorted(ts_ns, pd.Timestamp(exit_ts).value, 'right'))
    seg = rth['price'].to_numpy()[a:b]
    if seg.size == 0:
        return None
    return float(seg.max()) if pdir > 0 else float(seg.min())


def prior_rth(sym, day, back=7):
    d = day - timedelta(days=1)
    for _ in range(back):
        if d.weekday() < 5 and tickmod.contract_for_cached('NQ', d) == sym:
            r = tickmod.cached_rth(sym, d)
            if r is not None and not r.empty:
                return r, d
        d -= timedelta(days=1)
    return None, None


def prior_levels(day):
    """(poc, vah, val, mid, lo, hi) of the prior same-contract RTH session."""
    if day in _pdlevel_cache:
        return _pdlevel_cache[day]
    sym = tickmod.contract_for_cached('NQ', day)
    out = None
    if sym:
        prth, _ = prior_rth(sym, day)
        if prth is not None:
            price = prth['price'].to_numpy('float64')
            size = prth['size'].to_numpy('float64')
            lv = np.rint(price / TICK).astype('int64')
            base = int(lv.min())
            hist = np.bincount(lv - base, weights=size, minlength=int(lv.max()) - base + 1)
            total = float(size.sum())
            poc_i = int(hist.argmax())
            lo_va, hi_va = profmod._value_area(hist, poc_i, total, 0.70)
            out = ((base + poc_i) * TICK, (base + hi_va) * TICK, (base + lo_va) * TICK,
                   (price.max() + price.min()) / 2, float(price.min()), float(price.max()))
    _pdlevel_cache[day] = out
    return out


df = pd.read_parquet(RUN)
df['day'] = pd.to_datetime(df['session']).dt.date
has_mfe = 'mfe_points' in df.columns
rng = np.random.default_rng(SEED)

rows = []
for _, t in df.iterrows():
    lv = prior_levels(t['day'])
    if lv is None:
        continue
    poc, vah, val, mid, lo, hi = lv
    pdir = -1.0 if t['direction'] == 'Short' else 1.0
    entry = float(t['avg_entry'])
    if has_mfe:
        stall = entry + pdir * float(t['mfe_points'])
    else:
        stall = stall_from_ticks(t['day'], t['entry_ts_utc'], t['exit_ts_utc'], pdir)
        if stall is None:
            continue
    mfe = pdir * (stall - entry)                    # favorable excursion (points)

    def offset(L):                                  # >0 = level is ahead in profit dir
        return (L - entry) * pdir

    def stall_dist(L):
        return abs(stall - L)

    o_poc = offset(poc)
    rand = rng.uniform(lo, hi, N_RANDNULL)
    rows.append(dict(
        day=str(t['day']), direction=t['direction'], win=bool(t['points'] > 0),
        mfe=round(mfe, 2), entry=round(entry, 2),
        poc_on_profit_side=o_poc > 0,
        off_poc=round(o_poc, 2),
        reach_poc=(mfe / o_poc) if o_poc > 0 else np.nan,
        d_poc=round(stall_dist(poc), 2),
        d_vah=round(stall_dist(vah), 2), d_val=round(stall_dist(val), 2),
        d_mid=round(stall_dist(mid), 2),
        d_rand=round(float(np.abs(stall - rand).mean()), 2),
        day_range=round(hi - lo, 2),
    ))

R = pd.DataFrame(rows)
if R.empty:
    sys.exit('no rows (no prior-day levels resolved)')


def frac(mask):
    return mask.mean() if len(mask) else float('nan')


moves = R[R.mfe >= MIN_MFE]
winners = R[R.win]
print(f'\n=== Prior-day POC magnet check — {LABEL} — {len(R)} trades, '
      f'{R.day.nunique()} days ===')
print(f'(favorable-move set: mfe>={MIN_MFE}pt, n={len(moves)}; winners n={len(winners)})\n')

print('-- usability: is the prior POC on the PROFIT side of entry? --')
print(f'  all trades : {frac(R.poc_on_profit_side):.0%} have prior POC ahead in the profit direction')
print(f'  moves      : {frac(moves.poc_on_profit_side):.0%}')

reach = moves[moves.poc_on_profit_side].reach_poc.dropna()
print(f'\n-- reach ratio (mfe / distance-to-POC), moves with POC ahead, n={len(reach)} --')
print(f'  undershoot (<0.9) : {frac(reach < 0.9):.0%}   fell short of the POC')
print(f'  STALL AT  (0.9-1.1): {frac((reach >= 0.9) & (reach <= 1.1)):.0%}   topped out at the POC')
print(f'  overshoot  (>1.1) : {frac(reach > 1.1):.0%}   blew past the POC')
print(f'  median reach = {reach.median():.2f}')

print(f'\n-- magnet: does the stall price sit closer to the POC than to null levels? --')
print(f'   (fraction of moves whose top is within +/-BAND pts of each level)')
print(f'   {"level":<10}' + ''.join(f'  +/-{b:g}pt' for b in BANDS))
for name, col in [('prior POC', 'd_poc'), ('prior VAH', 'd_vah'), ('prior VAL', 'd_val'),
                  ('prior mid', 'd_mid'), ('random', 'd_rand')]:
    cells = ''.join(f'  {frac(moves[col] <= b):>6.0%}' for b in BANDS)
    print(f'   {name:<10}{cells}')

print(f'\n-- median stall-distance to each level (points; lower = better magnet) --')
print(f'   POC {moves.d_poc.median():5.1f}   VAH {moves.d_vah.median():5.1f}   '
      f'VAL {moves.d_val.median():5.1f}   mid {moves.d_mid.median():5.1f}   '
      f'rand {moves.d_rand.median():5.1f}   (day range median {moves.day_range.median():.0f})')

slug = LABEL.split()[0].replace('-', '_')
out = f'data/research/prior-poc-magnet/poc_magnet_{slug}.parquet'
R.to_parquet(out)
print(f'\nwrote {out}')
