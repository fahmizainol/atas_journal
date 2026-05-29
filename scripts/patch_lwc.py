"""Patch the vendored streamlit-lightweight-charts-pro frontend bundle.

The trade-rectangle tooltip in the compiled JS bundle hardcodes its P&L as
``price2 - price1`` (raw price points) and ignores the real ``pnl`` we pass from
Python. For futures that shows points instead of dollars (e.g. 17.25 instead of
$345 on NQ at $20/pt), and because price1/price2 are stored as min/max it always
renders a positive number even for losing trades.

There is no Python-level hook for this (the rectangle primitive is built without
tooltip options, so the library's ``tooltip_template`` never applies), so we
rewrite the bundle's else-branch to prefer the real ``pnl`` / ``pnlPercentage``
with the original computation kept as a fallback.

This patch lives in site-packages, which is wiped on every reinstall, so the fix
is applied idempotently at app startup via ``ensure_patched()`` and can also be
run manually:

    uv run python scripts/patch_lwc.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_OLD = (
    "const t=this._data.price2-this._data.price1,"
    "e=(t/this._data.price1*100).toFixed(2),"
    "i=this._data.isProfitable"
)
_NEW = (
    "const t=this._data.pnl??(this._data.price2-this._data.price1),"
    "e=(this._data.pnlPercentage??"
    "((this._data.price2-this._data.price1)/this._data.price1*100)).toFixed(2),"
    "i=this._data.isProfitable"
)
# Substring unique to the patched bundle, used to detect an already-applied fix.
_MARKER = "const t=this._data.pnl??(this._data.price2-this._data.price1)"


def _bundle_dir() -> Path | None:
    spec = importlib.util.find_spec("streamlit_lightweight_charts_pro")
    if spec is None or not spec.submodule_search_locations:
        return None
    pkg = Path(next(iter(spec.submodule_search_locations)))
    js_dir = pkg / "frontend" / "build" / "static" / "js"
    return js_dir if js_dir.is_dir() else None


def ensure_patched() -> str:
    """Apply the tooltip fix if needed. Returns a short status string.

    Safe and cheap to call repeatedly: it only writes when the unpatched marker
    is found, and silently no-ops if the package or bundle layout is missing.
    """
    js_dir = _bundle_dir()
    if js_dir is None:
        return "skipped: bundle not found"

    for js in js_dir.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        if _MARKER in text:
            return "already patched"
        if _OLD in text:
            js.write_text(text.replace(_OLD, _NEW), encoding="utf-8")
            return f"patched {js.name}"
    return "skipped: target code not found (library version changed?)"


if __name__ == "__main__":
    print(ensure_patched())
