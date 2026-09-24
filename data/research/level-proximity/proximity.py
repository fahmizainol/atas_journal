"""Which levels do my fills actually land on? — derive the references from the trades.

The journal already stores *what I said* about a trade (model tag, confluences).
This asks the complementary question with no self-report in it: given where my
fills actually printed, which of the levels on my chart do they cluster against?

Method, in three parts:

  measure   for every fill (entry and exit), the signed distance in ticks to every
            candidate level, evaluated CAUSALLY — the last drawn bar strictly
            before the fill's own bar, so a developing value area never sees the
            print that made it.

  null      "my entries are near the POC" is worthless until you know how often
            price is near the POC anyway. So each fill draws N_NULL companion
            timestamps from the same session inside a +/-NULL_WINDOW_MIN band of
            its own time-of-day, and we score the same distances at whatever was
            trading then. A level counts only if the real fills sit TIGHTER than
            its own null.

  ladder    levels run collinear (the 9 EMA is the +1 sigma band wearing a hat),
            so marginal scores light up a dozen references and mean nothing. The
            ranking is greedy and conditional: take the best level, then re-ask
            whether each survivor still says anything about fill placement among
            the trades the winner did NOT already explain.

Exits are split by the reason in `comment` (stop / target / trail / manual). A
stopped exit lands wherever the stop was — vol-ruler distance from entry, not a
level — so it doubles as a negative control: if stops "find" levels, the null is
broken.

Cache-only: never buys a tick day (Databento budget is empty).

Usage: .venv/bin/python data/research/level-proximity/proximity.py
"""
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo

sys.path.insert(0, 'src')
sys.path.insert(0, '.')
import numpy as np
import pandas as pd

from api import session_chart as sc
from journal.level_tag import (DayLevels, MAX_ALIGN_PTS, N_NULL, TICK,
                               _companions)

# The measurement itself lives in ``journal.level_tag`` — the same code the
# per-trade tagger runs. This file is the *population* question ("which
# references do my fills cluster on"), which needs per-level columns rather than
# the tagger's families, but it must not own a second copy of the level reader
# or the null: two implementations of a null is two nulls, and the one that
# isn't exercised is the one that rots.
NY = ZoneInfo('America/New_York')
DB = 'data/journal.db'
OUT = 'data/research/level-proximity/proximity.parquet'

NEAR_TICKS = 8.0         # "at the level" tolerance, descriptive only
MIN_FILLS = 40           # below this a level is not scored at all
SEED = 20260817

def load_trades() -> pd.DataFrame:
    c = sqlite3.connect(DB)
    df = pd.read_sql_query(
        'select account, instrument, open_ts_utc, close_ts_utc, open_price, '
        'close_price, open_volume, pnl, comment from atas_journal '
        'where open_ts_utc is not null and close_ts_utc is not null', c)
    c.close()
    df['entry_ts'] = pd.to_datetime(df['open_ts_utc'], utc=True, format='ISO8601')
    df['exit_ts'] = pd.to_datetime(df['close_ts_utc'], utc=True, format='ISO8601')
    df['side'] = np.where(df['open_volume'] > 0, 1, -1)
    df['day'] = df['entry_ts'].dt.tz_convert(NY).dt.date
    df['mode'] = df['comment'].fillna('?').str.split(':').str[0]
    df['reason'] = df['comment'].fillna('?').str.split(':').str[-1]
    return df


