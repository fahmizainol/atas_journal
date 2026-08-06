"""The wire format a tick tape is shipped to the browser in.

One session is ~0.5-1M trades, so a list of objects is not an option: the tape
goes over as delta-encoded columns — timestamps as integer-millisecond deltas,
prices as integer tick deltas off a base, sizes as ints, aggressor sides as one
packed string — and the client reconstructs by prefix-sum (`decodeTape` in
lib/replayEngine.ts).

Extracted from the Simulator's session endpoint because Live needs exactly the
same bytes for a *slice* of a tape that is still growing. Two encoders would be
two chances for a decoder to be right about one of them: the replay tape and the
live tape are decoded by the same function on the client, so they had better be
produced by the same function here.

SELF-CONTAINED SLICES. Every encoded block carries its own `t0` and `price0` and
opens with `dt[0] == dp[0] == 0`, so a block decodes without knowing what came
before it. That is what lets the live poll hand back rows [since, n) and have the
client simply append the result: the block is a tape in its own right, and the
join is a concatenation rather than a continuation of somebody else's prefix sum.

TIMES ARE WALL-CLOCK, NOT INSTANTS. The epoch-ms here is that of the *display
zone's wall clock* (the same projection as every other chart payload, see
charts_data._epoch_local), so weekend and overnight gaps collapse natively and
the forming 1-minute candle lands on the grid the rest of the app draws on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS


def zone_for(tz: str | None):
    """The display zone a payload's wall clock is projected into."""
    return DISPLAY_TZS.get(tz or DEFAULT_DISPLAY_TZ, DISPLAY_TZS[DEFAULT_DISPLAY_TZ])


def local_ms(ts_utc: pd.Series, zone) -> np.ndarray:
    """UTC instants -> epoch-ms of the wall clock in ``zone`` (gap-collapsing)."""
    local = ts_utc.dt.tz_convert(zone).dt.tz_localize(None)
    return local.values.astype("datetime64[ms]").astype("int64")


def encode_ticks(frame: pd.DataFrame, zone, tick_size: float) -> dict:
    """Delta-encode a tick frame into the columns the client decodes.

    ``frame`` carries what ``ticks.get_day_ticks`` returns (ts_utc, price, size,
    side) and must be sorted by ``ts_utc`` — the prefix sums below are stored as
    signed deltas, so an out-of-order row is not a crash, it is a tape the client
    silently reconstructs wrong.

    An empty frame encodes as ``n = 0`` with empty columns rather than raising:
    the live poll asks for [since, n) every time it wakes, and "nothing arrived"
    is the common answer, not an error.
    """
    n = int(len(frame))
    if n == 0:
        return {"n": 0, "t0": 0, "dt": [], "price0": 0.0, "dp": [],
                "size": [], "side": ""}

    t_ms = local_ms(frame["ts_utc"], zone)
    dt = np.diff(t_ms, prepend=t_ms[:1]).astype("int64")

    price = frame["price"].to_numpy(dtype="float64")
    ticks = np.round(price / tick_size).astype("int64")
    dp = np.diff(ticks, prepend=ticks[:1]).astype("int64")

    size = frame["size"].to_numpy(dtype="int64")
    side = "".join(frame["side"].fillna("N").astype(str).str[0].tolist())

    return {
        "n": n,
        "t0": int(t_ms[0]),
        "dt": dt.tolist(),
        "price0": float(ticks[0] * tick_size),
        "dp": dp.tolist(),
        "size": size.tolist(),
        "side": side,
    }
