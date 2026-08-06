"""Touch-fade at the weekly ±1σ: immediate entry vs wait-for-confirmation.

For every seasoned from-mid-side ±1σ touch (the "yolo the first touch" fade),
compare entering at the touch bar's close against waiting for a confirmation
inside a 30-min window:

  immediate : enter at the touch bar's close (baseline)
  close_05  : first 1-min close 0.05σ back on the mid's side of the level
  bar_break : first bar that closes beyond the touch bar's rejection extreme
              (below the touch bar's low for upper1; above its high for lower1)
  two_close : two consecutive closes back on the mid's side of the level

A pending setup ABORTS (no trade) if price trades 0.30σ through the level away
from the mid before the rule fires — the break-away the confirmation is
supposed to dodge.

All variants are scored the same way from their own entry bar e and entry price
(close[e]): 60-min signed edge toward the mid in σ, and a first-crossing race at
entry ± 0.30σ starting at bar e+1 (no touch-bar artifact by construction).
give_up = distance from the touched level to the entry, in σ — the price paid
for waiting.

Usage: .venv/bin/python data/research/weekly-vwap-context/confirm_test.py
"""
import sys
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod
from journal.sim import weekly as weeklymod
from journal.sim.regime import minute_bars

D = 'data/research/weekly-vwap-context'
CONFIRM_WIN = 30   # minutes to wait for confirmation
ABORT_SIG = 0.30   # break-away through the level kills the pending setup
CLOSE_SIG = 0.05   # close-beyond margin for close_05
RACE_SIG = 0.30
OUTCOME_MIN = 60

_day_cache = {}


def day_frame(day_iso):
    if day_iso in _day_cache:
        return _day_cache[day_iso]
    day = pd.Timestamp(day_iso).date()
    contract = tickmod.contract_for_cached('NQ', day)
    on = tickmod.cached_overnight(contract, day)
    rth = tickmod.cached_rth(contract, day)
    seed = weeklymod.weekly_seed('NQ', day)
    full = pd.concat([on, rth], ignore_index=True)
    w = vwapmod.vwap_bands(full, seed=seed)
    bars = minute_bars(full)
    pos = bars['end_idx'].to_numpy()
    fr = {
        'o': bars['open'].to_numpy(), 'h': bars['high'].to_numpy(),
        'l': bars['low'].to_numpy(), 'c': bars['close'].to_numpy(),
    }
    _day_cache[day_iso] = fr
    return fr


def confirm_bar(fr, i, lvl, std, sgn, rule):
    """Entry bar for a rule, or None if aborted / never confirmed.

    sgn = +1 for upper1 (fade is short, 'toward mid' is DOWN): confirmation is
    price coming back DOWN, abort is price running UP. Mirrored via sgn.
    """
    h, l, c = fr['h'], fr['l'], fr['c']
    n = len(c)
    if rule == 'immediate':
        return i
    ref_ext = l[i] if sgn > 0 else h[i]   # touch bar's rejection extreme
    streak = 0
    for k in range(i + 1, min(i + CONFIRM_WIN, n - 1) + 1):
        # abort first: traded through the level away from the mid
        away = h[k] >= lvl + ABORT_SIG * std if sgn > 0 else \
               l[k] <= lvl - ABORT_SIG * std
        if away:
            return None
        back = sgn * (lvl - c[k])          # >0 when close is on the mid's side
        if rule == 'close_05' and back >= CLOSE_SIG * std:
            return k
        if rule == 'bar_break' and (sgn * (ref_ext - c[k]) > 0):
            return k
        if rule == 'two_close':
            streak = streak + 1 if back > 0 else 0
            if streak >= 2:
                return k
    return None


def score(fr, e, sgn, std):
    """Outcome from entry bar e at close[e]: 60m signed edge toward the mid (σ)
    and an entry-price race from bar e+1."""
    h, l, c = fr['h'], fr['l'], fr['c']
    n = len(c)
    entry = c[e]
    j = min(e + OUTCOME_MIN, n - 1)
    if sgn > 0:   # short
        edge = (entry - l[e:j + 1].min()) - (h[e:j + 1].max() - entry)
    else:
        edge = (h[e:j + 1].max() - entry) - (entry - l[e:j + 1].min())
    thr = RACE_SIG * std
    race = 'none'
    for k in range(e + 1, j + 1):
        win = l[k] <= entry - thr if sgn > 0 else h[k] >= entry + thr
        lose = h[k] >= entry + thr if sgn > 0 else l[k] <= entry - thr
        if win and lose:
            race = 'ambig'; break
        if win:
            race = 'win'; break
        if lose:
            race = 'lose'; break
    return entry, edge / std, race


