import pandas as pd, numpy as np
SP = '/tmp/claude-1000/-home-afahmi-repos-atas-journal/8cd1ee6c-1406-45bd-a71e-c26cca627300/scratchpad/'
lf = pd.read_parquet(SP + 'loser_features.parquet')
ef = pd.read_parquet(SP + 'flow_features.parquet')  # entry-anchored, prior study
df = lf.merge(ef.drop(columns=['session', 'r', 'net', 'exit_reason', 'mfe_r', 'mae_r', 'dur_s']),
              on='idx', how='left')
df['date'] = pd.to_datetime(df.session)
df = df.sort_values('date').reset_index(drop=True)

def cohort(row):
    if row.exit_reason == 'stop': return 'STOP'
    if row.r >= 2: return 'BIG'
    if row.r >= 0.5: return 'SMALLWIN'
    return 'SCRATCH'
df['cohort'] = df.apply(cohort, axis=1)
df['is_stop'] = (df.cohort == 'STOP').astype(int)
# derived composition features (60s pre-entry, from prior study conventions)
df['pre60_bigpart'] = df.pre60_big10_vol / df.pre60_vol.replace(0, np.nan)
print('cohorts:', df.cohort.value_counts().to_dict())

def auc(pos, neg):
    pos = np.asarray(pos, float); neg = np.asarray(neg, float)
    pos = pos[~np.isnan(pos)]; neg = neg[~np.isnan(neg)]
    if len(pos) < 2 or len(neg) < 2: return np.nan
    allv = np.concatenate([pos, neg]); ranks = pd.Series(allv).rank().values
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))

def table(name, pos, neg, feats, k=14):
    print(f'\n===== {name}  (n_pos={len(pos)}, n_neg={len(neg)}) =====')
    rows = []
    for f in feats:
        a = auc(pos[f], neg[f])
        if np.isnan(a): continue
        rows.append((f, a, abs(a - 0.5), np.nanmedian(pos[f]), np.nanmedian(neg[f])))
    rows.sort(key=lambda x: -x[2])
    print(f'{"feature":26s} {"AUC":>6s} {"|sep|":>6s} {"pos_med":>10s} {"neg_med":>10s}')
    for f, a, s, pm, nm in rows[:k]:
        print(f'{f:26s} {a:6.3f} {s:6.3f} {pm:10.2f} {nm:10.2f}')
    return rows

# ---------- A. ENTRY-TIME: can you smell a stop at the fill? ----------
PRE = [c for c in ef.columns if c.startswith('pre')] + ['pre60_bigpart']
stop = df[df.cohort == 'STOP']; nonstop = df[df.cohort != 'STOP']
win = df[df.r > 0]
print('\n########## A. ENTRY-TIME — STOP vs everything (actionable if any) ##########')
ra = table('STOP vs NON-STOP — pre-entry', stop, nonstop, PRE)
table('STOP vs WIN — pre-entry', stop, win, PRE, k=8)

# entry-anchored absorption/exhaustion from the winners study (post-fill = descriptive)
AE = [c for c in ['pre60_absorp', 'pre180_absorp', 'pre300_absorp', 'exh_sell_ratio',
                  'exh_sell_drop', 'low_absorp', 'low_sellvol', 'low_recov_pts',
                  'cvd_div_at_low', 'depth_to_low_ticks'] if c in df.columns]
table('STOP vs NON-STOP — absorption/exhaustion (entry-anchored; post-fill descriptive)',
      stop, nonstop, AE, k=10)

# ---------- B. MATCHED-DEPTH EARLY WARNING ----------
print('\n########## B. UNDERWATER TOUCH — among trades down X, who stops? ##########')
for tag, thr in (('t25', '-0.25R'), ('t40', '-0.40R'), ('t70', '-0.70R')):
    d = df[df.get(f'{tag}_hit', 0) == 1]
    if not len(d): continue
    s = d[d.is_stop == 1]; rcv = d[d.is_stop == 0]
    print(f'\n--- touched {thr}: {len(d)} trades ({len(s)} stop, {len(rcv)} recover) '
          f'| recoverers avg R {rcv.r.mean():.2f} ---')
    feats = [c for c in d.columns if c.startswith(tag + '_') and c != f'{tag}_hit']
    table(f'{thr}: STOP vs RECOVER — tape in 60s into the touch', s, rcv, feats, k=12)

# split-half on the top t40 features
d40 = df[df.get('t40_hit', 0) == 1]
if len(d40):
    mid = d40.date.median()
    feats40 = [c for c in d40.columns if c.startswith('t40_') and c != 't40_hit']
    scored = sorted(((f, auc(d40[d40.is_stop == 1][f], d40[d40.is_stop == 0][f])) for f in feats40),
                    key=lambda x: -abs((x[1] or 0.5) - 0.5))[:6]
    print('\n--- split-half robustness (t40 top features) ---')
    for f, a in scored:
        h1 = d40[d40.date <= mid]; h2 = d40[d40.date > mid]
        a1 = auc(h1[h1.is_stop == 1][f], h1[h1.is_stop == 0][f])
        a2 = auc(h2[h2.is_stop == 1][f], h2[h2.is_stop == 0][f])
        print(f'{f:26s} full={a:.3f}  H1={a1:.3f}  H2={a2:.3f}')

