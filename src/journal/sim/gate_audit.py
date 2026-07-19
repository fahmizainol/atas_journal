"""Gate-robustness audit: is each of a run's confluences a real edge or luck?

The request-time port of ``data/research/gate-robustness/eval_scorecard.py``
(doc: ``docs/research/gate-robustness.md``). For the run being viewed, every
gate in its stack is scored on the study's test ladder:

  marginal      — this run vs the same config with the gate *deleted* (a real
                  engine re-run, re-arm chains intact — never subtraction)
  months        — per-month sign test on the marginal (exact binomial)
  bootstrap     — month-block bootstrap CI of the gate's total contribution
  tail          — does the marginal survive removing each run's top-20 trades
                  and winsorizing at the pooled p95? (top-20 ≈ 78% of net)
  selection     — kept book vs random same-size subsets of kept ∪ unique-ghosts
  cohort        — kept vs unique-ghost R distributions (Mann-Whitney / AUC)
  neighborhood  — parameter neighbors: plateau (edge exists across the range)
                  vs spike (only the exact cutoff wins)
  halves        — marginal sign agreement across the window's two halves

Variant runs are resolved by config hash — mutate this run's config, hash it —
so the audit needs no registry of ladder runs and follows any re-pin. Variants
that were never run are reported with their ready-to-POST config; the ghost
frame tests (selection/cohort) need only this run's own vetoed.parquet, so the
panel is never empty.

Two standing caveats baked into the output: ghost *dollars* invert the verdict
for composition gates (``mirage`` flag — twice-confirmed on gx_overhang), and
per-month significance is unreachable at n≈260 even for real gates, so the
verdict rules weigh selection quality and tail robustness instead.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from . import store

N_PERM = 10_000
_SEED = 7  # fixed: the audit of an immutable run set must be reproducible


# --- variant configs --------------------------------------------------------

def neighbor_params(gate: str, params: dict) -> list[tuple[str, dict]]:
    """The gate's parameter neighbors, derived from its *pinned* values so the
    audit follows whatever config it is looking at. One step either side of the
    threshold that defines the gate; unknown gates get no neighborhood (their
    off-run marginal still scores)."""
    def patch(**kw) -> dict:
        p = dict(params)
        p.update(kw)
        return p

    if gate == "regime":
        b = params.get("bbr_max", 0.35)
        return [(f"bbr≤{b - 0.05:.2f}", patch(bbr_max=round(b - 0.05, 2))),
                (f"bbr≤{b + 0.05:.2f}", patch(bbr_max=round(b + 0.05, 2)))]
    if gate == "chop":
        m = params.get("max_overlap", 0.65)
        return [(f"≤{m - 0.05:.2f}", patch(max_overlap=round(m - 0.05, 2))),
                (f"≤{m + 0.05:.2f}", patch(max_overlap=round(m + 0.05, 2)))]
    if gate == "gx_overhang":
        t = int(params.get("max_ticks", 50))
        return [(f"{t - 10}t", patch(max_ticks=t - 10)),
                (f"{t + 10}t", patch(max_ticks=t + 10))]
    if gate == "gx_poc_shape":
        z = int(params.get("zone_max_ticks", 100))
        return [(f"zone≤{z - 25}t", patch(zone_max_ticks=z - 25)),
                (f"zone≤{z + 25}t", patch(zone_max_ticks=z + 25))]
    return []


def _variant_config(base_cfg: dict, gate: str, params: dict | None) -> dict:
    """Base config with one gate's section replaced — or deleted (params=None),
    which is the house convention for 'off' (matches how the ladder runs and
    every historical gate-off run were hashed)."""
    d = json.loads(json.dumps(base_cfg))
    if params is None:
        d["confluences"].pop(gate, None)
    else:
        d["confluences"][gate] = params
    return d


# --- statistics (numpy-only: the venv carries no scipy) ---------------------

def _binom_two_sided(k: int, n: int) -> float:
    pk = math.comb(n, k) / 2 ** n
    return float(min(1.0, sum(math.comb(n, i) / 2 ** n
                              for i in range(n + 1)
                              if math.comb(n, i) / 2 ** n <= pk + 1e-12)))


def _mannwhitney(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """U for x-vs-y and a tie-corrected normal-approximation two-sided p."""
    nx, ny = len(x), len(y)
    allv = np.concatenate([x, y])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv))
    ranks[order] = np.arange(1, len(allv) + 1)
    sv = allv[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    u = ranks[:nx].sum() - nx * (nx + 1) / 2
    mu = nx * ny / 2
    _, cnt = np.unique(allv, return_counts=True)
    tie = (cnt ** 3 - cnt).sum()
    n = nx + ny
    sigma = math.sqrt(nx * ny / 12 * ((n + 1) - tie / (n * (n - 1))))
    z = (u - mu) / sigma if sigma > 0 else 0.0
    return u, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def _monthly(t: pd.DataFrame) -> pd.Series:
    return t.groupby(t.session.dt.to_period("M")).net_pnl.sum()


def _sign_test(base_t: pd.DataFrame, off_t: pd.DataFrame) -> dict:
    b, o = _monthly(base_t), _monthly(off_t)
    idx = b.index.union(o.index)
    d = b.reindex(idx, fill_value=0) - o.reindex(idx, fill_value=0)
    d = d[d.round(2) != 0]
    if len(d) == 0:
        return {"months": 0, "better": 0, "p": 1.0}
    better = int((d > 0).sum())
    return {"months": int(len(d)), "better": better,
            "p": _binom_two_sided(better, int(len(d)))}


def _bootstrap(base_t: pd.DataFrame, off_t: pd.DataFrame, rng) -> dict:
    b, o = _monthly(base_t), _monthly(off_t)
    idx = b.index.union(o.index)
    d = (b.reindex(idx, fill_value=0) - o.reindex(idx, fill_value=0)).values
    n = len(d)
    totals = d[rng.integers(0, n, size=(N_PERM, n))].sum(axis=1)
    lo, hi = np.percentile(totals, [2.5, 97.5])
    return {"delta": float(d.sum()), "ci_lo": float(lo), "ci_hi": float(hi),
            "blocks": n}


def _ex_top20(t: pd.DataFrame) -> float:
    return float(t.net_pnl.sum() - t.net_pnl.nlargest(20).sum())


def _tail(base_t: pd.DataFrame, off_t: pd.DataFrame) -> dict:
    p95 = np.percentile(pd.concat([base_t.net_pnl, off_t.net_pnl]), 95)
    wins = lambda t: float(t.net_pnl.clip(upper=p95).sum())
    return {"d_net_ex_top20": _ex_top20(base_t) - _ex_top20(off_t),
            "d_net_winsor_p95": wins(base_t) - wins(off_t),
            "off_ex_top20": _ex_top20(off_t)}


def _halves(base_t: pd.DataFrame, off_t: pd.DataFrame, cfg: dict) -> dict:
    """Marginal per window half (calendar midpoint, not trade median, so both
    runs split at the same date regardless of composition)."""
    start = pd.Timestamp(cfg["start_date"])
    end = pd.Timestamp(cfg["end_date"])
    mid = start + (end - start) / 2
    cut = lambda t, a, b: float(t[(t.session >= a) & (t.session < b)].net_pnl.sum())
    return {"h1_label": f"{start.date()}–{mid.date()}",
            "h2_label": f"{mid.date()}–{end.date()}",
            "d_h1": cut(base_t, start, mid) - cut(off_t, start, mid),
            "d_h2": cut(base_t, mid, end + pd.Timedelta(days=1))
                    - cut(off_t, mid, end + pd.Timedelta(days=1))}


def _selection(kept_r: np.ndarray, ghost_r: np.ndarray, rng) -> dict:
    uni = np.concatenate([kept_r, ghost_r])
    k = len(kept_r)
    picks = np.argsort(rng.random((N_PERM, len(uni))), axis=1)[:, :k]
    samp = uni[picks]
    null_win, null_mr = (samp > 0).mean(axis=1), samp.mean(axis=1)
    act_win, act_mr = float((kept_r > 0).mean()), float(kept_r.mean())
    return {"n_universe": int(len(uni)), "n_kept": k,
            "win_pctile": float((null_win < act_win).mean() * 100),
            "mean_r_pctile": float((null_mr < act_mr).mean() * 100)}


def _cohort(kept: pd.DataFrame, ghosts: pd.DataFrame) -> dict:
    kept_r, ghost_r = kept.r_multiple.values, ghosts.r_multiple.values
    u, p = _mannwhitney(kept_r, ghost_r)
    return {"n_ghost": int(len(ghosts)),
            "ghost_net": float(ghosts.net_pnl.sum()),
            "auc": float(u / (len(kept_r) * len(ghost_r))),
            "p": float(p),
            "kept_stop": float((kept.exit_reason == "stop").mean()),
            "ghost_stop": float((ghosts.exit_reason == "stop").mean())}


# --- the audit --------------------------------------------------------------

def _load_variant(slug: str, rid: str) -> dict:
    """State + (if done) metrics/trades for a variant run id."""
    st = store.read_state(slug, rid)
    out = {"run_id": rid, "state": st["status"] if st else "missing",
           "metrics": None, "trades": None}
    if st and st.get("status") == "done":
        r = store.read_run(slug, rid)
        if r is not None:
            _, trades, metrics = r
            trades = trades.copy()
            trades["session"] = pd.to_datetime(trades["session"])
            out["metrics"], out["trades"] = metrics, trades
    return out


def _verdict(marginal, checks) -> str:
    if marginal is None:
        return "unscored"
    if marginal["d_net"] <= 0:
        return "fail"
    vals = list(checks.values())
    if any(v is False for v in vals):
        return "weak"
    if all(v is True for v in vals):
        return "real"
    return "partial"  # positive marginal, no failed check, variants still missing


def audit(strat, slug: str, run_id: str) -> dict | None:
    """The full scorecard for one run. None when the run has no artifact."""
    r = store.read_run(slug, run_id)
    if r is None:
        return None
    cfg, trades, metrics = r
    trades = trades.copy()
    trades["session"] = pd.to_datetime(trades["session"])
    vetoed = store.read_vetoed(slug, run_id)
    has_gates_col = len(vetoed) > 0 and "gates" in vetoed.columns
    if has_gates_col:
        vetoed = vetoed.copy()
        vetoed["session"] = pd.to_datetime(vetoed["session"])

    rng = np.random.default_rng(_SEED)
    gates_out = []
    for gate, params in (cfg.get("confluences") or {}).items():
        if isinstance(params, dict) and params.get("enabled") is False:
            continue

        # -- variants: off + neighbors, resolved by config hash
        def resolve(p):
            c = store.config_from_json(_variant_config(cfg, gate, p),
                                       strat.config_cls)
            return store.run_id(c, strat.version)

        off = _load_variant(slug, resolve(None))
        off["config"] = _variant_config(cfg, gate, None)
        neighbors = []
        for label, p in neighbor_params(gate, params if isinstance(params, dict) else {}):
            v = _load_variant(slug, resolve(p))
            v["label"] = label
            v["config"] = _variant_config(cfg, gate, p)
            if v["metrics"] is not None:
                v["net_ex_top20"] = _ex_top20(v["trades"])
            neighbors.append(v)

        # -- run-vs-off tests
        marginal = months = bootstrap = tail = halves = None
        if off["metrics"] is not None:
            om, ot = off["metrics"], off["trades"]
            marginal = {"off_trades": int(om["trades"]),
                        "off_net": om["net_pnl"], "off_pf": om["profit_factor"],
                        "off_maxdd": om["max_drawdown"], "off_sharpe": om["sharpe"],
                        "d_net": metrics["net_pnl"] - om["net_pnl"],
                        "d_pf": metrics["profit_factor"] - om["profit_factor"],
                        "d_maxdd": metrics["max_drawdown"] - om["max_drawdown"],
                        "d_sharpe": metrics["sharpe"] - om["sharpe"]}
            months = _sign_test(trades, ot)
            bootstrap = _bootstrap(trades, ot, rng)
            tail = _tail(trades, ot)
            halves = _halves(trades, ot, cfg)

        # -- ghost-frame tests (this run's ledger only; unique vetoes)
        selection = cohort = None
        if has_gates_col:
            ghosts = vetoed[vetoed.gates.astype(str) == gate]
            if len(ghosts) >= 5:
                selection = _selection(trades.r_multiple.values,
                                       ghosts.r_multiple.values, rng)
                cohort = _cohort(trades, ghosts)

        done_nb = [v for v in neighbors if v["metrics"] is not None]
        checks = {
            "tail": None if tail is None else bool(tail["d_net_ex_top20"] > 0),
            "halves": None if halves is None
                      else bool(halves["d_h1"] > 0 and halves["d_h2"] > 0),
            # One neighbor below off already proves the spike; the plateau needs
            # the full neighborhood before it counts as passed.
            "plateau": False if (off["metrics"] is not None and any(
                          v["metrics"]["net_pnl"] < off["metrics"]["net_pnl"]
                          for v in done_nb))
                       else (True if (off["metrics"] is not None and neighbors
                                      and len(done_nb) == len(neighbors))
                             else None),
            "selection": None if cohort is None
                         else bool((cohort["auc"] >= 0.55 and cohort["p"] < 0.1)
                                   or selection["win_pctile"] >= 95
                                   or selection["mean_r_pctile"] >= 95),
        }

        off.pop("metrics", None), off.pop("trades", None)
        for v in neighbors:
            m = v.pop("metrics", None)
            v.pop("trades", None)
            if m is not None:
                v.update(net=m["net_pnl"], pf=m["profit_factor"],
                         maxdd=m["max_drawdown"], trades=int(m["trades"]))

        gates_out.append({
            "gate": gate,
            "params": params,
            "verdict": _verdict(marginal, checks),
            "checks": checks,
            "marginal": marginal, "months": months, "bootstrap": bootstrap,
            "tail": tail, "halves": halves,
            "selection": selection, "cohort": cohort,
            # Ghost dollars positive while the in-stack marginal is also
            # positive = the composition-gate mirage (gx_overhang, twice).
            "mirage": bool(cohort and marginal
                           and cohort["ghost_net"] > 0 and marginal["d_net"] > 0),
            "off": off, "neighbors": neighbors,
        })

    return {"run_id": run_id,
            "baseline": {"trades": int(metrics["trades"]),
                         "net": metrics["net_pnl"],
                         "pf": metrics["profit_factor"],
                         "maxdd": metrics["max_drawdown"],
                         "sharpe": metrics["sharpe"],
                         "net_ex_top20": _ex_top20(trades)},
            "has_ghost_frame": has_gates_col,
            "gates": gates_out}
