"""Standalone checks for the recording-name helpers.

No pytest in this project, so run directly:  ``.venv/bin/python tests/test_recordings.py``
(the functions are still named ``test_*`` so pytest picks them up if it's ever
added).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from journal.recordings import expected_recording_name, parse_attempt_no  # noqa: E402


def test_parse_attempt_no():
    # First take: no suffix on the export name -> attempt 1.
    assert parse_attempt_no("ATAS_statistics_04032026_05032026.xlsx") == 1
    # Re-done take, single digit (legacy) and zero-padded (new convention).
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-2.xlsx") == 2
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-02.xlsx") == 2
    assert parse_attempt_no("ATAS_statistics_14042026_15042026-10.xlsx") == 10
    # The date range's underscores are never mistaken for the attempt suffix.
    assert parse_attempt_no("ATAS_statistics_30042026_29052026.xlsx") == 1


def test_expected_recording_name():
    # Uppercase month, zero-padded day + attempt.
    assert expected_recording_name(date(2026, 3, 4), 1) == "04-MAR-2026-01.mp4"
    assert expected_recording_name(date(2024, 4, 3), 2) == "03-APR-2024-02.mp4"
    assert expected_recording_name(date(2026, 6, 13), 1) == "13-JUN-2026-01.mp4"
    assert expected_recording_name(date(2026, 12, 31), 10) == "31-DEC-2026-10.mp4"


def test_round_trip_matches_user_convention():
    # The whole point: an export's parsed attempt picks the matching recording.
    sf = "ATAS_statistics_13062026_14062026-2.xlsx"
    name = expected_recording_name(date(2026, 6, 13), parse_attempt_no(sf))
    assert name == "13-JUN-2026-02.mp4"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
