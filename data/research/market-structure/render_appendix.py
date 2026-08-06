"""Append a data-dictionary appendix (both parquets) + the example-event rows
to docs/research/vah-snap-examples.html."""
import pandas as pd

D = 'data/research/market-structure'
ev = pd.read_parquet(f'{D}/vah_snap_events.parquet')
bl = pd.read_parquet(f'{D}/vah_snap_baseline.parquet')

EVENT_COLS = [
    ('session', 'str', 'Trading day (ET calendar date).'),
    ('src', 'str', "Which developing VAH snapped: <code>gx</code> = Globex profile (full ON+RTH), <code>ny</code> = NY profile (RTH-anchored)."),
    ('hm', 'str', 'Snap minute, ET (HH:MM).'),
    ('is_rth', 'bool', 'True if the snap fired during the RTH session (09:30–16:00 ET); False = overnight.'),
    ('price', 'float', 'Last trade price at the snap minute (index points).'),
    ('snap1_t', 'ticks', 'VAH relocation over the 1 minute of the snap (VAH_now − VAH_prev). The "violence" measure.'),
    ('snap5_t', 'ticks', 'VAH relocation over the prior 5 minutes. NaN if VAH was absent 5 min back.'),
    ('vah_above_t', 'ticks', 'How far the new VAH landed above price at the snap (VAH − price).'),
    ('band_pos', '0–1', 'Where price sat in the upper band: 0 = on dev1, 1 = on dev2.'),
    ('fwd_15m', 'ticks', 'Signed price change 15 min after the snap (+ = up). Capped at RTH close.'),
    ('fwd_30m', 'ticks', 'Signed price change 30 min after.'),
    ('fwd_60m', 'ticks', 'Signed price change 60 min after — the primary outcome.'),
    ('fwd_eod', 'ticks', 'Signed price change to the RTH close.'),
    ('max_up_60m', 'ticks', 'Best up-excursion within 60 min (max price − snap price).'),
    ('max_dn_60m', 'ticks', 'Worst down-excursion within 60 min (min price − snap price, ≤ 0).'),
    ('broke_vah', '0/1', 'Price traded ≥ snapped VAH + 2t within 60 min — i.e. the "resistance" gave way.'),
    ('retest_reject', '0/1', 'Came within 8t of the VAH, then printed ≥ 20t below it without breaking through — the literal resistance-held shape.'),
]
BASE_COLS = [
    ('session / hm / is_rth / price / band_pos', '', 'Same meaning as the events table.'),
    ('vah_above', 'bool', 'Was the Globex VAH above price at this in-band minute (context flag, no snap required).'),
    ('fwd_* / max_* / broke_vah / retest_reject', '', 'Same forward-outcome fields as the events table, measured from this baseline minute.'),
]

# rows for the charted examples (+ all of Feb 4)
keys = [('2025-04-07', 'gx', '09:54'), ('2025-08-22', 'ny', '09:48'),
        ('2025-05-13', 'ny', '09:33'), ('2025-05-21', 'ny', '13:01'),
        ('2026-04-01', 'ny', '13:35'), ('2026-02-13', 'ny', '14:37'),
        ('2026-01-02', 'gx', '10:04'), ('2025-10-15', 'gx', '10:48')]
mask = pd.Series(False, index=ev.index)
for s, sr, h in keys:
    mask |= (ev.session == s) & (ev.src == sr) & (ev.hm == h)
mask |= (ev.session == '2025-02-04')
rows = ev[mask].sort_values(['session', 'hm'])

SHOW = ['session', 'src', 'hm', 'is_rth', 'snap1_t', 'snap5_t', 'vah_above_t',
        'band_pos', 'fwd_30m', 'fwd_60m', 'fwd_eod', 'max_dn_60m', 'broke_vah', 'retest_reject']


def dict_table(cols):
    tr = ''.join(
        f'<tr><td class="mono">{c}</td><td class="ty">{t}</td><td>{d}</td></tr>'
        for c, t, d in cols)
    return f'<table class="dd"><thead><tr><th>column</th><th>unit</th><th>meaning</th></tr></thead><tbody>{tr}</tbody></table>'


def data_table(df, cols):
    head = ''.join(f'<th>{c}</th>' for c in cols)
    body = ''
    for _, r in df.iterrows():
        tds = ''
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cell = '' if pd.isna(v) else (f'{v:.2f}' if c == 'band_pos' else f'{v:+.0f}')
            elif isinstance(v, (bool,)) or c in ('broke_vah', 'retest_reject'):
                cell = 'Y' if bool(v) else '·'
            else:
                cell = str(v)
            neg = isinstance(v, float) and not pd.isna(v) and v < 0 and c.startswith(('fwd', 'max'))
            pos = isinstance(v, float) and not pd.isna(v) and v > 0 and c.startswith('fwd')
            cls = ' class="neg"' if neg else (' class="pos"' if pos else '')
            tds += f'<td{cls}>{cell}</td>'
        body += f'<tr>{tds}</tr>'
    return f'<table class="data"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


appendix = f'''<h2 id="appendix">Appendix — what the analyzed data stores</h2>
<p class="secnote">Two parquet files. <code>vah_snap_events.parquet</code> ({len(ev)} rows) —
one row per snap event. <code>vah_snap_baseline.parquet</code> ({len(bl)} rows) — one row
per unconditional in-upper-band minute (every 5th), the control the events are measured
against. All distances are in NQ ticks (0.25 pt); forward returns are signed, + = up.</p>

<h3 class="ah">Events — <code>vah_snap_events.parquet</code></h3>
{dict_table(EVENT_COLS)}

<h3 class="ah">Baseline — <code>vah_snap_baseline.parquet</code></h3>
{dict_table(BASE_COLS)}

<h3 class="ah">The charted events, as stored</h3>
<p class="secnote">Every event drawn above plus all of 2025-02-04, straight from the events
parquet — so each chart's numbers are traceable to a row.</p>
<div class="scroll">{data_table(rows[SHOW], SHOW)}</div>

<style>
.dd,.data{{border-collapse:collapse;font-size:12px;margin:4px 0 8px;width:100%;}}
.dd th,.dd td,.data th,.data td{{border:1px solid var(--border);padding:4px 8px;text-align:left;vertical-align:top;}}
.dd th,.data th{{color:var(--ink2);font-weight:600;background:var(--surface);}}
.dd .mono,.mono{{font-family:ui-monospace,Menlo,monospace;color:var(--ink);white-space:nowrap;}}
.dd .ty{{color:var(--muted);white-space:nowrap;}}
.data{{font-variant-numeric:tabular-nums;white-space:nowrap;}}
.data td{{text-align:right;}} .data td:nth-child(-n+4){{text-align:left;}}
.data .neg{{color:var(--dn);}} .data .pos{{color:var(--up);}}
.ah{{font-size:13px;margin:20px 0 2px;}}
.scroll{{overflow-x:auto;}}
</style>'''

html = open('docs/research/vah-snap-examples.html').read()
marker = '<h2 id="appendix">'
tail = '<p class="secnote" style="margin-top:30px">'
if marker in html:
    html = html[:html.index(marker)] + html[html.index(tail):]
html = html.replace(tail, appendix + '\n' + tail, 1)
open('docs/research/vah-snap-examples.html', 'w').write(html)
print('appended appendix; example rows:', len(rows))
