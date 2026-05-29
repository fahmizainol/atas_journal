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

# Continuous-contract roll rule for front-month resolution:
# "v" volume, "c" calendar, "n" open interest.
CONTINUOUS_ROLL_RULE = "v"

# AI analyzer defaults. Reasoning models ("thinking" variants) spend part of this
# budget on hidden reasoning tokens before emitting the answer, so the default is
# generous; override with LLM_MAX_TOKENS when a model needs more or less headroom.
LLM_MAX_TOKENS_DEFAULT = 4000


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def databento_key() -> str | None:
    load_env()
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE":
        return None
    return key


def _models_env() -> list[str]:
    """Parse the comma-separated LLM_MODELS list (may be empty)."""
    load_env()
    raw = os.environ.get("LLM_MODELS", "").strip()
    return [m.strip() for m in raw.split(",") if m.strip() and m.strip() != "YOUR_MODEL_HERE"]


def llm_model() -> str | None:
    """Default LiteLLM model: LLM_MODEL if set, else the first of LLM_MODELS."""
    load_env()
    model = os.environ.get("LLM_MODEL", "").strip()
    if model and model != "YOUR_MODEL_HERE":
        return model
    env = _models_env()
    return env[0] if env else None


def llm_models() -> list[str]:
    """All selectable models for multi-model reviews (default first, de-duped)."""
    out: list[str] = []
    default = llm_model()
    if default:
        out.append(default)
    for m in _models_env():
        if m not in out:
            out.append(m)
    return out


def llm_available() -> bool:
    return bool(llm_models())


def llm_max_tokens() -> int:
    """Completion token budget; override with LLM_MAX_TOKENS in the env."""
    load_env()
    raw = os.environ.get("LLM_MAX_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return LLM_MAX_TOKENS_DEFAULT


def ai_debug_prompts() -> bool:
    """When AI_DEBUG_PROMPTS is truthy, log the full prompt sent to the model."""
    load_env()
    return os.environ.get("AI_DEBUG_PROMPTS", "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_instrument(instrument: str) -> str:
    """`#NQM6@CME` -> `NQM6@CME` (strip the leading `#` ATAS adds inconsistently).

    Keeps the `@VENUE` suffix; only collapses the prefix so the same contract
    isn't split into two instruments across exports.
    """
    return instrument.strip().lstrip("#")


def raw_symbol(instrument: str) -> str:
    """`NQM6@CME` -> `NQM6` (strip @VENUE suffix and any leading `#`).

    ATAS prefixes some contract symbols with `#` (e.g. `#NQM6@CME`), which
    Databento cannot resolve.
    """
    return instrument.split("@", 1)[0].strip().lstrip("#")


def root_symbol(instrument: str) -> str:
    """`NQM6@CME` -> `NQ` (strip month/year code and @VENUE)."""
    sym = raw_symbol(instrument)
    # Futures code: ROOT + month letter + 1-2 year digits, e.g. NQM6 / ESH26.
    m = re.match(r"^([A-Z]+?)([FGHJKMNQUVXZ])(\d{1,2})$", sym)
    if m:
        return m.group(1)
    return sym


def continuous_symbol(instrument: str) -> str:
    """Front-month continuous symbol for Databento, e.g. 'NQ' -> 'NQ.v.0'."""
    return f"{root_symbol(instrument)}.{CONTINUOUS_ROLL_RULE}.0"


def contract_spec(instrument: str) -> dict[str, float]:
    return CONTRACT_SPECS.get(root_symbol(instrument), DEFAULT_SPEC)


def point_value(instrument: str) -> float:
    return contract_spec(instrument)["point_value"]


def tick_size(instrument: str) -> float:
    return contract_spec(instrument)["tick_size"]
