"""journal.econ_calendar: the page parse and the banked-week reader."""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from journal import econ_calendar as ec  # noqa: E402


def _ev(id_, dateline, name, cur="USD", impact="high", label="8:30pm"):
    return {"id": id_, "dateline": dateline, "name": name, "currency": cur,
            "impactName": impact, "timeLabel": label, "actual": "0.3%",
            "forecast": "0.2%", "previous": "0.1%"}


def test_parse_keeps_usd_dedupes_and_sorts():
    blob = [_ev(2, 200, "CPI m/m"), _ev(1, 100, "PPI m/m"), _ev(3, 150, "GDP", cur="EUR"),
            _ev(2, 200, "CPI m/m")]  # the page repeats every event
    html = "window.calendarComponentStates[1] = {days:[{events:" + json.dumps(blob, separators=(",", ":")) + "}]};"
    out = ec.parse(html)
    assert [e["id"] for e in out] == [1, 2]
    assert set(out[0]) == set(ec.KEEP)


def test_events_filters_range_impact_and_dedupes_across_weeks(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "ECON_CACHE", tmp_path)
    # 2025-12-10 19:00Z (FOMC) and 2025-12-10 13:30Z (medium), plus a week-edge
    # duplicate banked in both adjacent week files.
    fomc, eci, edge = 1765393200, 1765373400, 1765065600  # edge = 2025-12-07 00:00Z
    now = int(time.time())
    for wk, evs in {"2025-11-30": [_ev(9, edge, "Edge")],
                    "2025-12-07": [_ev(9, edge, "Edge"), _ev(1, fomc, "FOMC"),
                                   _ev(2, eci, "ECI", impact="medium")]}.items():
        (tmp_path / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "fetched_at": now, "events": [{k: e.get(k) for k in ec.KEEP} for e in evs]}))
    d = date(2025, 12, 10)
    assert [e["id"] for e in ec.events(d, d, ("high",))] == [1]
    assert [e["id"] for e in ec.events(d, d, ("high", "medium"))] == [2, 1]
    assert [e["id"] for e in ec.events(date(2025, 12, 7), d, ("high",))] == [9, 1]


def test_week_start_is_sunday():
    assert ec.week_start(date(2026, 9, 24)) == date(2026, 9, 20)
    assert ec.week_start(date(2026, 9, 20)) == date(2026, 9, 20)