def main():
    t = pd.read_parquet(f'{D}/touches.parquet')
    b = t[~t.first_session & t.level.isin(['upper1', 'lower1'])].copy()
    up = b.level == 'upper1'
    b['from_mid_side'] = np.where(up, b.approach == 'below', b.approach == 'above')
    b['res_beyond_min'] = np.where(up, b.res_beyond_u1_min, b.res_beyond_l1_min)
    b['t2b'] = np.where(up, b.touched_u2_before, b.touched_l2_before)
    # fade: from the mid's side, trade the rejection toward the mid.
    # bounce (obs 2): pullback from outside, trade the band holding — sgn flips
    # so confirmation = closes back AWAY from the mid, abort = 0.30σ through
    # the level toward the mid. Same machinery, mirrored.
    b['setup'] = np.where(b.from_mid_side, 'fade', 'bounce')
    ev = b.sort_values(['day', 'bar'])
    print(f"{(ev.setup == 'fade').sum()} fade + {(ev.setup == 'bounce').sum()} "
          f"bounce ±1σ touches")

    rules = ('immediate', 'close_05', 'bar_break', 'two_close')
    rows = []
    for _, r in ev.iterrows():
        fr = day_frame(r.day)
        fade_sgn = 1 if r.level == 'upper1' else -1
        sgn = fade_sgn if r.setup == 'fade' else -fade_sgn
        for rule in rules:
            e = confirm_bar(fr, int(r.bar), r.level_px, r['std'], sgn, rule)
            if e is None:
                rows.append(dict(day=r.day, bar=r.bar, level=r.level, rule=rule,
                                 setup=r.setup, entered=False, wait=np.nan,
                                 give_up=np.nan, edge=np.nan, race='abort',
                                 retest=r.res_beyond_min >= 5,
                                 accepted=bool((r.res_beyond_min >= 15) or r.t2b)))
                continue
            entry, edge, race = score(fr, e, sgn, r['std'])
            rows.append(dict(
                day=r.day, bar=r.bar, level=r.level, rule=rule, setup=r.setup,
                entered=True, wait=e - int(r.bar),
                give_up=sgn * (r.level_px - entry) / r['std'],
                edge=edge, race=race, retest=r.res_beyond_min >= 5,
                accepted=bool((r.res_beyond_min >= 15) or r.t2b)))
    df = pd.DataFrame(rows)
    df.to_parquet(f'{D}/confirm_test.parquet')

    def table(sub, title):
        print(f'\n== {title} ==')
        out = []
        for rule in rules:
            g = sub[sub.rule == rule]
            e = g[g.entered]
            dec = e[e.race.isin(['win', 'lose'])]
            out.append({
                'rule': rule, 'setups': len(g), 'entered': len(e),
                'aborted': int((~g.entered).sum()),
                'med_wait_min': round(e.wait.median(), 1) if len(e) else np.nan,
                'med_give_up_sig': round(e.give_up.median(), 3) if len(e) else np.nan,
                'win_rate': round(dec.race.eq('win').mean(), 3) if len(dec) else np.nan,
                'med_edge_sig': round(e.edge.median(), 3) if len(e) else np.nan,
                'mean_edge_sig': round(e.edge.mean(), 3) if len(e) else np.nan,
            })
        print(pd.DataFrame(out).to_string(index=False))

    fade, bnc = df[df.setup == 'fade'], df[df.setup == 'bounce']
    table(fade, 'FADE: all from-mid-side touches')
    table(fade[fade.level == 'upper1'], 'FADE upper1 (short)')
    table(fade[fade.level == 'lower1'], 'FADE lower1 (long)')
    table(fade[fade.retest], 'FADE retest_after_fail (obs 1) only')
    table(bnc, 'BOUNCE (obs 2): all pullbacks from outside')
    table(bnc[bnc.accepted], 'BOUNCE with acceptance (obs 2a)')
    table(bnc[~bnc.accepted], 'BOUNCE without acceptance (obs 2b)')

    for name, sub in (('fade', fade), ('bounce', bnc)):
        # what did the aborts dodge? score the immediate entry on aborted setups
        print(f'\n== {name}: immediate-entry outcome on setups each rule ABORTED ==')
        imm = sub[sub.rule == 'immediate'].set_index(['day', 'bar', 'level'])
        for rule in rules[1:]:
            ab = sub[(sub.rule == rule) & ~sub.entered].set_index(
                ['day', 'bar', 'level'])
            j = imm.loc[imm.index.isin(ab.index)]
            dec = j[j.race.isin(['win', 'lose'])]
            if len(j):
                print(f'{rule:9s} aborted {len(j):4d}: immediate would have '
                      f'win_rate={dec.race.eq("win").mean():.3f} '
                      f'med_edge={j.edge.median():+.3f}σ '
                      f'mean_edge={j.edge.mean():+.3f}σ')

        print(f'\n== {name}: split-half (med edge σ, entered only) ==')
        half = sub.day.sort_values().iloc[len(sub) // 2]
        for rule in rules:
            a = sub[(sub.rule == rule) & sub.entered & (sub.day < half)]
            z = sub[(sub.rule == rule) & sub.entered & (sub.day >= half)]
            print(f'{rule:9s} half1 {a.edge.median():+.3f} (n={len(a)}) | '
                  f'half2 {z.edge.median():+.3f} (n={len(z)})')


if __name__ == '__main__':
    main()
