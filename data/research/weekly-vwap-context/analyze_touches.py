"""Cohort analysis of context-classified weekly ±1σ touches.

Maps the hand observations to testable cohorts (upper1 shown; lower1 mirrored,
both pooled after mirroring so "toward the mid" / "away from the mid" is the
common outcome axis):

  retest_after_fail : approach from the mid's side AFTER >=5min residence beyond
                      the band earlier in the session (price failed out, now
                      retesting). Hypothesis: rejects toward the mid.  (obs 1)
  pullback_accepted : approach from OUTSIDE (inside the outer band) after
                      acceptance — residence beyond the band >= threshold or a
                      ±2σ touch. Hypothesis: holds, bounces away from mid. (obs 2)
  pullback_brief    : same approach, no acceptance — the failure-in-progress.
  fresh_deep        : first visit from the mid's side, origin at/через the mid
                      (120-min σ-extreme crossed 0). Hypothesis: mixed.   (obs 3)
  fresh_shallow     : first visit, shallow origin (never left the band's half).

Outcome per event: 60-min first-crossing race at ±0.30σ (toward-mid vs
away-from-mid vs none/ambig) + excursion edge in points.

Usage: .venv/bin/python data/research/weekly-vwap-context/analyze_touches.py
"""
import numpy as np
import pandas as pd

D = 'data/research/weekly-vwap-context'
rng = np.random.default_rng(11)
ACCEPT_MIN = 15  # minutes beyond the band that count as acceptance (swept below)


