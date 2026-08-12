"""Cut the ATR-trail A/B arms by the vol clock.

The headline says whether an arm won. This says whether it moved money for the
reason the idea claims — a wider trail paying on hot days and a tighter one on
quiet ones — or whether it just spread the same distance around at random.

    .venv/bin/python data/research/atr-trail/analyze.py
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from journal.sim import vol_regime as volmod  # noqa: E402

SIMS = ROOT / "data" / "sims" / "vwap-upper-band-bounce"
RESULTS = Path(__file__).with_name("ab_results.json")
START, END = date(2024, 3, 3), date(2026, 6, 30)
TICK = 0.25
ORDER = ["quiet", "mid", "hot"]


def main() -> None:
    days = pd.DataFrame(volmod.range_labels("NQ", START, END)["days"]).dropna(
        subset=["atr", "label"])
    lab = days.set_index("date")
    arms = json.loads(RESULTS.read_text())

    print("=== headline ===")
    print(f"{'arm':26s} {'trades':>6s} {'net':>11s} {'PF':>5s} {'win%':>5s} "
          f"{'maxDD':>10s} {'sharpe':>6s} {'avgWin':>8s} {'avgLoss':>8s}")
    for a in arms:
        m = a["metrics"]
        print(f"{a['label']:26s} {m['trades']:6d} {m['net_pnl']:11,.0f} "
              f"{m['profit_factor']:5.2f} {m['win_rate']:5.1f} "
              f"{m['max_drawdown']:10,.0f} {m['sharpe']:6.2f} "
              f"{m['avg_win']:8,.0f} {m['avg_loss']:8,.0f}")

    print("\n=== net $ / trades / PF, by vol-clock regime ===")
    rows = {}
    for a in arms:
        t = pd.read_parquet(SIMS / a["run_id"] / "trades.parquet")
        t["session"] = pd.to_datetime(t["entry_ts_utc"], utc=True).dt.tz_convert(
            "America/New_York").dt.date.astype(str)
        t = t.join(lab, on="session")
        out = {}
        for name, g in t.groupby("label", observed=True):
            gp = g.loc[g["net_pnl"] > 0, "net_pnl"].sum()
            gl = -g.loc[g["net_pnl"] < 0, "net_pnl"].sum()
            out[name] = (g["net_pnl"].sum(), len(g), gp / gl if gl else float("inf"))
        rows[a["label"]] = out
    print(f"{'arm':26s} " + "  ".join(f"{k:^24s}" for k in ORDER))
    for label, out in rows.items():
        cells = []
        for k in ORDER:
            if k in out:
                n, c, pf = out[k]
                cells.append(f"{n:>11,.0f} /{c:4d} /{pf:5.2f}")
            else:
                cells.append(" " * 24)
        print(f"{label:26s} " + "  ".join(cells))

    print("\n=== the distance each multiplier actually trails at (ticks) ===")
    print(f"{'mult':>6s}  " + "  ".join(f"{k:^22s}" for k in ORDER))
    for mult in (0.04, 0.05, 0.065):
        d = days.assign(t=(mult * days["atr"] / TICK).round())
        by = d.groupby("label", observed=True)["t"].agg(["min", "median", "max"])
        cells = [f"{int(by.loc[k,'min']):3d}-{int(by.loc[k,'max']):3d} (med "
                 f"{int(by.loc[k,'median']):3d})" if k in by.index else " " * 22
                 for k in ORDER]
        print(f"{mult:6.3f}  " + "  ".join(f"{c:^22s}" for c in cells))
    print("(the baseline trails at a flat 75 on every one of them)")

    print("\n=== how far the trailed exits actually gave back ===")
    for a in arms:
        t = pd.read_parquet(SIMS / a["run_id"] / "trades.parquet")
        tr = t[t["exit_reason"] == "trail"]
        if tr.empty:
            continue
        print(f"{a['label']:26s} trailed={len(tr):4d} "
              f"net=${tr['net_pnl'].sum():>10,.0f} "
              f"avg=${tr['net_pnl'].mean():>7,.0f} "
              f"median R={tr['r_multiple'].median():5.2f} "
              f"scratches={int((tr['net_pnl'] <= 0).sum()):4d}")


if __name__ == "__main__":
    main()
