"""Map a replay attempt to its expected recording filename.

The auto-link scanner ties each ATAS export (``source_file``) to a session
recording purely by **filename**, because the export name and the recording
name share nothing else (the export is ``ATAS_statistics_<range>.xlsx``; the
recording is ``DD-MON-YYYY-NN.mp4``). Two facts the app already knows build that
name:

  * the **replayed day** — derived from the trades' own entry date, not the
    date baked into the export filename (which spans a range);
  * the **attempt number** — parsed from a trailing ``-N`` on the export name.

The attempt number lives *in the export filename* (first take has no suffix →
1; a re-done take is ``…-2.xlsx`` → 2, ``…-02.xlsx`` → 2). That makes it stable
across take deletions, unlike the positional "Attempt N" the day view used to
show — so the same number always points at the same ``-NN`` recording.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

# A trailing "-<digits>" on the export's stem is the attempt number. The date
# range uses underscores (``_05032026``), so the only hyphen is this suffix.
_ATTEMPT_SUFFIX = re.compile(r"-(\d+)$")


def parse_attempt_no(source_file: str) -> int:
    """Attempt number encoded in an ATAS export filename (no suffix → 1).

    ``ATAS_statistics_04032026_05032026.xlsx`` → 1 (first take, implicit).
    ``…15042026-2.xlsx`` → 2, ``…15042026-02.xlsx`` → 2 (zero-pad tolerated).
    """
    stem = Path(source_file).stem  # drops the .xlsx extension
    m = _ATTEMPT_SUFFIX.search(stem)
    return int(m.group(1)) if m else 1


def expected_recording_name(day: date, attempt_no: int) -> str:
    """The recording filename for a day's attempt: ``DD-MON-YYYY-NN.mp4``.

    Uppercase 3-letter English month, zero-padded day and attempt — e.g.
    ``04-MAR-2026-01.mp4``. Format is fixed (one convention, one playable
    extension); only the recordings *folder* is configurable.
    """
    return f"{day:%d-%b-%Y}".upper() + f"-{attempt_no:02d}.mp4"
