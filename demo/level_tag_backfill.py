"""Backfill measured level proximity over the whole journal.

The session-end hook only ever sees new sittings, so this is how the existing
735 trades get their numbers — and how a ``level_tag.METHOD`` bump gets applied
to everything already scored. Safe to re-run: each trade's rows are replaced
whole, never merged.

Usage:
    .venv/bin/python demo/level_tag_backfill.py            # everything
    .venv/bin/python demo/level_tag_backfill.py 2026-01-01 # from a date
"""
import sys

sys.path[:0] = ['src', '.']

import pandas as pd

from journal import db as dbmod
from journal import level_store, level_tag, trades as trmod


def main() -> None:
    since = pd.Timestamp(sys.argv[1], tz='UTC') if len(sys.argv) > 1 else None
    conn = dbmod.connect()
    dbmod.init_db(conn)

    logical = trmod.build_logical_trades(dbmod.load_journal(conn),
                                         dbmod.load_executions(conn))
    if since is not None:
        logical = logical[logical['entry_ts_utc'] >= since]
    rows = logical.to_dict('records')
    print(f'{len(rows)} logical trades to score')

    report = level_store.tag_trades(conn, rows)
    skipped = report.pop('days_skipped')
    print(report)
    if skipped:
        print(f'{len(skipped)} day(s) skipped (no cached tape, or the tape '
              f'disagrees with the fills): {", ".join(skipped)}')
    if since is None:
        # A whole-journal run scored everything scorable, so anything still
        # carrying an older stamp belongs to a trade that no longer exists or
        # can no longer be scored. Left in place it makes the table mixed, and
        # a mixed table is one where no cross-trade comparison holds.
        dropped = dbmod.prune_trade_levels(conn, level_tag.METHOD)
        if dropped:
            print(f'pruned {dropped} row(s) left by an earlier method')
    print('methods now in the table:', dbmod.trade_levels_methods(conn))


if __name__ == '__main__':
    main()
