"""A/B ladder for the σ-normalised acceptance floor (min_acceptance_sigma).

Re-runs the adopted a348d176 config on the current engine as the baseline (the
new field defaults to 0.0 = off, so the baseline must reproduce a348d176's
$150,439 / 262 trades byte-for-byte), then each σ-floor variant. The research
(docs/research/ny-band-ema920.md §5.1) points to a LOW floor (~0.10-0.15σ) that
vetoes shallow acceptance on wide-band days; higher floors gut the setup.

Sequential on purpose: the runner parallelizes across sessions internally. The
__main__ guard is load-bearing — forkserver workers re-import this as __main__.

    Usage: .venv/bin/python data/research/market-structure/nyema_ab.py
"""
import sys, json, time
sys.path.insert(0, "src")
from journal.sim import store, registry, runner

BASE_CFG = ("data/sims/vwap-upper-band-bounce/"
            "20250201-20260630-v13-a348d176/config.json")

LADDER = [
    ("baseline",  0.00),
    ("sigma-0.10", 0.10),
    ("sigma-0.15", 0.15),
    ("sigma-0.20", 0.20),
]


def main():
    base = json.load(open(BASE_CFG))
    strat = registry.get("vwap-upper-band-bounce")
    rows = []
    for label, sig in LADDER:
        d = json.loads(json.dumps(base))
        d["min_acceptance_sigma"] = sig
        cfg = store.config_from_json(d)
        rid = store.run_id(cfg, strat.version)
        if store.read_run(strat.slug, rid) is None:
            store.delete_run(strat.slug, rid)   # clear any partial dir
            t0 = time.time()
            print(f"RUN  {label:<11} {rid} ...", flush=True)
            runner.execute(strat, cfg)
            print(f"     done in {time.time()-t0:.0f}s", flush=True)
        else:
            print(f"SKIP {label:<11} {rid} (exists)", flush=True)
        m = json.load(open(f"data/sims/{strat.slug}/{rid}/metrics.json"))
        rows.append((label, sig, rid, m))

    b = rows[0][3]
    print(f"\n{'variant':<11} {'σ':>5} {'trades':>7} {'net':>10} {'Δnet':>8} "
          f"{'win%':>6} {'PF':>6} {'maxDD':>10} {'Sharpe':>7} {'Sortino':>8} {'exp$':>7}")
    for label, sig, rid, m in rows:
        dn = m["net_pnl"] - b["net_pnl"]
        print(f"{label:<11} {sig:>5.2f} {m['trades']:>7} {m['net_pnl']:>10,.0f} "
              f"{dn:>+8,.0f} {m['win_rate']:>6.1f} {m['profit_factor']:>6.2f} "
              f"{m['max_drawdown']:>10,.0f} {m['sharpe']:>7.2f} {m['sortino']:>8.2f} "
              f"{m['expectancy']:>7,.0f}")

    print("\n(baseline must match a348d176: 262 trades / net $150,439 / PF 2.05 / Sharpe 3.04)")


if __name__ == "__main__":
    main()
