"""Core performance metrics over a set of trades.

Operates on the normalized trade frame produced by trades.build_logical_trades
or trades.atas_trades. Uses net_pnl per trade as the unit of return.
"""

from __future__ import annotations

import math

import pandas as pd


# Trading days in a year: the constant that turns a daily ratio into the annual
# one everybody quotes.
TRADING_DAYS = 252


def _max_consecutive(mask: pd.Series) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _daily_ratios(daily: pd.Series) -> tuple[float, float]:
    """(Sharpe, Sortino) on DAILY P&L, annualized — not per trade.

    Per trade, the two ratios are blind to how often you trade: a setup that fires
    63 times and one that fires 527 times to earn the same money per unit of
    per-trade noise score the same, when the second is plainly the better business.
    Daily P&L is the series that actually accrues to an account, so that is the one
    to measure, and *252 sqrt-days is what makes the number comparable to a Sharpe
    quoted anywhere else.

    Flat days count, and they are the reason this is not just a rescaling: a day
    the strategy sat out earned 0, and dropping those days would flatter a rare
    setup by pretending its idle weeks never happened. So the series is reindexed
    over every WEEKDAY from the first trading day to the last — the span the
    strategy was actually exposed for — with the untraded ones filled with 0.
    (Exchange holidays land in that span as flat days too. They dilute every
    config by the same handful of days, which is a rounding error next to the
    distortion that dropping real flat days would introduce.)

    Sortino divides by the deviation of the LOSING days only: upside volatility is
    not risk, and a strategy whose good days are wildly good should not be punished
    for it.
    """
    if daily.empty:
        return 0.0, 0.0
    idx = pd.bdate_range(min(daily.index), max(daily.index))
    d = daily.reindex(idx.date, fill_value=0.0).astype(float)
    if len(d) < 2:
        return 0.0, 0.0

    ann = math.sqrt(TRADING_DAYS)
    std = float(d.std(ddof=1))
    sharpe = float(d.mean() / std) * ann if std > 0 else 0.0
    down = d[d < 0]
    dstd = float(down.std(ddof=1)) if len(down) > 1 else 0.0
    sortino = float(d.mean() / dstd) * ann if dstd > 0 else 0.0
    return sharpe, sortino


def compute_metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0, "longs": 0, "shorts": 0}

    t = trades.sort_values("entry_ts_utc").reset_index(drop=True)
    pnl = t["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n = len(t)
    direction = t.get("direction", pd.Series(dtype=str)).astype(str)
    longs = int((direction == "Long").sum())
    shorts = int((direction == "Short").sum())

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())  # negative
    net = float(pnl.sum())
    win_rate = len(wins) / n * 100 if n else 0.0
    loss_rate = len(losses) / n * 100 if n else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else math.inf
    expectancy = (win_rate / 100) * avg_win + (loss_rate / 100) * avg_loss

    equity = pnl.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    max_dd = float(drawdown.min()) if n else 0.0
    recovery = (net / abs(max_dd)) if max_dd != 0 else math.inf

    # day-level
    day = t["entry_ts_local"].dt.date
    daily = pnl.groupby(day).sum()
    sharpe, sortino = _daily_ratios(daily)
    profit_days = int((daily > 0).sum())
    loss_days = int((daily < 0).sum())
    total_days = int(daily.size)
    winning_days_pct = (profit_days / total_days * 100) if total_days else 0.0

    return {
        "trades": n,
        "longs": longs,
        "shorts": shorts,
        "net_pnl": net,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": (avg_win / abs(avg_loss)) if avg_loss != 0 else math.inf,
        "expectancy": expectancy,
        "best_trade": float(pnl.max()),
        "worst_trade": float(pnl.min()),
        "max_consecutive_wins": _max_consecutive(pnl > 0),
        "max_consecutive_losses": _max_consecutive(pnl < 0),
        "max_drawdown": max_dd,
        "recovery_factor": recovery,
        "sharpe": sharpe,
        "sortino": sortino,
        "total_days": total_days,
        "profit_days": profit_days,
        "loss_days": loss_days,
        "winning_days_pct": winning_days_pct,
        "avg_trade_length_s": float(t["duration_s"].mean()),
        "total_commission": float(t["commission"].sum()),
    }


def equity_curve(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame(columns=["ts", "equity", "drawdown"])
    t = trades.sort_values("entry_ts_utc").reset_index(drop=True)
    equity = t["net_pnl"].astype(float).cumsum()
    running_max = equity.cummax()
    return pd.DataFrame({
        "ts": t["exit_ts_local"],
        "trade_no": t.get("trade_no", pd.Series(range(1, len(t) + 1))),
        "pnl": t["net_pnl"].astype(float),
        "equity": equity,
        "drawdown": equity - running_max,
    })


def daily_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    """Net PnL and trade count per trading day (in the active display zone)."""
    if trades is None or trades.empty:
        return pd.DataFrame(columns=["date", "net_pnl", "trades"])
    t = trades.copy()
    t["date"] = t["entry_ts_local"].dt.date
    g = t.groupby("date").agg(net_pnl=("net_pnl", "sum"), trades=("net_pnl", "size"))
    g = g.reset_index()
    g["date"] = pd.to_datetime(g["date"])
    return g
