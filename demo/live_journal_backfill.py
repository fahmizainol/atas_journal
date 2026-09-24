"""Put back the live lots the journal's content hash ate, and the fees it dropped.

Two independent defects put the journal out of step with the broker's statement,
and this repairs the days both of them already damaged. The code fixes are in
``journal.ingest._journal_key`` (a lot discriminator) and the ``atas_journal.fees``
column; neither reaches backwards, which is what this is for.

**1. Dropped lots.** The importer's hash reads account, instrument, both stamps,
both prices and P&L. The portions of one scale-out agree in every one of them —
``Broker._open_state`` freezes the open stamp, and the lots closed by a single
sweep share an exit price and a per-lot P&L — so they hashed alike and
``db.insert_journal`` (INSERT OR IGNORE) kept one and silently discarded the
rest. Size and reason do not separate them either.

**2. Missing commission.** ``Broker._emit_trade`` has always computed ``fees``
and written it to ``orders.jsonl``; ``atas_journal`` had nowhere to put it, so
every live day read gross while the broker reports net.

``orders.jsonl`` is the trustworthy record — its ``trade`` events reconcile to
the broker's own reported net to the cent — so every number here is re-derived
from it rather than guessed at.

**STRICTLY ADDITIVE, AND THAT IS NOT A STYLE CHOICE.** A logical trade's
``trade_key`` — the id notes, reviews, recall cards, models and level tags are
all filed under — is ``sha1(instrument | first lot's dedupe_key)``. Rewriting the
surviving rows under the new hash would move that key and orphan every one of
them (13 notes, 13 model bindings, 18 context rows and 520 level rows across the
affected days). So existing rows are never re-keyed and never deleted: the lots
that were dropped are inserted beside them, and ``fees`` is filled in place.

The safety check is not advisory. Everything happens inside one transaction,
the logical trade keys are recomputed with the new rows in place, and the work
is **rolled back** unless every key that existed before still exists after.

Usage:
    .venv/bin/python demo/live_journal_backfill.py           # report only
    .venv/bin/python demo/live_journal_backfill.py --write   # do it
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path[:0] = ['src', '.']

from journal import db as dbmod  # noqa: E402
from journal import trades as trmod  # noqa: E402
from journal.ingest import _journal_key  # noqa: E402
from journal.live import booking as bk  # noqa: E402

ORDERS = Path('data/live/orders')
BACKUP = Path('data/journal.db.pre-lot-backfill.bak')

#: The venue every live contract traded on, and what `Broker._book` stamps.
EXCHANGE = 'CME'


def emitted_lots() -> dict[tuple[str, str], list[dict]]:
    """Every round trip the broker emitted, keyed by (account, session date).

    **Attributed by timeline, not by file.** One ``orders.jsonl`` can contain an
    ``account`` event switching mid-session — 2026-09-10 holds both TEST001 and
    TEST002 — so reading the first one and applying it to the whole file files
    half a day under the wrong account. And a day's lots are gathered across
    *both* contract folders, because ``source_file`` is ``live/<account>/<day>``:
    one sitting per account per day, whatever it was traded in.
    """
    out: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for path in sorted(ORDERS.glob('*/*/orders.jsonl')):
        # The contract folder, which is the only thing that names the instrument
        # on an older log: `symbol` was added to the trade event later, and the
        # August sittings predate it. A lot's own symbol still wins where it has
        # one — a sitting can send its orders to the micro of the contract it is
        # reading, and then the folder is not the whole story.
        folder_symbol = path.parent.parent.name
        day = path.parent.name
        account = None
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get('event') == 'account':
                account = rec.get('account')
            elif rec.get('event') == 'trade' and account:
                rec['_symbol'] = rec.get('symbol') or folder_symbol
                out[(account, day)].append(rec)
    return out


def _rows_for(conn, source_file: str) -> dict[str, tuple]:
    """{dedupe_key: (fees, comment)} for one sitting already in the journal."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT dedupe_key, fees, comment FROM atas_journal WHERE source_file = ?",
        (source_file,))}


