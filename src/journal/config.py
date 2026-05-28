"""Paths, timezones, contract specs, and environment loading."""

from __future__ import annotations

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# --- Paths ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
IMPORTS_DIR = DATA_DIR / "imports"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "journal.db"

for _d in (IMPORTS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Timezones -----------------------------------------------------------
# ATAS Journal/Executions timestamps are local Asia/Kuala_Lumpur (UTC+8).
# The Statistics sheet is already UTC. Databento queries are in UTC.
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")
UTC_TZ = ZoneInfo("UTC")
ET_TZ = ZoneInfo("America/New_York")  # CME session reference

# User-selectable display zones for the UI. New York is DST-aware
# (EDT = UTC-4 in summer, EST = UTC-5 in winter), unlike a fixed offset.
DISPLAY_TZS: dict[str, ZoneInfo] = {
    "New York": ET_TZ,
    "Kuala Lumpur": KL_TZ,
}
DEFAULT_DISPLAY_TZ = "New York"

# --- Contract specifications --------------------------------------------
# point_value = dollars per full index point; tick_size in points.
CONTRACT_SPECS: dict[str, dict[str, float]] = {
    "NQ": {"point_value": 20.0, "tick_size": 0.25},  # $5/tick
    "ES": {"point_value": 50.0, "tick_size": 0.25},
    "MNQ": {"point_value": 2.0, "tick_size": 0.25},
    "MES": {"point_value": 5.0, "tick_size": 0.25},
}
DEFAULT_SPEC = {"point_value": 20.0, "tick_size": 0.25}

# Databento dataset for CME Globex futures.
DATABENTO_DATASET = "GLBX.MDP3"


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def databento_key() -> str | None:
    load_env()
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE":
        return None
    return key


def raw_symbol(instrument: str) -> str:
    """`NQM6@CME` -> `NQM6` (strip the @VENUE suffix)."""
    return instrument.split("@", 1)[0].strip()


def root_symbol(instrument: str) -> str:
    """`NQM6@CME` -> `NQ` (strip month/year code and @VENUE)."""
    sym = raw_symbol(instrument)
    # Futures code: ROOT + month letter + 1-2 year digits, e.g. NQM6 / ESH26.
    m = re.match(r"^([A-Z]+?)([FGHJKMNQUVXZ])(\d{1,2})$", sym)
    if m:
        return m.group(1)
    return sym


def contract_spec(instrument: str) -> dict[str, float]:
    return CONTRACT_SPECS.get(root_symbol(instrument), DEFAULT_SPEC)


def point_value(instrument: str) -> float:
    return contract_spec(instrument)["point_value"]


def tick_size(instrument: str) -> float:
    return contract_spec(instrument)["tick_size"]
