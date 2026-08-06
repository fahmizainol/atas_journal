"""Stage 2b: train a leakage-safe meta-label to skip the engine's losers, and
judge it OUT-OF-SAMPLE before anyone touches the engine.

Model: L2 logistic regression (pure numpy — no sklearn in this venv, and with 262
samples / 67 losers a linear model is the overfit-safe choice anyway). Honest OOS
probabilities via purged K-fold (contiguous time folds + 1-session embargo, so a
same-session intraday label can't leak train<->test). The scaler + median-impute
are fit on the TRAIN fold only.

Judged by, in order:
  1. OOS AUC of P(loss) vs realized loss, against a label-shuffle null.
  2. Split-half OOS AUC (both halves must beat 0.5 to be real).
  3. A skip-threshold sweep: naive-subtraction net effect + skip precision.

CAVEAT stated loudly: the net effect here is naive subtraction of skipped trades.
It is an UPPER-BOUND estimate, NOT an adoption verdict — freeing a position slot
can re-materialize a previously-missed trade, so only an engine A/B (a real re-run
with the gate live) settles it. Offline-positive is necessary, not sufficient.

    python data/research/triple-barrier-driftfade/meta_label.py [SLUG]
"""
import sys

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

SLUG = sys.argv[1] if len(sys.argv) > 1 else "vwap-upper-band-bounce"
IN = f"data/research/triple-barrier-driftfade/meta_features__{SLUG}.parquet"
K = 5
EMBARGO_DAYS = 1
L2 = 1.0
ITERS = 3000
LR = 0.1


def auc(y, s):
    """Rank-based AUC of score s for positive class y==1."""
    y = np.asarray(y, dtype=bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    return (ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def fit_logistic(X, y):
    """L2 logistic regression by gradient descent. X already standardized+bias."""
    w = np.zeros(X.shape[1])
    for _ in range(ITERS):
        p = 1.0 / (1.0 + np.exp(-X @ w))
        g = X.T @ (p - y) / len(y)
        g[1:] += L2 * w[1:] / len(y)  # don't regularize bias
        w -= LR * g
    return w


def design(Xraw, mean, std, med):
    X = np.where(np.isnan(Xraw), med, Xraw)
    X = (X - mean) / std
    return np.column_stack([np.ones(len(X)), X])


def oos_probs(F, y, feat, sess):
    """Purged K-fold OOS P(loss) for every trade."""
    n = len(y)
    order = np.argsort(sess.values.astype("datetime64[ns]"), kind="mergesort")
    folds = np.array_split(order, K)
    probs = np.full(n, np.nan)
    for f in folds:
        test = np.zeros(n, dtype=bool)
        test[f] = True
        t0, t1 = sess.values[f].min(), sess.values[f].max()
        emb = pd.Timedelta(days=EMBARGO_DAYS)
        near = (sess.values >= t0 - emb) & (sess.values <= t1 + emb)
        train = ~test & ~near
        if train.sum() < 20:
            train = ~test
        Xtr_raw = F[train]
        med = np.nanmedian(Xtr_raw, axis=0)
        mean = np.nanmean(np.where(np.isnan(Xtr_raw), med, Xtr_raw), axis=0)
        std = np.nanstd(np.where(np.isnan(Xtr_raw), med, Xtr_raw), axis=0)
        std[std == 0] = 1.0
        w = fit_logistic(design(Xtr_raw, mean, std, med), y[train])
        probs[test] = 1.0 / (1.0 + np.exp(-design(F[test], mean, std, med) @ w))
    return probs


def main():
    df = pd.read_parquet(IN)
    drop = {"trade_no", "session", "eng_net", "eng_r", "eng_loss", "eng_exit_reason"}
    feat = [c for c in df.columns if c not in drop]
    F = df[feat].to_numpy(dtype="float64")
    y = df.eng_loss.to_numpy(dtype="float64")
    net = df.eng_net.to_numpy(dtype="float64")
    sess = pd.to_datetime(df.session)
    n = len(df)
    print(f"=== Stage 2 meta-label — {SLUG} — {n} trades, "
          f"{int(y.sum())} losers ({y.mean()*100:.1f}%), {len(feat)} features ===")
    print(f"baseline net ${net.sum():,.0f}")

    # 1. OOS AUC vs shuffle null
    probs = oos_probs(F, y, feat, sess)
    a = auc(y, probs)
    np.random.seed(0)
    null = []
    for _ in range(30):
        ys = y.copy()
        np.random.shuffle(ys)
        null.append(auc(ys, oos_probs(F, ys, feat, sess)))
    null = np.array(null)
    print(f"\n[1] OOS AUC (predict loss): {a:.3f}  "
          f"| shuffle-null AUC mean {null.mean():.3f} (p95 {np.quantile(null,.95):.3f}, max {null.max():.3f})")
    print(f"    empirical p (null >= real): {(null >= a).mean():.3f}")

    # 2. split-half OOS AUC
    mid = sess.min() + (sess.max() - sess.min()) / 2
    h1 = (sess < mid).to_numpy()
    print(f"[2] split-half OOS AUC: H1 {auc(y[h1], probs[h1]):.3f} (n={int(h1.sum())})  "
          f"H2 {auc(y[~h1], probs[~h1]):.3f} (n={int((~h1).sum())})")

    # 3. skip-threshold sweep (NAIVE subtraction — upper bound, not a verdict)
    print("\n[3] skip if OOS P(loss) >= thr  (NAIVE subtraction — needs engine A/B):")
    print(f"  {'thr':>5} {'skip n':>6} {'skip=loss%':>10} {'kept n':>6} "
          f"{'kept net':>10} {'delta':>9} {'kept PF':>7}")
    gross_w = net[net > 0]
    for thr in (0.30, 0.35, 0.40, 0.45, 0.50, 0.60):
        skip = probs >= thr
        kept = ~skip
        sk_lossrate = y[skip].mean() if skip.any() else float("nan")
        kn = net[kept]
        pf = kn[kn > 0].sum() / -kn[kn < 0].sum() if (kn < 0).any() else float("inf")
        print(f"  {thr:5.2f} {int(skip.sum()):6d} {sk_lossrate*100:9.1f}% {int(kept.sum()):6d} "
              f"{kn.sum():10,.0f} {kn.sum()-net.sum():9,.0f} {pf:7.2f}")

    # coefficients (full-data refit, standardized — interpretation only)
    med = np.nanmedian(F, axis=0)
    mean = np.nanmean(np.where(np.isnan(F), med, F), axis=0)
    std = np.nanstd(np.where(np.isnan(F), med, F), axis=0)
    std[std == 0] = 1.0
    w = fit_logistic(design(F, mean, std, med), y)
    coef = sorted(zip(feat, w[1:]), key=lambda kv: -abs(kv[1]))
    print("\n[coef] top standardized weights (+ = predicts LOSS):")
    for name, c in coef[:8]:
        print(f"    {name:16} {c:+.3f}")

    print("\n[verdict] Adopt-worthy only if OOS AUC clears the null AND both halves")
    print("  beat 0.5 AND the sweep shows a stable positive delta — THEN run an")
    print("  engine A/B (gate live) to confirm past the capacity caveat.")


if __name__ == "__main__":
    main()
