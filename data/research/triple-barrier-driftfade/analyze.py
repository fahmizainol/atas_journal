"""Stage 1 analysis: does a volatility-scaled first-touch relabel disagree with
the engine's realized win/loss on drift-fade, and if so, where?

Reads relabeled.parquet (from relabel.py) and prints:
  1. Mechanical validation — the walk reproduces the engine's own MFE/MAE.
  2. Per-config agreement matrix — engine outcome (net sign) vs barrier label.
  3. The two disagreement cells characterized (count, net, R): the "path tax"
     (engine won, a k-sigma stop would have lost) and the "engine gave up"
     (engine's exit cut a trade a symmetric first-touch would have won).
  4. Morning vs afternoon under barrier labels — stress-tests the confirmed
     "afternoon is the edge, mornings lose" finding against a stop-enforced label.
  5. Split-half stability of the flip rate.

    python data/research/triple-barrier-driftfade/analyze.py
"""
import sys

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

SLUG = sys.argv[1] if len(sys.argv) > 1 else "drift-touch-fade-entry-stop"
IN = f"data/research/triple-barrier-driftfade/relabeled__{SLUG}.parquet"
VOL_CFGS = ["tb_1.0_60", "tb_1.5_60", "tb_2.0_60", "tb_2.0_30", "tb_2.0_120"]
WIN_LABELS = {"win", "time_pos"}
LOSS_LABELS = {"loss", "time_neg"}
PM_ET = 12  # session split hour (ET): afternoon = entry_hour_et >= 12


def barrier_win(series):
    """Map a label column to True(win)/False(loss)/NA(empty/novol/na)."""
    return series.map(lambda v: True if v in WIN_LABELS
                      else False if v in LOSS_LABELS else np.nan)


def main():
    df = pd.read_parquet(IN)
    df["eng_win"] = df.eng_net > 0
    n = len(df)
    print(f"=== Stage 1 triple-barrier relabel — {SLUG} — {n} trades ===")
    print(f"engine: {int(df.eng_win.sum())} win / {int((~df.eng_win).sum())} loss, "
          f"net ${df.eng_net.sum():,.0f}, sigma_pts median {df.sigma_pts.median():.1f} "
          f"(p10 {df.sigma_pts.quantile(.1):.1f} / p90 {df.sigma_pts.quantile(.9):.1f})")

    # 1. mechanical validation
    dmfe = (df.chk_mfe - df.eng_mfe).abs().max()
    dmae = (df.chk_mae - df.eng_mae).abs().max()
    print(f"\n[1] walk validation: max |chk-eng| MFE {dmfe:.3f} / MAE {dmae:.3f} pt "
          f"({'EXACT' if max(dmfe, dmae) < 1e-6 else 'MISMATCH'})")

    # 2 + 3. agreement matrix + disagreement cells, per config
    print(f"\n[2/3] engine-sign vs barrier-label (win = label in {sorted(WIN_LABELS)}):")
    hdr = (f"  {'config':11} {'agree%':>7} {'flip%':>6} "
           f"{'engW/barL':>10} {'$(that)':>9} {'Rmean':>6}  "
           f"{'engL/barW':>10} {'$(that)':>9} {'Rmean':>6}")
    print(hdr)
    for c in VOL_CFGS:
        bw = barrier_win(df[c + "_label"])
        m = bw.notna()
        agree = (df.eng_win[m] == bw[m]).mean()
        # engine won, barrier says loss -> path tax
        tax = df[m & df.eng_win & (bw == False)]
        # engine lost, barrier says win -> engine gave up
        gave = df[m & (~df.eng_win) & (bw == True)]
        print(f"  {c:11} {agree*100:6.1f}% {(1-agree)*100:5.1f}% "
              f"{len(tax):10d} {tax.eng_net.sum():9,.0f} {tax.eng_r.mean() if len(tax) else float('nan'):6.2f}  "
              f"{len(gave):10d} {gave.eng_net.sum():9,.0f} {gave.eng_r.mean() if len(gave) else float('nan'):6.2f}")
    print("  engW/barL = engine banked it but a k-sigma stop touches first (path tax)")
    print("  engL/barW = engine's exit cut a trade a symmetric first-touch would win")

    # 4. flip / path-tax by entry hour, to see whether the disagreement
    #    concentrates by time of day. (Drift-fade's champion is afternoon-only,
    #    entry_open 12:00 / entry_close 15:00 ET, so it has no morning trades;
    #    upper-band spans the whole session from its 09:45/10:30 checkpoint.)
    hrs = sorted(df.entry_hour_et.unique())
    print(f"\n[4] flip% and path-tax by entry hour ET (hours present: {hrs}):")
    print(f"  {'config':11} " + " ".join(f"{h}:00 flip%/tax$".rjust(16)
                                          for h in sorted(df.entry_hour_et.unique())))
    for c in VOL_CFGS:
        bw = barrier_win(df[c + "_label"])
        cells = []
        for h in sorted(df.entry_hour_et.unique()):
            m = (df.entry_hour_et == h) & bw.notna()
            flip = (df.eng_win[m] != bw[m]).mean()
            tax = df[m & df.eng_win & (bw == False)].eng_net.sum()
            cells.append(f"{flip*100:5.1f}%/{tax:8,.0f}")
        print(f"  {c:11} " + " ".join(x.rjust(16) for x in cells))

    # 5. split-half stability of the flip rate (calendar midpoint)
    print("\n[5] split-half flip% (calendar midpoint):")
    d = pd.to_datetime(df.session)
    mid = d.min() + (d.max() - d.min()) / 2
    h1, h2 = d < mid, d >= mid
    print(f"  midpoint {mid.date()}  |  H1 n={int(h1.sum())}  H2 n={int(h2.sum())}")
    print(f"  {'config':11} {'H1 flip%':>9} {'H2 flip%':>9} {'H1 tax$':>9} {'H2 tax$':>9}")
    for c in VOL_CFGS:
        bw = barrier_win(df[c + "_label"])
        row = []
        for half in (h1, h2):
            m = half & bw.notna()
            flip = (df.eng_win[m] != bw[m]).mean()
            tax = df[m & df.eng_win & (bw == False)].eng_net.sum()
            row += [flip, tax]
        print(f"  {c:11} {row[0]*100:8.1f}% {row[2]*100:8.1f}% {row[1]:9,.0f} {row[3]:9,.0f}")

    # verdict hint
    print("\n[verdict] Stage 2 (meta-label) is justified only if the disagreement")
    print("  cells are fat AND split-half-stable. A thin, unstable flip means the")
    print("  engine's exit already tracks a clean first-touch — labeling is inert.")


if __name__ == "__main__":
    main()