def perm_p_rate(a, b, n_iter=4000):
    """Two-sided permutation p for difference in mean of 0/1 arrays a vs b."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    if len(a) < 5 or len(b) < 5:
        return np.nan
    obs = abs(a.mean() - b.mean())
    pool = np.concatenate([a, b]); na = len(a); c = 0
    for _ in range(n_iter):
        rng.shuffle(pool)
        if abs(pool[:na].mean() - pool[na:].mean()) >= obs:
            c += 1
    return (c + 1) / (n_iter + 1)


def load(race_col='race60'):
    t = pd.read_parquet(f'{D}/touches.parquet')
    t = t[~t.first_session].copy()
    t['race60'] = t[race_col]
    b = t[t.level.isin(['upper1', 'lower1'])].copy()
    up = b.level == 'upper1'

    # mirror lower1 onto the upper1 frame: everything is "vs the mid"
    b['from_mid_side'] = np.where(up, b.approach == 'below', b.approach == 'above')
    b['res_beyond_min'] = np.where(up, b.res_beyond_u1_min, b.res_beyond_l1_min)
    b['touched_2_before'] = np.where(up, b.touched_u2_before, b.touched_l2_before)
    # origin depth: did the 120-min approach start at/through the mid (σ crossed 0)
    b['origin_deep'] = np.where(up, b.min_sig_before <= 0, b.max_sig_before >= 0)
    b['toward'] = np.where(up, b.race60 == 'dn', b.race60 == 'up')
    b['away'] = np.where(up, b.race60 == 'up', b.race60 == 'dn')
    b['edge_toward_60'] = np.where(up, b.dn_pts_60 - b.up_pts_60,
                                   b.up_pts_60 - b.dn_pts_60)
    b['edge_toward_sig'] = b.edge_toward_60 / b['std']
    return t, b


def cohort(b):
    inside = b[b.from_mid_side]
    outside = b[~b.from_mid_side]
    acc = (outside.res_beyond_min >= ACCEPT_MIN) | outside.touched_2_before
    return {
        'retest_after_fail': inside[inside.res_beyond_min >= 5],
        'fresh_deep': inside[(inside.res_beyond_min < 5) & inside.origin_deep],
        'fresh_shallow': inside[(inside.res_beyond_min < 5) & ~inside.origin_deep],
        'pullback_accepted': outside[acc],
        'pullback_brief': outside[~acc],
    }


def line(name, sub):
    if len(sub) == 0:
        return None
    dec = sub[sub.toward | sub.away]
    return {
        'cohort': name, 'n': len(sub), 'decisive': len(dec),
        'toward_mid_rate': round(dec.toward.mean(), 3) if len(dec) else np.nan,
        'med_edge_toward_pts': round(sub.edge_toward_60.median(), 1),
        'med_edge_toward_sig': round(sub.edge_toward_sig.median(), 3),
        'hit_mid_60': round(sub.hit_mid_60.mean(), 3),
        'rth_share': round(sub.is_rth.mean(), 2),
    }


def table(cs, title):
    print(f'\n== {title} ==')
    rows = [r for r in (line(k, v) for k, v in cs.items()) if r]
    print(pd.DataFrame(rows).to_string(index=False))


def monthly_consistency(sub, col='toward'):
    """In how many months (n>=5 decisive) does toward beat away?"""
    dec = sub[sub.toward | sub.away]
    g = dec.groupby('month')[col].agg(['mean', 'size'])
    g = g[g['size'] >= 5]
    return f"{int((g['mean'] > .5).sum())}/{len(g)} months toward>50% (n>=5)"


def main():
    t, b = load()
    print(f'events: {len(b)} band touches on {b.day.nunique()} seasoned sessions '
          f'({b.day.min()} .. {b.day.max()})')
    print(b.groupby(['level', 'from_mid_side']).size().to_string())

    cs = cohort(b)
    table(cs, f'ALL (accept>={ACCEPT_MIN}m or ±2σ touch)')
    for lvl in ('upper1', 'lower1'):
        table(cohort(b[b.level == lvl]), lvl)
    table(cohort(b[b.is_rth]), 'RTH only')
    table(cohort(b[~b.is_rth]), 'overnight only')

    # contrasts the observations claim
    print('\n== contrasts (permutation p, decisive toward-rate) ==')
    def dtr(s):
        d = s[s.toward | s.away]
        return d.toward.values
    print('retest_after_fail vs fresh_deep   p =',
          round(perm_p_rate(dtr(cs['retest_after_fail']), dtr(cs['fresh_deep'])), 4))
    print('retest_after_fail vs fresh_shallow p =',
          round(perm_p_rate(dtr(cs['retest_after_fail']), dtr(cs['fresh_shallow'])), 4))
    print('pullback_accepted vs pullback_brief p =',
          round(perm_p_rate(dtr(cs['pullback_accepted']), dtr(cs['pullback_brief'])), 4))

    print('\n== stability ==')
    for k, v in cs.items():
        print(f'{k:20s} {monthly_consistency(v)}')
    half = b.day.sort_values().iloc[len(b) // 2]
    for k in cs:
        a = cs[k][cs[k].day < half]; z = cs[k][cs[k].day >= half]
        la, lz = line(k, a), line(k, z)
        if la and lz:
            print(f'{k:20s} half1 toward={la["toward_mid_rate"]} (n={la["decisive"]}) '
                  f'| half2 toward={lz["toward_mid_rate"]} (n={lz["decisive"]})')

    # acceptance-threshold sweep (obs 2's "can't quantify acceptance yet")
    print('\n== pullback-from-outside: bounce (away) rate by residence beyond band ==')
    out = b[~b.from_mid_side]
    outd = out[out.toward | out.away].copy()
    outd['res_bkt'] = pd.cut(outd.res_beyond_min, [-1, 4, 14, 29, 59, 1e9],
                             labels=['0-4m', '5-14m', '15-29m', '30-59m', '60m+'])
    g = outd.groupby(['res_bkt', 'touched_2_before'], observed=True)
    print(g.away.agg(['mean', 'size']).round(3).to_string())

    # fresh-traverse origin sweep (obs 3)
    print('\n== fresh from mid-side: toward (reject) rate by origin σ-extreme ==')
    fr = b[b.from_mid_side & (b.res_beyond_min < 5)]
    frd = fr[fr.toward | fr.away].copy()
    org = np.where(frd.level == 'upper1', frd.min_sig_before, -frd.max_sig_before)
    frd['org_bkt'] = pd.cut(org, [-np.inf, -1, -0.5, 0, 0.5, np.inf],
                            labels=['<-1σ', '-1..-0.5σ', '-0.5..0σ', '0..0.5σ', '>0.5σ'])
    print(frd.groupby('org_bkt', observed=True).toward.agg(['mean', 'size'])
          .round(3).to_string())

    # obs 4: rotation + mid as S/R after band residence
    s = pd.read_parquet(f'{D}/sessions.parquet')
    s = s[~s.first_session]
    print('\n== sessions: mid crossings vs rotational character ==')
    print('corr(mid_crosses, |drift|/range) =',
          round(s[['mid_crosses', 'rot']].corr().iloc[0, 1], 3))
    s['cross_bkt'] = pd.cut(s.mid_crosses, [-1, 0, 2, 6, 1e9],
                            labels=['0', '1-2', '3-6', '7+'])
    print(s.groupby('cross_bkt', observed=True)
          .agg(n=('rot', 'size'), med_rot=('rot', 'median'),
               med_range=('range_pts', 'median'),
               med_absdrift=('gx_drift_pts', lambda x: x.abs().median()))
          .round(2).to_string())

    print('\n== mid touches: does band residence make the mid hold? ==')
    m = t[(t.level == 'mid') & ~t.first_session].copy()
    m = m[m.race60.isin(['up', 'dn'])]
    from_above = m.approach == 'above'
    # held = bounced back to the residence side
    m['held'] = np.where(from_above, m.race60 == 'up', m.race60 == 'dn')
    m['res_src'] = np.where(from_above, m.res_beyond_u1_min, m.res_beyond_l1_min)
    m['res_bkt'] = pd.cut(m.res_src, [-1, 4, 29, 89, 1e9],
                          labels=['0-4m', '5-29m', '30-89m', '90m+'])
    print(m.groupby('res_bkt', observed=True).held.agg(['mean', 'size'])
          .round(3).to_string())

    # robustness: same cohorts scored with the next-bar race (no touch-bar
    # extreme can leak the approach into the outcome)
    _, bx = load('race60_ex')
    table(cohort(bx), 'ROBUSTNESS: race from next bar')
    frx = bx[bx.from_mid_side & (bx.res_beyond_min < 5)]
    frdx = frx[frx.toward | frx.away].copy()
    orgx = np.where(frdx.level == 'upper1', frdx.min_sig_before,
                    -frdx.max_sig_before)
    frdx['org_bkt'] = pd.cut(orgx, [-np.inf, -1, -0.5, 0, 0.5, np.inf],
                             labels=['<-1σ', '-1..-0.5σ', '-0.5..0σ',
                                     '0..0.5σ', '>0.5σ'])
    print('\nfresh origin sweep (next-bar race):')
    print(frdx.groupby('org_bkt', observed=True).toward.agg(['mean', 'size'])
          .round(3).to_string())


if __name__ == '__main__':
    main()
