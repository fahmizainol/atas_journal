"""A/B the ATR-scaled trail on vwap-upper-band-bounce, one arm per invocation.

Arm 0 is the v15 twin of the pinned v14 baseline (identical knobs) — it exists to
prove the version bump changed nothing before any arm is believed. The rest set
the trail's distance from the daily ATR the session opened with (trail_atr_mult),
with a 0 step so the ratchet's grid scales with the distance instead of pinning
to a tick count the distance no longer matches.

    SIM_WORKERS=11 .venv/bin/python data/research/atr-trail/run_ab.py <arm-index>

One arm per process, in the foreground: a run parked in the background is reaped
when the agent session tears down, and a half-finished run leaves its folder
stuck in 'running'. The __main__ guard is load-bearing too — the session pool
re-imports this module in every worker.
"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal.sim import registry, runner, schema, store  # noqa: E402

SLUG = "vwap-upper-band-bounce"
# The pinned v14 baseline this study is measured against: 401 trades, $152,923
# net, PF 1.58 over 2024-03-03..2026-06-30.
BASE_RUN = "20240303-20260630-v14-ff7e84f8"
BASE_DIR = ROOT / "data" / "sims" / SLUG / BASE_RUN
RESULTS = Path(__file__).with_name("ab_results.json")

# 0.05 is the vol-neutral setting: NQ's daily ATR ran a 390-point median over
# this window, so 0.05 lands a median 78-tick trail against the baseline's fixed
# 75 — the arm changes how the distance is ALLOCATED across days, not its
# average size. 0.04 and 0.065 bracket it.
ARMS = [
    ("twin (v15, fixed 75t)", {}),
    ("atr 0.04", {"trail_step_ticks": 0, "trail_atr_mult": 0.04}),
    ("atr 0.05 (vol-neutral)", {"trail_step_ticks": 0, "trail_atr_mult": 0.05}),
    ("atr 0.065", {"trail_step_ticks": 0, "trail_atr_mult": 0.065}),
]


def main() -> None:
    i = int(sys.argv[1])
    label, over = ARMS[i]
    strat = registry.get(SLUG)
    base = schema.parse(json.loads((BASE_DIR / "config.json").read_text()))
    cfg = replace(base, **over) if over else base

    t0 = time.time()
    rid = runner.execute(strat, cfg)
    st = json.loads((store.SIMS_DIR / SLUG / rid / "state.json").read_text())
    if st.get("status") != "done":
        print(f"{label}: FAILED — {st.get('error')}", flush=True)
        sys.exit(1)

    store.write_meta(SLUG, rid, label=f"ATR trail A/B — {label}",
                     notes="2026-08-08 ATR-scaled trail study; the twin arm is the "
                           "v15 re-run of the v14 baseline ff7e84f8.")
    m = json.loads((store.SIMS_DIR / SLUG / rid / "metrics.json").read_text())
    print(f"{label:26s} {rid}  {time.time()-t0:6.0f}s  "
          f"trades={m['trades']:4d} net=${m['net_pnl']:>10,.0f} "
          f"PF={m['profit_factor']:.2f} win={m['win_rate']:.0f}% "
          f"DD=${m['max_drawdown']:>9,.0f} sharpe={m['sharpe']:.2f} "
          f"exits={m['exit_reasons']}", flush=True)

    out = json.loads(RESULTS.read_text()) if RESULTS.exists() else []
    out = [o for o in out if o["label"] != label]
    out.append({"label": label, "run_id": rid, "metrics": m})
    out.sort(key=lambda o: [a[0] for a in ARMS].index(o["label"]))
    RESULTS.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
