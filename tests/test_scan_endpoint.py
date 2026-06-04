"""End-to-end check of POST /videos/scan against a temp DB + temp folder.

Run directly:  ``.venv/bin/python tests/test_scan_endpoint.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from api.routers import videos  # noqa: E402
from api.scope import resolve_scope  # noqa: E402


def _journal_row(source_file: str, day: str) -> dict:
    """A minimal atas_journal row for a single trade on ``day`` (ISO date)."""
    open_local = f"{day}T09:30:00-04:00"
    open_utc = datetime.fromisoformat(open_local).astimezone(timezone.utc).isoformat()
    close_local = f"{day}T09:35:00-04:00"
    close_utc = datetime.fromisoformat(close_local).astimezone(timezone.utc).isoformat()
    rec = {
        "account": "SIM", "instrument": "NQ",
        "open_ts_local": open_local, "close_ts_local": close_local,
        "open_ts_utc": open_utc, "close_ts_utc": close_utc,
        "open_price": 100.0, "open_volume": 1.0, "close_price": 110.0,
        "close_volume": 1.0, "price_pnl": 10.0, "profit_ticks": 40.0, "pnl": 200.0,
        "comment": "", "source_file": source_file,
    }
    rec["dedupe_key"] = source_file + "|" + day  # unique per (file, day)
    return rec


def _setup(tmp: Path):
    db_path = tmp / "test.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    # Two attempts of the same day: first take (no suffix) + re-done take (-02).
    db.insert_journal(conn, [
        _journal_row("ATAS_statistics_13062026_14062026.xlsx", "2026-06-13"),
        _journal_row("ATAS_statistics_13062026_14062026-02.xlsx", "2026-06-13"),
        # A different day, first take.
        _journal_row("ATAS_statistics_03042024_04042024.xlsx", "2024-04-03"),
    ])
    # Point deps at this temp connection (resolve_scope/endpoints read it).
    deps._conn = conn
    rec_dir = tmp / "recordings"
    rec_dir.mkdir()
    db.save_setting(conn, "recordings_folder", str(rec_dir))
    return conn, rec_dir


def _scope():
    # ATAS view needs no executions; default tz. resolve_scope reads deps._conn.
    # Pass every arg explicitly — the defaults are FastAPI Query markers.
    return resolve_scope(
        view="atas", instruments=None, accounts=None,
        start=None, end=None, tags=None, tz=None,
    )


def test_scan_links_matching_and_reports():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn, rec_dir = _setup(tmp)
        # Recordings present: attempt 1 of 2026-06-13, and 2024-04-03. The -02
        # take of 2026-06-13 has NO file on disk.
        (rec_dir / "13-JUN-2026-01.mp4").write_bytes(b"x")
        (rec_dir / "03-APR-2024-01.mp4").write_bytes(b"x")

        res = videos.scan_recordings(_scope())
        names = {r["filename"] for r in res["linked"]}
        assert res["count"] == 2, res
        assert names == {"13-JUN-2026-01.mp4", "03-APR-2024-01.mp4"}, names
        # The -02 attempt had no matching file -> not linked.
        linked_sf = db.linked_video_source_files(conn)
        assert "ATAS_statistics_13062026_14062026-02.xlsx" not in linked_sf
        # Linked rows actually persisted with the resolved path.
        v = db.get_attempt_video(conn, "ATAS_statistics_13062026_14062026.xlsx")
        assert v and v["path"].endswith("13-JUN-2026-01.mp4")


def test_scan_skips_already_linked():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn, rec_dir = _setup(tmp)
        (rec_dir / "13-JUN-2026-01.mp4").write_bytes(b"x")
        # Pre-link this attempt to a DIFFERENT (manual) path; scan must not touch it.
        db.save_attempt_video(conn, "ATAS_statistics_13062026_14062026.xlsx", "C:/manual.mp4")
        res = videos.scan_recordings(_scope())
        assert all(
            r["source_file"] != "ATAS_statistics_13062026_14062026.xlsx"
            for r in res["linked"]
        ), res
        v = db.get_attempt_video(conn, "ATAS_statistics_13062026_14062026.xlsx")
        assert v["path"] == "C:/manual.mp4"  # untouched


def test_scan_rejects_unset_or_bad_folder():
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn, _ = _setup(tmp)
        db.save_setting(conn, "recordings_folder", "")
        try:
            videos.scan_recordings(_scope())
            raise AssertionError("expected HTTPException for empty folder")
        except HTTPException as e:
            assert e.status_code == 400

        db.save_setting(conn, "recordings_folder", str(tmp / "does-not-exist"))
        try:
            videos.scan_recordings(_scope())
            raise AssertionError("expected HTTPException for missing folder")
        except HTTPException as e:
            assert e.status_code == 400


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
