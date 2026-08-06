"""Render an expanded example set for obs 2a on the upper1 side — pullbacks
onto weekly +1σ from above, after acceptance in the upper band.

This is the study's worst repeated cell for the observation's hypothesis: the
band holds only 48.2% next-bar overall (n=312), and during NY hours it drops to
43.9% — RTH pullback-buys onto +1σ lose at every confirmation rule. Panels show
both outcomes at roughly the real ~50/50 ratio, sampled deterministically
(earliest / middle / latest by date within each outcome — no cherry-picking).

Usage: .venv/bin/python data/research/weekly-vwap-context/render_obs2a_upper_examples.py
Writes docs/research/weekly-vwap-obs2a-upper-examples.html
"""
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'data/research/weekly-vwap-context')
import numpy as np

from render_touch_examples import load_events, card, HTML_TMPL

OUT = 'docs/research/weekly-vwap-obs2a-upper-examples.html'


def spread(sub, k):
    sub = sub.sort_values(['day', 'bar'])
    if len(sub) <= k:
        return [r for _, r in sub.iterrows()]
    idx = np.linspace(0, len(sub) - 1, k).round().astype(int)
    return [sub.iloc[int(i)] for i in idx]


def main():
    b, cohorts = load_events()
    pa = cohorts['pullback_accepted']
    pa = pa[pa.level == 'upper1'].copy()
    # upper1 pullback: held/bounced = race resolves up (away from the mid)
    pa['held_ex'] = pa.race60_ex == 'up'
    pa['broke_ex'] = pa.race60_ex == 'dn'

    sections = []
    for flag, title, note, k in (
        ('held_ex',
         'Held — the band bounced (what obs 2a predicted)',
         'Price pulled back onto +1σ from above after acceptance (≥15m beyond '
         'the band or a +2σ touch) and the next-bar race resolved back up. '
         'This happens 48.2% of the time — slightly LESS than half.', 3),
        ('broke_ex',
         'Broke — the pullback kept going through the band',
         'The same setup, same acceptance context, resolving down through the '
         'band toward the weekly mid — the marginally more common outcome '
         '(51.8%), and during NY hours the dominant one (56.1%). At entry '
         'time these are indistinguishable from the holds.', 3),
    ):
        sub = pa[pa[flag]]
        cards = [card(ev, ('resolved <b>away from the mid</b> — held' if
                           flag == 'held_ex' else
                           'resolved <b>toward the mid</b> — broke')
                      + ' (next-bar race)') for ev in spread(sub, k)]
        sections.append((title, note, cards))
        print(title, '—', len(cards), 'panels')

    body = []
    for title, note, cards in sections:
        body.append(f'<h2>{title}</h2><p class="secnote">{note}</p>'
                    f'<div class="panels">{"".join(cards)}</div>')

    html = HTML_TMPL.replace('__BODY__', ''.join(body))
    html = html.replace(
        '<title>Weekly-band touch context — worked examples</title>',
        '<title>Weekly-band obs 2a upper1 — pullback examples</title>').replace(
        'Weekly-band touches in context — worked examples',
        'Obs 2a on upper1 — accepted pullbacks, expanded examples').replace(
        'Real NQ events from the touch-context study',
        'Accepted-pullback (obs 2a) touches of weekly +1σ from above')
    open(OUT, 'w').write(html)
    print('WROTE', OUT, len(html), 'bytes')


if __name__ == '__main__':
    main()
