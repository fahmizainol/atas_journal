"""One-time carry of the old free-tag reviews onto the setup/discipline axes.

The 2026-08-31 review redesign (journal.review) replaced "≥1 free tag" with two
enumerated axes. The 132 reviews written before it answered those axes through
the tag box, one trade in three, in a vocabulary that had sprawled to 70 tags —
so the unambiguous ones are carried over here and the rest are left NULL, which
under the new gate reads as *never answered*. That is the honest reading: an
absent "Revenge" tag under the old rules never asserted the trade was clean,
so nothing here defaults a discipline in.

Judgment calls live in this script and not in a db migration on purpose: a
migration is a schema fact every install must apply; this is one person's tag
history being read by someone who knows what "Reoffer" meant.

What it does, in order:
  1. copies data/journal.db to data/journal.db.pre-review-axes.bak (once —
     an existing backup is never overwritten);
  2. fills ``setup`` / ``discipline`` from the tag map below, NULL columns
     only, skipping (and listing) any trade whose tags map to two different
     setups;
  3. deletes the literal tags ``None`` and ``???`` — they were the old gate
     being satisfied, not answers — and leaves every other tag exactly where
     it is, mapped ones included: the tag stays as colour, the axis is the
     queryable copy.

Safe to re-run: every write is guarded by IS NULL or by presence, so a second
pass finds nothing to do.

Usage:
    .venv/bin/python demo/review_axes_backfill.py           # report only
    .venv/bin/python demo/review_axes_backfill.py --write   # do it
"""
import json
import shutil
import sys
from pathlib import Path

sys.path[:0] = ['src', '.']

from journal import db as dbmod

#: tag (as typed, case-sensitive) -> setup. Only the unambiguous ones:
#: "Continuation down" is joining a trend but not through any of the six
#: shapes, "Failed breakout" describes the market rather than the entry, and
#: "Under risk" could mean undersized or under-the-limit — all left for the
#: owner to answer, or not, trade by trade.
SETUP_FROM_TAG = {
    "Rally fade": "faded_rally",
    "Reoffer": "faded_rally",
    "Breakout": "joined_breakout",
    "VAH Breakout": "joined_breakout",
    "+Dev2 breakout": "joined_breakout",
    "Expecting breakout": "joined_breakout",
    "Pullback Buy": "joined_pullback",
    "Small Pullback Buy": "joined_pullback",
    "Test": "test",
    "Range Play": "range_play",
}

DISCIPLINE_FROM_TAG = {
    "Revenge": "revenge",
    "FOMO": "fomo",
    "Too early": "rushed",
    "Over risk": "oversized",
}

#: The old gate being satisfied, not answers. Deleted outright.
GATE_ESCAPES = {"None", "???"}


def main() -> None:
    write = "--write" in sys.argv

    if write:
        src = Path("data/journal.db")
        bak = Path("data/journal.db.pre-review-axes.bak")
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f"[backfill] backed up to {bak}")
        else:
            print(f"[backfill] backup already at {bak} — left alone")

    conn = dbmod.connect()
    dbmod.init_db(conn)

    rows = conn.execute(
        "SELECT trade_key, tags_json, setup, discipline FROM trade_notes"
    ).fetchall()

    set_setup, set_disc, conflicts, cleaned = [], [], [], []
    for r in rows:
        try:
            tags = [str(t) for t in json.loads(r["tags_json"] or "[]")]
        except (TypeError, ValueError):
            continue

        wants = {SETUP_FROM_TAG[t] for t in tags if t in SETUP_FROM_TAG}
        if len(wants) > 1:
            conflicts.append((r["trade_key"], sorted(wants)))
        elif wants and r["setup"] is None:
            set_setup.append((wants.pop(), r["trade_key"]))

        d_wants = {DISCIPLINE_FROM_TAG[t] for t in tags if t in DISCIPLINE_FROM_TAG}
        # Two discipline tags (e.g. Revenge + FOMO) are not a contradiction the
        # way two setups are, but a column holds one — the first by tag order
        # is as arbitrary as any, so those are skipped and listed too.
        if len(d_wants) > 1:
            conflicts.append((r["trade_key"], sorted(d_wants)))
        elif d_wants and r["discipline"] is None:
            set_disc.append((d_wants.pop(), r["trade_key"]))

        kept = [t for t in tags if t not in GATE_ESCAPES]
        if kept != tags:
            cleaned.append((r["trade_key"], json.dumps(kept)))

    print(f"[backfill] {len(rows)} note rows: "
          f"{len(set_setup)} setups, {len(set_disc)} disciplines to carry, "
          f"{len(cleaned)} gate-escape tag rows to clean, "
          f"{len(conflicts)} conflicts skipped")
    for key, wants in conflicts:
        print(f"  conflict {key}: tags map to {wants} — answer it by hand")

    if not write:
        print("[backfill] report only — re-run with --write")
        return

    for setup, key in set_setup:
        conn.execute(
            "UPDATE trade_notes SET setup = ?, updated_at = datetime('now') "
            "WHERE trade_key = ? AND setup IS NULL", (setup, key))
    for disc, key in set_disc:
        conn.execute(
            "UPDATE trade_notes SET discipline = ?, updated_at = datetime('now') "
            "WHERE trade_key = ? AND discipline IS NULL", (disc, key))
    for key, tags_json in cleaned:
        conn.execute(
            "UPDATE trade_notes SET tags_json = ?, updated_at = datetime('now') "
            "WHERE trade_key = ?", (tags_json, key))
    conn.commit()
    print("[backfill] written")


if __name__ == "__main__":
    main()
