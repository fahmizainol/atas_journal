"""Backfill pre-entry / post-exit context over the whole journal.

The session-end hook only ever sees sittings that *finish*, so this is how the
existing trades get their windows — and how a ``trade_context.METHOD`` bump gets
applied to everything already measured. Safe to re-run: each trade's row is
replaced whole.

Sibling of ``level_tag_backfill.py``, and deliberately a separate script: the
two measurements carry their own method stamps, so a redefinition of one must
not force a recompute of the other.

Usage:
    .venv/bin/python demo/trade_context_backfill.py            # everything
    .venv/bin/python demo/trade_context_backfill.py 2026-01-01 # from a date
"""
import sys

sys.path[:0] = ['src', '.']

import pandas as pd

from journal import context_store
from journal import db as dbmod
from journal import trade_context as tcmod
from journal import trades as trmod


def main() -> None:
    since = pd.Timestamp(sys.argv[1], tz='UTC') if len(sys.argv) > 1 else None
    conn = dbmod.connect()
    dbmod.init_db(conn)

    logical = trmod.build_logical_trades(dbmod.load_journal(conn),
                                         dbmod.load_executions(conn))
    if since is not None:
        logical = logical[logical['entry_ts_utc'] >= since]
    rows = logical.to_dict('records')
    print(f'{len(rows)} logical trades to measure')

    report = context_store.context_trades(conn, rows)
    skipped = report.pop('days_skipped')
    print(report)
    if skipped:
        print(f'{len(skipped)} day(s) skipped (no cached tape, or the tape '
              f'disagrees with the fills): {", ".join(skipped)}')
    print('methods now in the table:', dbmod.trade_context_methods(conn))
    print(f'trades carrying {tcmod.METHOD}: '
          f'{len(dbmod.trade_context_keys(conn, tcmod.METHOD))}')


if __name__ == '__main__':
    main()
