"""Backfill the cached what-if grid over every stored sitting.

The finish hook (``api.routers.replays._price_whatif``) only ever sees new
sittings, so this is how the ones already on disk get priced — and how a
``replay_whatif.GRID_VERSION`` bump, or an engine change, gets applied to
everything already measured.

Safe to re-run and cheap to re-run: a sitting already priced under the current
engine and ladder is skipped, so a second run costs a directory walk. ``--force``
re-prices regardless, which is what a changed fill model needs.

A sitting no fill model reproduces is stored as a *refusal* rather than skipped.
That is the point: a refusal is a stable fact under this engine, and storing it
is what stops the account view re-paying a second per rep to be told it again.

Usage:
    .venv/bin/python demo/whatif_backfill.py                  # everything unpriced
    .venv/bin/python demo/whatif_backfill.py --account funded # one account
    .venv/bin/python demo/whatif_backfill.py --force          # re-price all
"""
import argparse
import sys
import time

sys.path[:0] = ['src', '.']

from journal import replay_account as A          # noqa: E402
from journal import replay_whatif as W           # noqa: E402
from journal import replays                      # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', help='only this account id (default: every sitting)')
    ap.add_argument('--force', action='store_true', help='re-price even if cached')
    args = ap.parse_args()

    rows = replays.list_attempts(limit=5000)
    if args.account:
        rows = A.attempts_by_account(rows).get(args.account, [])
        if not rows:
            sys.exit(f'no sittings on account {args.account!r}')
    # A rep with no trades has nothing to re-price, and an unsettled one is still
    # being traded — the same two cuts the account walk makes.
    rows = [r for r in rows
            if r.get('status') in A.SETTLED and A.trade_count(r) > 0]
    print(f'{len(rows)} settled sittings with trades')

    priced = skipped = refused = failed = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        aid = r['id']
        if not args.force and W.cached(aid, r) is not None:
            skipped += 1
            continue
        try:
            out = W.price(aid, force=args.force)
        except Exception as e:  # noqa: BLE001 — one bad sitting must not end the run
            failed += 1
            print(f'  [{i}/{len(rows)}] {aid}: FAILED {e!r}')
            continue
        if out.get('valid'):
            priced += 1
        else:
            refused += 1
            print(f'  [{i}/{len(rows)}] {aid}: refused — {out.get("reason")}')
        if i % 10 == 0:
            print(f'  [{i}/{len(rows)}] {time.time() - t0:.0f}s elapsed')

    print(f'\npriced {priced}, refused {refused}, already cached {skipped}, '
          f'failed {failed} — {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