def rows_for_day(day: date, trades: pd.DataFrame, rng) -> list[dict]:
    frame = sc.session_frame(contract='NQ', day=day, tz=NY, overnight=True,
                             allow_fetch=False, context_days=0)
    if frame is None or frame.ticks.empty:
        return []
    lv = DayLevels(frame)
    if not lv.axes:
        return []

    # Alignment gate. A journal row carries the front month at EXPORT time, so the
    # roll can hand back a different contract than the one that was actually
    # traded — and the fills then sit hundreds of points off a session that looks
    # perfectly healthy. Cheapest possible check: the fill price must match what
    # the tape had at the fill's own instant. Refuse the day rather than measure it.
    gaps = [abs(float(t['open_price']) - lv.price_at(t['entry_ts']))
            for _, t in trades.iterrows()]
    gaps = [g for g in gaps if np.isfinite(g)]
    if not gaps or float(np.median(gaps)) > MAX_ALIGN_PTS:
        return []

    out = []
    for _, t in trades.iterrows():
        for anchor, ts, px in (('entry', t['entry_ts'], t['open_price']),
                               ('exit', t['exit_ts'], t['close_price'])):
            base = {'day': day, 'account': t['account'], 'mode': t['mode'],
                    'reason': t['reason'], 'side': int(t['side']), 'pnl': t['pnl'],
                    'anchor': anchor, 'price': float(px), 'is_null': False,
                    'trade': f"{t['open_ts_utc']}|{t['instrument']}"}
            levels = lv.at(ts)
            out.append(base | {f'd_{k}': (px - v) / TICK for k, v in levels.items()})

            # Companions from the shared picker — same session, same clock,
            # matched on how far and which way price had run.
            for nts, npx in _companions(lv, ts, rng):
                nlv = lv.at(nts)
                out.append(base | {'price': npx, 'is_null': True} |
                           {f'd_{k}': (npx - v) / TICK for k, v in nlv.items()})
    return out