def plan(conn) -> tuple[list[dict], list[tuple[str, float]], list[str]]:
    """What to insert, what to fill in, and a line per sitting for the report.

    A lot is "already recorded" by *count*, not by identity: four identical lots
    sharing one old-form hash can be represented by at most one row, so three
    are missing however you look at them. Where the survivor's comment names one
    of them, that one is the lot treated as present — it keeps the stored reason
    (`live:reduce` vs `live:manual`) honest — otherwise the first is.
    """
    inserts: list[dict] = []
    fee_fills: list[tuple[str, float]] = []
    report: list[str] = []

    for (account, day), lots in sorted(emitted_lots().items(), key=lambda kv: kv[0][::-1]):
        src = bk.source_file_for(account, date.fromisoformat(day))
        existing = _rows_for(conn, src)
        if not existing:
            # A sitting the journal never recorded at all. Out of scope: it has
            # no session row and no bound records, and inventing one here would
            # be a different decision than repairing a day that exists.
            report.append(f'{day}  {account:<26} not in the journal — skipped')
            continue

        groups: dict[str, list[dict]] = collections.defaultdict(list)
        for lot in lots:
            args = dict(account=account, instrument=f"{lot['_symbol']}@{EXCHANGE}",
                        source_file=src, trade=lot)
            lot['_old_key'] = bk.journal_row(**args)['dedupe_key']
            lot['_new_key'] = bk.journal_row(**args, lot=lot.get('id'))['dedupe_key']
            groups[lot['_old_key']].append(lot)

        missing = 0
        missing_pnl = 0.0
        filled = 0
        for old_key, group in groups.items():
            # **Both key forms, or this is not idempotent.** A lot restored by an
            # earlier run is in the journal under the *new* hash, which this
            # grouping does not look at — so checking only the old one re-proposes
            # every previously repaired lot on the next pass, and the report's
            # arithmetic then counts them twice. INSERT OR IGNORE would still
            # refuse to duplicate the rows, so the data was never at risk; the
            # plan was simply lying about the work.
            pending = [lot for lot in group if lot['_new_key'] not in existing]

            if old_key in existing:
                # Fill the commission on the row that is already there.
                stored_fees, stored_comment = existing[old_key]
                fees = group[0].get('fees')
                if stored_fees is None and fees is not None:
                    fee_fills.append((old_key, float(fees)))
                    filled += 1
                # One of the pending lots *is* that surviving row, recorded under
                # the old hash. Prefer the one whose reason the row already names,
                # so `live:reduce` vs `live:manual` stays faithful to what closed.
                keep = next(
                    (i for i, lot in enumerate(pending)
                     if f"live:{lot.get('reason', 'manual')}" == stored_comment), 0)
                pending = [x for i, x in enumerate(pending) if i != keep]

            for lot in pending:
                inserts.append(bk.journal_row(
                    account=account, instrument=f"{lot['_symbol']}@{EXCHANGE}",
                    source_file=src, trade=lot, lot=lot.get('id')))
                missing += 1
                missing_pnl += float(lot['pnl'])

        if missing or filled:
            emitted_pnl = sum(float(x['pnl']) for x in lots)
            stored_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM atas_journal WHERE source_file = ?",
                (src,)).fetchone()[0]
            # Arithmetic, not aspiration: what the sitting holds now, what this
            # run adds, and what the broker says the day was. The three agree
            # only where `orders.jsonl` covers the whole sitting — a log that
            # started late leaves rows it cannot explain, and saying so here is
            # the difference between a repaired day and a silently widened one.
            after = stored_pnl + missing_pnl
            gap = after - emitted_pnl
            line = (f'{day}  {account:<26} +{missing:>2} lot(s)  fees x{filled:<3} '
                    f'{stored_pnl:>9.2f} +{missing_pnl:>8.2f} = {after:>9.2f}  '
                    f'broker {emitted_pnl:>9.2f}')
            if abs(gap) > 0.05:
                line += f'   [{gap:+.2f} in rows the log does not cover]'
            report.append(line)
    return inserts, fee_fills, report


def live_trade_keys(conn) -> set[str]:
    """Every logical ``trade_key`` the journal currently yields for live sittings."""
    journal = dbmod.load_journal(conn)
    logical = trmod.build_logical_trades(journal)
    if logical.empty:
        return set()
    live = logical[logical['source_file'].str.startswith(f'{bk.LIVE_PREFIX}/')]
    return set(live['trade_key'])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='apply the repair')
    args = ap.parse_args()

    conn = dbmod.connect()
    inserts, fee_fills, report = plan(conn)

    print('\n'.join(report) or 'nothing to repair')
    print(f'\n{len(inserts)} lot(s) to insert, {len(fee_fills)} row(s) to fill fees on')
    if not args.write:
        print('\nreport only — pass --write to apply')
        return 0
    if not inserts and not fee_fills:
        return 0

    if not BACKUP.exists():
        shutil.copy2(dbmod.DB_PATH, BACKUP)
        print(f'backed up to {BACKUP}')

    before = live_trade_keys(conn)
    try:
        conn.execute('BEGIN')
        cols = ','.join(dbmod.JOURNAL_COLS)
        conn.executemany(
            f"INSERT OR IGNORE INTO atas_journal ({cols}) "
            f"VALUES ({','.join('?' for _ in dbmod.JOURNAL_COLS)})",
            [tuple(r[c] for c in dbmod.JOURNAL_COLS) for r in inserts])
        conn.executemany(
            "UPDATE atas_journal SET fees = ? WHERE dedupe_key = ?",
            [(f, k) for k, f in fee_fills])

        # The check the docstring promises, against uncommitted rows: a note
        # whose trade moved key is a note pointing at nothing.
        after = live_trade_keys(conn)
        lost = before - after
        if lost:
            conn.rollback()
            print(f'\nROLLED BACK — {len(lost)} logical trade key(s) would have moved:')
            for k in sorted(lost)[:10]:
                print(f'  {k}')
            return 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print(f'\nwrote {len(inserts)} lot(s); every one of the {len(before)} '
          f'existing live trade keys survived')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
