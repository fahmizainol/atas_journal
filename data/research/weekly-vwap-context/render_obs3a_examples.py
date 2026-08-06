"""Render an expanded example set for obs 3a — the deep-traverse touches.

The split-by-side scorecard made this the study's most interesting cell:
lower1 deep traverses (price runs from the mid or above all the way down into
weekly −1σ) bounce 57.9% next-bar with an edge that grows with horizon
(+0.21σ@60m → +0.29σ@120m, n=116), while upper1 deep traverses are REVERSED
(47.5% — they punch through). This doc shows both sides with both outcomes,
sampled deterministically (earliest / middle / latest by date within each
side × outcome — no cherry-picking).

Usage: .venv/bin/python data/research/weekly-vwap-context/render_obs3a_examples.py
Writes docs/research/weekly-vwap-obs3a-examples.html
"""
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'data/research/weekly-vwap-context')
import numpy as np

from render_touch_examples import load_events, card, HTML_TMPL

OUT = 'docs/research/weekly-vwap-obs3a-examples.html'


def spread(sub, k):
    """Earliest, evenly spread middles, latest — deterministic date coverage."""
    sub = sub.sort_values(['day', 'bar'])
    if len(sub) <= k:
        return [r for _, r in sub.iterrows()]
    idx = np.linspace(0, len(sub) - 1, k).round().astype(int)
    return [sub.iloc[int(i)] for i in idx]


def main():
    b, cohorts = load_events()
    fd = cohorts['fresh_deep'].copy()
    up = fd.level == 'upper1'
    fd['toward_ex'] = np.where(up, fd.race60_ex == 'dn', fd.race60_ex == 'up')
    fd['away_ex'] = np.where(up, fd.race60_ex == 'up', fd.race60_ex == 'dn')

    sections = []
    for lvl, title, note, k_t, k_a in (
        ('lower1',
         'lower1 deep traverse — the strongest cell (57.9% bounce, n=116)',
         'Price runs from the mid (or higher) all the way down into weekly −1σ '
         'with no prior residence below the band this session. These bounce '
         'toward the mid 57.9% on the next-bar race, with the study\'s only '
         'horizon-growing edge (+0.21σ@60m → +0.29σ@120m). Three bounces, two '
         'punch-throughs — roughly the real ratio.', 3, 2),
        ('upper1',
         'upper1 deep traverse — the reversed cell (47.5% reject, n=60)',
         'The mirror shape into weekly +1σ does NOT reject: deep upward '
         'traverses punch through slightly more often than they fail (47.5% '
         'toward). Two of each outcome.', 2, 2),
    ):
        sub = fd[fd.level == lvl]
        cards = []
        for ev in spread(sub[sub.toward_ex], k_t):
            cards.append(card(ev, 'resolved <b>toward the mid</b> (next-bar race)'))
        for ev in spread(sub[sub.away_ex], k_a):
            cards.append(card(ev, 'resolved <b>away from the mid</b> (next-bar race)'))
        sections.append((title, note, cards))
        print(lvl, len(cards), 'panels')

    body = []
    for title, note, cards in sections:
        body.append(f'<h2>{title}</h2><p class="secnote">{note}</p>'
                    f'<div class="panels">{"".join(cards)}</div>')

    html = HTML_TMPL.replace('__BODY__', ''.join(body))
    html = html.replace(
        '<title>Weekly-band touch context — worked examples</title>',
        '<title>Weekly-band obs 3a — deep-traverse examples</title>').replace(
        'Weekly-band touches in context — worked examples',
        'Obs 3a deep traverses — expanded examples').replace(
        'Real NQ events from the touch-context study',
        'Deep-traverse (obs 3a) touch events from the touch-context study')
    open(OUT, 'w').write(html)
    print('WROTE', OUT, len(html), 'bytes')


if __name__ == '__main__':
    main()
