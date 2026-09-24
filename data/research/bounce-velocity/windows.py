"""The measurement, shared by both extractors.

The sim run and the manual journal source their trades completely differently
(one has an ``entry_idx`` into the engine's own tick array, the other has a
timestamp and nothing else), but the *measurement* must be identical or the two
studies cannot be compared. So it lives here once.

Input is the trade's own path from the fill: ``sig`` = signed distance from the
entry price in ticks (positive = in your favour, whichever way you were facing),
``sec`` = seconds since the fill. Both arrays cover entry tick -> exit tick.
"""
import numpy as np


def first_time(sec, mask):
    """Seconds to the first True in ``mask``, or NaN."""
    if not mask.any():
        return np.nan
    return float(sec[int(mask.argmax())])


def path_features(sig, sec, *, windows, first_up, first_dn, censor_s,
                  tick, pval, contracts, commission, duration_s, stop_pts=None):
    """Dip, climb-out and speed inside each window, plus window-free reach times.

    Per window W:
      ``depth``  max adverse excursion inside the window, ticks, >= 0
      ``t_low``  seconds from the fill to that deepest tick
      ``up``     max favourable excursion inside the window, ticks
      ``end``    signed ticks at the window's last tick
      ``trec``   seconds from the deepest tick back to the fill price — NaN if
                 it never got back inside the window (the censored, no-bounce case)
      ``vel``    ``depth / trec`` — ticks per second of climb-out
      ``open``   whether the trade outlives the window at all

    ``trec``/``vel`` are deliberately measured from the window's low rather than
    from the fill: the question is the speed of the *bounce*, not of the dip.
    """
    # Window-free timing of the whole path's extremes. ``t_mae_s`` is the one
    # number the window grid can only bracket: when the worst tick actually
    # happened. Reported as a fraction of the holding time too, because a
    # 40-second trade and a 15-minute one cannot be compared in raw seconds.
    rec = {}
    dur = max(float(sec[-1]), 1e-9)
    rec["t_mae_s"] = float(sec[int(sig.argmin())])
    rec["t_mfe_s"] = float(sec[int(sig.argmax())])
    rec["mae_frac_dur"] = rec["t_mae_s"] / dur
    rec["mfe_frac_dur"] = rec["t_mfe_s"] / dur

    for n in first_up:
        v = first_time(sec, sig >= n)
        rec[f"t_up{n}"] = v if (v == v and v <= censor_s) else np.nan
    for n in first_dn:
        v = first_time(sec, sig <= -n)
        rec[f"t_dn{n}"] = v if (v == v and v <= censor_s) else np.nan

    for w in windows:
        k = int((sec <= w).sum())
        rec[f"w{w}_open"] = bool(duration_s > w)
        rec[f"w{w}_n"] = k
        if k == 0:
            continue
        p, sc = sig[:k], sec[:k]
        lo_i = int(p.argmin())
        depth = float(max(-p[lo_i], 0.0))
        rec[f"w{w}_depth"] = depth
        rec[f"w{w}_t_low"] = float(sc[lo_i])
        rec[f"w{w}_up"] = float(p.max())
        rec[f"w{w}_end"] = float(p[-1])
        rec[f"w{w}_end_$"] = float(p[-1] * tick * pval * contracts - commission)
        rec[f"w{w}_end_r"] = (float(p[-1] * tick / stop_pts)
                              if stop_pts and stop_pts == stop_pts else np.nan)
        back = p[lo_i:] >= 0
        if depth > 0 and back.any():
            trec = float(sc[lo_i + int(back.argmax())] - sc[lo_i])
            rec[f"w{w}_trec"] = trec
            rec[f"w{w}_vel"] = depth / trec if trec > 0 else np.inf
        else:
            rec[f"w{w}_trec"] = np.nan
            rec[f"w{w}_vel"] = np.nan
    return rec
