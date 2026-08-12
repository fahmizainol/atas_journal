"""The ATR line vs the one-number day range — same tape, two readouts.

The question this page answers: *is the ATR indicator on the chart the same
thing as the "median 5-min range" the vol-scaled sizing rule runs on, and if
not, which one do I read?* Five sessions are drawn, chosen because each one is
a chapter of the argument (the anchor day, the two "felt nice"/"felt tight"
days that measured backwards, the hottest recent tape, the most recent quiet
day). Each tab shows the 5-minute candles with the ATR(14) line below —
exactly the indicator as charted — and, laid over that same pane as flat
reference lines: today's one-number median range (hindsight), yesterday's
(the causal number the sizing rule actually uses), and the current 50-tick
stop. The wiggle of the blue line against the stillness of the flat ones *is*
the comparison.

The sizing rule being illustrated (not adopted — needs the survivability
re-sim before it is traded):

    V     = yesterday's 10:00-16:00 median 5-min range / 70 ticks
    stop  = 50 ticks x V, target and trail scale by the same V
    size  = floor($500 / stop_ticks) MNQ  (= $250 risk at $0.50/tick),
            or 1 NQ when the stop stays ~50t

Reads the tick cache and falls back to the live recorder's store (July/August
days live only there until an import runs) — never fetches, so the page costs
nothing at Databento.

    uv run python demo/atr_vs_r5_demo.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.atr import atr_series  # noqa: E402
from journal.config import ET_TZ  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
BAR_MINS = (5, 1)        # resolutions the page can flip between; 5 is the rule's
BAR_MIN = 5              # calibrated scale, 1 is what a scalper's eye watches
ATR_PERIOD = 14

R5_REF = 70          # ticks — the pre-2026 tape where a 50t stop felt right
STOP_BASE = 50       # ticks, at V = 1
RISK_USD = 250.0     # held constant; size absorbs the stop width
NQ_TICK = 5.0        # $/tick
MNQ_TICK = 0.5

# (prior session, session, how it felt at the time) — the prior day is what
# the causal rule reads; the session is what it then had to survive.
DAYS: list[tuple[date, date, str, str]] = [
    (date(2025, 10, 3), date(2025, 10, 6), "the anchor",
     "50t felt right here — this tape calibrates V = 1"),
    (date(2025, 3, 12), date(2025, 3, 13), "felt fine",
     "remembered as roomy, measured at ~2x the noise of the day that felt tight"),
    (date(2026, 6, 29), date(2026, 6, 30), "felt tight",
     "the 100t-candle day — actually one of the quietest days of the summer by 5-min noise; the range was directional"),
    (date(2026, 7, 28), date(2026, 7, 29), "hottest recent",
     "the top of the July tape — what the rule does when vol is 4x the anchor"),
    (date(2026, 8, 7), date(2026, 8, 10), "recent quiet",
     "the quietest full day since January — the rule walks most of the way back"),
]

HTML_OUT = ROOT / "docs" / "research" / "atr-vs-r5.html"
OUT_DIR = Path(__file__).resolve().parent
LWC_JS = ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / (
    "lightweight-charts.standalone.production.js"
)
LWC_CDN = "https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"


# --- Session assembly -----------------------------------------------------
def contract_on(day: date) -> str | None:
    sym = tickmod.contract_for_cached(INSTRUMENT, day)
    if sym and tickmod.cached_rth(sym, day) is not None:
        return sym
    for p in (tickmod.TICK_CACHE_DIR).glob(f"*_{day:%Y-%m-%d}_day.parquet"):
        return p.stem.split("_")[0]
    for p in (ROOT / "data" / "live" / "ticks").glob("*"):
        if (p / f"{day:%Y-%m-%d}").is_dir():
            return p.name
    return None


def bars_from(ticks: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """OHLC bars on an ET clock, with the ET-shifted epoch LWC will plot."""
    df = ticks.sort_values("ts_utc")
    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    key = et.dt.floor(f"{minutes}min")
    g = df.groupby(key, sort=True)
    bars = g.agg(open=("price", "first"), high=("price", "max"),
                 low=("price", "min"), close=("price", "last")).reset_index(names="et")
    bars["t"] = bars["et"].dt.tz_localize(None).astype("int64") // 10**9
    return bars


def median_range(bars: pd.DataFrame) -> float:
    """The one number: median bar range in ticks, 10:00-16:00 ET.

    Post-open on purpose — the 09:30 half-hour runs 2-4x the rest of the day
    everywhere and would drag a mean; the median over the settled session is
    the number the ladder was calibrated on.
    """
    tod = bars["et"].dt.time
    w = bars[(tod >= pd.Timestamp("10:00").time()) & (tod < pd.Timestamp("16:00").time())]
    return float(((w["high"] - w["low"]) * 4).median())


def sizing(prior_r5: float) -> dict:
    v = prior_r5 / R5_REF
    stop = max(STOP_BASE, int(round(STOP_BASE * v / 5)) * 5)
    if stop <= 55:
        inst, qty, tick = "NQ", 1, NQ_TICK    # 10 MNQ == 1 NQ and the mini is cheaper
    else:
        inst, tick = "MNQ", MNQ_TICK
        qty = min(10, int(RISK_USD // (stop * tick)))
    return {"v": round(v, 2), "stop": stop, "inst": inst, "qty": qty,
            "risk": round(stop * tick * qty)}


def build_resolution(context: pd.DataFrame, rth: pd.DataFrame,
                     prior_rth: pd.DataFrame, minutes: int) -> dict:
    """Candles + ATR line + the flat references, all at one bar size."""
    cbars = bars_from(context, minutes)
    atr_ticks = atr_series(cbars, ATR_PERIOD) * 4

    rth_bars = bars_from(rth, minutes)
    rth_t = set(rth_bars["t"].tolist())
    atr_line = [
        {"time": int(t), "value": round(float(a), 1)}
        for t, a in zip(cbars["t"], atr_ticks)
        if int(t) in rth_t and pd.notna(a)
    ]

    day_r = median_range(rth_bars)
    prior_r = median_range(bars_from(prior_rth, minutes))
    settled = [p["value"] for p in atr_line
               if datetime.fromtimestamp(p["time"]).time() >= pd.Timestamp("10:00").time()]
    s_atr = pd.Series(settled)

    # Today's median as it develops — the expanding median of bar ranges since
    # 10:00, i.e. what "today's number" read at each moment. Causal by
    # construction, and it converges to the flat hindsight line by definition.
    w = rth_bars[rth_bars["et"].dt.time >= pd.Timestamp("10:00").time()]
    dev = ((w["high"] - w["low"]) * 4).expanding(min_periods=3).median()
    dev_line = [
        {"time": int(t), "value": round(float(v), 1)}
        for t, v in zip(w["t"], dev) if pd.notna(v)
    ]

    return {
        "dev": dev_line,
        "candles": [
            {"time": int(r.t), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
            for r in rth_bars.itertuples()
        ],
        "atr": atr_line,
        "day_r": round(day_r), "prior_r": round(prior_r),
        "atr_min": round(float(s_atr.min())), "atr_max": round(float(s_atr.max())),
        "atr_med": round(float(s_atr.median())),
        "cover50": round(50 / day_r, 2),
    }


def build_session(prior_day: date, day: date, feel: str, why: str) -> dict | None:
    sym, prior_sym = contract_on(day), contract_on(prior_day)
    if not sym or not prior_sym:
        return None
    rth = tickmod.cached_rth(sym, day)
    prior_rth = tickmod.cached_rth(prior_sym, prior_day)
    if rth is None or prior_rth is None:
        return None
    on = tickmod.cached_overnight(sym, day)

    # ATR runs through the overnight so Wilder is warm at 09:30 — otherwise
    # the first hour-odd of RTH has no number, same reasoning as the Pulcini
    # demo. Only the RTH slice of the line is drawn.
    context = pd.concat([on, rth]) if on is not None and not on.empty else rth

    res = {str(m): build_resolution(context, rth, prior_rth, m) for m in BAR_MINS}

    # The rule itself is quoted from the 5-minute medians whatever the chart
    # shows — V's 70t reference is a 5-minute number and doesn't transfer.
    five = res[str(BAR_MIN)]
    rule = sizing(five["prior_r"])

    return {
        "date": day.isoformat(), "prior_date": prior_day.isoformat(),
        "contract": sym, "feel": feel, "why": why,
        "level": round(float(rth["price"].median())),
        "res": res,
        "day_r5": five["day_r"], "prior_r5": five["prior_r"],
        "rule": rule,
        "cover_rule": round(rule["stop"] / five["day_r"], 2),
    }


def _chart_lib() -> str:
    if LWC_JS.exists():
        return LWC_JS.read_text()
    print(f"! {LWC_JS.name} not found — falling back to the CDN (page needs network)")
    return f'</script><script src="{LWC_CDN}">'


def main() -> None:
    sessions = []
    for prior, day, feel, why in DAYS:
        print(f"[{day}] reading ticks (prior {prior}) …")
        s = build_session(prior, day, feel, why)
        if s is None:
            print(f"  ! no ticks for {day} or {prior} — skipped")
            continue
        sessions.append(s)
    if not sessions:
        raise SystemExit("nothing to draw")

    template = (OUT_DIR / "_atr_vs_r5_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(
        template
        .replace("__LWC_JS__", _chart_lib())
        .replace("__SESSIONS_JSON__", json.dumps(sessions))
        .replace("__R5_REF__", str(R5_REF))
        .replace("__STOP_BASE__", str(STOP_BASE))
        .replace("__RISK_USD__", f"{RISK_USD:.0f}")
        .replace("__ATR_PERIOD__", str(ATR_PERIOD))
        .replace("__BAR_MIN__", str(BAR_MIN))
    )

    print(f"\n{'day':>12} {'feel':>14} {'y-r5':>5} {'V':>5} {'rule':>14} "
          f"{'d-r5':>5} {'d-r1':>5} {'50t/5m':>6} {'50t/1m':>6} {'rule/5m':>7}")
    for s in sessions:
        r, f5, f1 = s["rule"], s["res"]["5"], s["res"]["1"]
        print(f"{s['date']:>12} {s['feel']:>14} {s['prior_r5']:>4}t {r['v']:>5.2f} "
              f"{r['stop']:>4}t x{r['qty']} {r['inst']:<4} {f5['day_r']:>4}t {f1['day_r']:>4}t "
              f"{f5['cover50']:>6.2f} {f1['cover50']:>6.2f} {s['cover_rule']:>7.2f}")
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


if __name__ == "__main__":
    main()
