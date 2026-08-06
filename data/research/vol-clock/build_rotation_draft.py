"""Build the Portfolio-B rotation trade table for the Drafts viewer.

Portfolio B from the vol-clock §9 re-cut: UB (v13 a348d176) trades on w60-QUIET
sessions + DTF v2 (523f4000, entry-reason config) trades on MID/HOT sessions.
Day-level selection is an exact re-simulation (sessions carry no cross-day
engine state), so these are the engine's own fills verbatim — the draft only
lays them on charts. Rebuild by re-running this script if either baseline or
daily_atr.parquet moves.
"""
import pandas as pd

ROOT = "/home/afahmi/repos/atas_journal"
OUT = f"{ROOT}/data/research/vol-clock/rotation_b_trades.parquet"

atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["session"] = pd.to_datetime(atr["session"]).dt.date.astype(str)
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
lab = atr.set_index("session")["terc"]

ARMS = {
    "UB": ("data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176", ["quiet"]),
    "DTF": ("data/sims/drift-touch-fade/20250203-20260630-v2-523f4000", ["mid", "hot"]),
}

COLS = ["direction", "entry_ts_utc", "exit_ts_utc", "avg_entry", "avg_exit",
        "stop_price", "target_price", "exit_reason", "points", "r_multiple",
        "net_pnl", "duration_s", "band_width_ticks"]

parts = []
for name, (path, terciles) in ARMS.items():
    t = pd.read_parquet(f"{ROOT}/{path}/trades.parquet")
    t["session"] = t["session"].astype(str)
    t["terc"] = t["session"].map(lab)
    kept = t[t["terc"].isin(terciles)].copy()
    kept["strategy"] = f"{name} ({'+'.join(terciles)})"
    parts.append(kept[["session", "strategy", "terc"] + COLS])
    print(f"{name}: {len(kept)}/{len(t)} trades kept on {terciles} days, "
          f"net ${kept.net_pnl.sum():,.0f}")

out = pd.concat(parts).rename(columns={"session": "day"})
out["terc"] = out["terc"].astype(str)
out = out.sort_values("entry_ts_utc").reset_index(drop=True)
out.to_parquet(OUT, index=False)
print(f"wrote {len(out)} trades -> {OUT}")
