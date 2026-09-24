"""Standalone checks for the export filename's attempt number.

No pytest in this project, so run directly:  ``.venv/bin/python tests/test_attempt_no.py``
(the functions are still named ``test_*`` so pytest picks them up if it's ever
added).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from journal.ingest import parse_attempt_no  # noqa: E402


def test_parse_attempt_no():
    # First take: no suffix on the export name -> attempt 1.
    assert parse_attempt_no("ATAS_statistics_04032026_05032026.xlsx") == 1
    # Re-done take, single digit (legacy) and zero-padded (new convention).
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-2.xlsx") == 2
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-02.xlsx") == 2
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-10.xlsx") == 10
    # The date range's underscores are never mistaken for the attempt suffix.
    assert parse_attempt_no("ATAS_statistics_30042026_29052026.xlsx") == 1


def test_a_foldered_export_keeps_its_attempt():
    # Backtest exports live under their model's folder; the suffix still rules.
    assert parse_attempt_no("backtest/drift-fade/ATAS_statistics_0106_0206-3.xlsx") == 3


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