def ranks(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Each fill's distance ranked against ITS OWN companions — one row per fill,
    one column per level, value in [0, 1].

    0 means the real fill was tighter to that level than every comparable moment
    in its own session; 0.5 is what chance produces. Pairing each fill to its own
    companions is what makes the statistic legitimate: it holds the day, the clock
    and the level's very existence fixed, so none of them can masquerade as an
    edge. Pooling absolute distances instead — the first cut's mistake — lets a
    level that is almost never near price win on a dozen coincidences.
    """
    d = df.copy()
    d['fill'] = d['trade'] + '|' + d['anchor']
    out = {}
    for c in cols:
        a = d[['fill', 'is_null', c]].dropna(subset=[c])
        a = a.assign(ad=a[c].abs())
        real = a[~a['is_null']].groupby('fill')['ad'].first()
        nul = a[a['is_null']]
        if real.empty or nul.empty:
            continue
        m = nul.join(real.rename('real_ad'), on='fill').dropna(subset=['real_ad'])
        if m.empty:
            continue
        out[c] = m.assign(t=m['ad'] < m['real_ad']).groupby('fill')['t'].mean()
    return pd.DataFrame(out)


def _p_two_sided(z: float) -> float:
    return float(math.erfc(abs(z) / math.sqrt(2.0)))


def score(rk: pd.DataFrame, min_fills: int = MIN_FILLS) -> pd.DataFrame:
    """Per-level attraction as a paired rank test.

    Under 'this level has nothing to do with where I fill', the ranks are uniform
    and their mean is 0.5. A mean below 0.5 is attraction, above is avoidance —
    and avoidance is a real answer, not a failure, so the sign is kept.
    """
    rec = []
    for c in rk.columns:
        r = rk[c].dropna()
        if len(r) < min_fills:
            continue
        mean, sd, n = r.mean(), r.std(ddof=1), len(r)
        z = (0.5 - mean) / (sd / math.sqrt(n)) if sd > 0 else 0.0
        rec.append({'level': c[2:], 'n': n, 'mean_rank': mean,
                    'tight10': (r < 0.10).mean(), 'z': z,
                    'p': _p_two_sided(z)})
    s = pd.DataFrame(rec)
    if s.empty:
        return s
    s['p_bonf'] = (s['p'] * len(s)).clip(upper=1.0)
    return s.sort_values('z', ascending=False)


def ladder(rk: pd.DataFrame, depth: int = 5, alpha: float = 0.05) -> list[dict]:
    """Greedy conditional ranking — 'which levels', not 'which levels look good'.

    Once a level is picked, the fills it genuinely landed on (rank < 0.10) leave
    the pool and every survivor is re-scored on the remainder. A reference that
    only ever scored by running parallel to the winner has nothing left to
    explain and drops out. Bonferroni is applied inside each round because the
    candidate set is wide by design and the ladder would otherwise walk down a
    column of noise.
    """
    live, picked = rk.copy(), []
    for _ in range(depth):
        s = score(live)
        if s.empty:
            break
        top = s.iloc[0]
        if top['z'] <= 0 or top['p_bonf'] > alpha:
            break
        col = f"d_{top['level']}"
        picked.append({'level': top['level'], 'n': int(top['n']),
                       'mean_rank': round(float(top['mean_rank']), 3),
                       'tight10': round(float(top['tight10']), 3),
                       'z': round(float(top['z']), 2),
                       'p_bonf': float(f"{top['p_bonf']:.2g}")})
        live = live[~(live[col] < 0.10)].drop(columns=[col])
        if len(live) < MIN_FILLS or live.empty:
            break
    return picked


def main():
    rng = np.random.default_rng(SEED)
    tr = load_trades()
    print(f'{len(tr)} trades, {tr["day"].nunique()} days, '
          f'{tr["day"].min()} .. {tr["day"].max()}')

    rows, skipped = [], []
    for day, grp in tr.groupby('day'):
        try:
            r = rows_for_day(day, grp, rng)
        except Exception as e:                       # a day without ticks is data, not a crash
            r, e_ = [], f'{type(e).__name__}: {e}'
            skipped.append((day, e_))
        if not r:
            skipped.append((day, 'no cached session'))
        rows.extend(r)
    df = pd.DataFrame(rows)
    if df.empty:
        print('no sessions resolved from cache — nothing to measure')
        return
    df.to_parquet(OUT)
    real = df[~df['is_null']]
    print(f'measured {real["trade"].nunique()} trades on '
          f'{real["day"].nunique()} days ({len(skipped)} days skipped)')

    cols = [c for c in df.columns if c.startswith('d_')]
    fills = df[~df['is_null']].assign(
        fill=lambda x: x['trade'] + '|' + x['anchor']).set_index('fill')

    for anchor, sub in (('ENTRY', df[df['anchor'] == 'entry']),
                        ('EXIT', df[df['anchor'] == 'exit'])):
        rk = ranks(sub, cols)
        print(f'\n=== {anchor} — paired rank vs own-session companions '
              f'(mean_rank 0.5 = chance, lower = attracted) ===')
        s = score(rk)
        print(s.to_string(index=False, float_format=lambda v: f'{v:8.3f}')
              if not s.empty else '  (too few fills)')
        print(f'--- {anchor} conditional ladder ---')
        lad = ladder(rk)
        for i, p in enumerate(lad, 1):
            print(f'  {i}. {p}')
        if not lad:
            print('  (nothing survives Bonferroni — no level explains these fills)')

    print(f'\n=== EXIT by reason — stops are the NEGATIVE CONTROL '
          f'(a stop lands at vol-ruler distance, not at a level) ===')
    ex = df[df['anchor'] == 'exit']
    for reason, sub in ex.groupby('reason'):
        if sub['trade'].nunique() < MIN_FILLS:
            continue
        s = score(ranks(sub, cols))
        if s.empty:
            print(f'  {reason:8s} (too few)')
            continue
        top = s.iloc[0]
        verdict = 'SURVIVES' if top['p_bonf'] < 0.05 else 'nothing'
        print(f"  {reason:8s} n={int(top['n']):4d}  best={top['level']:12s} "
              f"mean_rank={top['mean_rank']:.3f} z={top['z']:6.2f} "
              f"p_bonf={top['p_bonf']:.2g}  {verdict}")

    # Split-half by calendar: derive on the early days, confirm on the late ones.
    # A ranking that does not survive this is a description of 68 days, not of how
    # the user trades.
    print('\n=== SPLIT-HALF (entries) — derive early, confirm late ===')
    days = sorted(fills['day'].unique())
    cut = days[len(days) // 2]
    ent = df[df['anchor'] == 'entry']
    for half, sub in (('early', ent[ent['day'] < cut]), ('late', ent[ent['day'] >= cut])):
        lad = ladder(ranks(sub, cols))
        names = [p['level'] for p in lad] or ['(none)']
        print(f'  {half:5s} n_days={sub["day"].nunique():3d}  {" > ".join(names)}')


if __name__ == '__main__':
    main()