# ---------- C. STOP ANATOMY ----------
print('\n########## C. STOP ANATOMY — the print and its aftermath ##########')
s = df[df.cohort == 'STOP']
s = s.assign(capit_volratio=s.x60_volrate / s.hold_volrate.replace(0, np.nan),
             x60_bigpart_vs_hold=s.x60_big10_part - s.hold_big10_part)
print(f'n stops: {len(s)}')
print('\npre-exit 60s vs hold-average tape (capitulation check):')
print(s[['capit_volratio', 'x60_big10_part', 'hold_big10_part', 'x60_cvd', 'x60_big10_sd', 'x60_big50_sd',
         'x60_absorp', 'x_exh_ratio']].median().round(3).to_string())
print('same for SMALLWIN exits (baseline):')
print(df[df.cohort == 'SMALLWIN'][['x60_absorp', 'x_exh_ratio']].median().round(3).to_string())
# does absorption/exhaustion at the stop predict the v-reverse (which we cannot trade, but tells the story)?
for f in ('x60_absorp', 'x_exh_ratio'):
    s3 = s.dropna(subset=[f, 'p300_maxrec_r'])
    hi3 = s3[s3[f] >= s3[f].median()]; lo3 = s3[s3[f] < s3[f].median()]
    print(f'stops split by {f}: HIGH -> bounce {hi3.p300_maxrec_r.median():+.2f}R / fall {hi3.p300_maxadv_r.median():+.2f}R'
          f' | LOW -> bounce {lo3.p300_maxrec_r.median():+.2f}R / fall {lo3.p300_maxadv_r.median():+.2f}R')
print('\npost-exit path (R units of initial risk):')
for W in (60, 300, 900):
    print(f'  {W:>3d}s: med max bounce {s[f"p{W}_maxrec_r"].median():+.2f}R | '
          f'med further fall {s[f"p{W}_maxadv_r"].median():+.2f}R | '
          f'med end {s[f"p{W}_end_r"].median():+.2f}R | '
          f'bounce>0.5R {(s[f"p{W}_maxrec_r"]>0.5).mean():.0%} | '
          f'falls another 0.5R {(s[f"p{W}_maxadv_r"]>0.5).mean():.0%}')
print(f'\nregain ENTRY price within 15min of stop: {(s.regain_entry_s.notna()).mean():.0%}'
      f' (median {s.regain_entry_s.median():.0f}s when it happens)')
print('\npost-stop 60s tape (are big buyers eating our stop?):')
print(s[['p60_cvd', 'p60_big10_sd', 'p60_big50_sd', 'p60_big10_part']].median().round(3).to_string())
print('\nsame post-window stats for SMALLWIN exits (baseline):')
sw = df[df.cohort == 'SMALLWIN']
print(sw[['p60_cvd', 'p60_big10_sd', 'p60_big10_part']].median().round(3).to_string())

# does capitulation-style stop (big-print heavy final 60s) predict the v-reverse?
s2 = s.dropna(subset=['x60_big10_part', 'p300_maxrec_r'])
hi = s2[s2.x60_big10_part >= s2.x60_big10_part.median()]
lo = s2[s2.x60_big10_part < s2.x60_big10_part.median()]
print(f'\nstops split by big-lot share of final 60s tape:')
print(f'  HIGH bigpart stops: post-300s bounce med {hi.p300_maxrec_r.median():+.2f}R, further-fall {hi.p300_maxadv_r.median():+.2f}R  (n={len(hi)})')
print(f'  LOW  bigpart stops: post-300s bounce med {lo.p300_maxrec_r.median():+.2f}R, further-fall {lo.p300_maxadv_r.median():+.2f}R  (n={len(lo)})')

# ---------- D. cohort medians on touch/exit features ----------
print('\n########## D. DESCRIPTIVE MEDIANS by cohort ##########')
cols = [c for c in ['t25_hit', 't40_hit', 't40_secs', 't40_dropspeed', 't40_big10_part', 't40_big10_sd',
                    't40_cvd', 't40_absorp', 't40_exh_ratio', 't40_cvd_div',
                    'x60_big10_part', 'x60_absorp', 'x_exh_ratio',
                    'p300_maxrec_r', 'p300_maxadv_r'] if c in df.columns]
print(df.groupby('cohort')[cols].median().reindex(['STOP', 'SCRATCH', 'SMALLWIN', 'BIG']).round(3).T.to_string())
df.to_parquet(SP + 'loser_analyzed.parquet')
print('\nWROTE loser_analyzed.parquet')
