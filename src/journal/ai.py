"""AI analyzer: per-trade critique and aggregate period review via LiteLLM.

Provider-agnostic — the model is chosen by the LLM_MODEL env var (see config),
so swapping providers is a one-line change. Degrades gracefully: when no model
is configured, is_available() is False and the UI hides the AI controls.

Grounding principle: prompts carry only the objective numbers the journal already
computes (PnL, MAE/MFE, exit efficiency, edges breakdowns), so the model reasons
over real figures rather than inventing price action.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from . import config

_COMMON = (
    "You are a disciplined futures-trading coach reviewing NQ index-future trades. "
    "Be concise, specific, and tie every observation to the numbers provided. "
    "Never invent prices, levels, or chart patterns that are not in the data. "
    "Respond with a single JSON object and nothing else."
)


def is_available() -> bool:
    return config.llm_available()


def _complete(system: str, user: str, model: str | None = None) -> str:
    """One LiteLLM completion. Raises on failure (caller turns it into an error dict)."""
    import litellm

    resp = litellm.completion(
        model=model or config.llm_model(),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=config.LLM_MAX_TOKENS,
        temperature=0.3,
    )
    return resp["choices"][0]["message"]["content"] or ""


def _parse_json(text: str) -> dict:
    """Best-effort JSON parse; tolerate code fences and trailing prose."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        s = s[start:end + 1]
    try:
        out = json.loads(s)
        if isinstance(out, dict):
            return out
    except (json.JSONDecodeError, ValueError):
        pass
    return {"verdict": text.strip()}


def _profile_block(profile: str) -> str:
    profile = (profile or "").strip()
    if not profile:
        return ""
    return f"\n\nThe trader's own rules / style (judge the trade against these):\n{profile}\n"


# --- Per-trade critique --------------------------------------------------
def _fmt_fills(fills) -> str:
    if not isinstance(fills, list) or not fills:
        return "n/a"
    parts = []
    for f in fills:
        ts = f.get("ts_local")
        ts_s = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        parts.append(f"{ts_s} {f.get('direction')} {f.get('volume')}@{f.get('price')}")
    return "; ".join(parts)


def analyze_trade(
    trade: pd.Series, excursion: dict, note: str, comment: str, profile: str,
    model: str | None = None,
) -> dict:
    eff = excursion.get("exit_efficiency")
    eff_s = f"{eff * 100:.0f}%" if eff is not None else "n/a"
    hold_min = float(trade["duration_s"]) / 60.0

    facts = (
        f"Instrument: {trade['instrument']}\n"
        f"Direction: {trade['direction']}\n"
        f"Contracts: {float(trade['max_contracts']):.0f}\n"
        f"Avg entry: {trade['avg_entry']}\n"
        f"Avg exit: {trade['avg_exit']}\n"
        f"Hold time: {hold_min:.1f} min\n"
        f"Gross PnL: ${float(trade['gross_pnl']):,.2f}\n"
        f"Commission: ${float(trade['commission']):,.2f}\n"
        f"Net PnL: ${float(trade['net_pnl']):,.2f}\n"
        f"MFE (best unrealized): ${excursion['mfe_usd']:,.2f}\n"
        f"MAE (worst unrealized): ${excursion['mae_usd']:,.2f}\n"
        f"Exit efficiency (realized / MFE): {eff_s}\n"
        f"Fills: {_fmt_fills(trade.get('fills'))}\n"
    )
    note = (note or "").strip()
    comment = (comment or "").strip()
    if note:
        facts += f"Trader's note on this trade: {note}\n"
    if comment:
        facts += f"ATAS comment: {comment}\n"

    user = (
        "Critique this single trade.\n\n"
        f"{facts}{_profile_block(profile)}\n"
        "Return JSON with exactly these keys:\n"
        '  "verdict": one-sentence summary,\n'
        '  "went_well": array of short strings (may be empty),\n'
        '  "went_wrong": array of short strings (may be empty),\n'
        '  "suggestion": one concrete, actionable fix,\n'
        '  "grade": single letter A-F.\n'
    )
    try:
        return _parse_json(_complete(_COMMON, user, model))
    except Exception as exc:  # noqa: BLE001 — surface any provider error to the UI
        return {"error": str(exc)}


# --- Aggregate period review --------------------------------------------
def _df_to_text(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  (no data)"
    return df.to_string(index=False)


def analyze_period(
    metrics: dict, edges: dict[str, pd.DataFrame], daily: pd.DataFrame, profile: str,
    model: str | None = None,
) -> dict:
    def g(k, money=True):
        v = metrics.get(k)
        if v is None:
            return "n/a"
        return f"${v:,.2f}" if money else f"{v}"

    summary = (
        f"Trades: {metrics.get('trades', 0)}\n"
        f"Net PnL: {g('net_pnl')}\n"
        f"Win rate: {metrics.get('win_rate', 0):.1f}%  "
        f"({metrics.get('wins', 0)}W / {metrics.get('losses', 0)}L)\n"
        f"Profit factor: {g('profit_factor', money=False)}\n"
        f"Expectancy/trade: {g('expectancy')}\n"
        f"Avg win: {g('avg_win')}  Avg loss: {g('avg_loss')}\n"
        f"Best: {g('best_trade')}  Worst: {g('worst_trade')}\n"
        f"Max drawdown: {g('max_drawdown')}\n"
        f"Max consecutive W/L: {metrics.get('max_consecutive_wins', 0)} / "
        f"{metrics.get('max_consecutive_losses', 0)}\n"
        f"Sharpe: {g('sharpe', money=False)}  Sortino: {g('sortino', money=False)}\n"
        f"Winning days: {metrics.get('winning_days_pct', 0):.0f}% of "
        f"{metrics.get('total_days', 0)} days\n"
    )

    edge_text = "\n".join(
        f"-- {name} --\n{_df_to_text(df)}" for name, df in edges.items()
    )

    user = (
        "Review this trader's performance over the selected period.\n\n"
        f"Headline metrics:\n{summary}\n"
        f"Behavioral edge breakdowns:\n{edge_text}\n\n"
        f"Daily PnL:\n{_df_to_text(daily)}\n"
        f"{_profile_block(profile)}\n"
        "Identify where money is made and lost. Return JSON with exactly these keys:\n"
        '  "summary": 1-2 sentence overall read,\n'
        '  "strengths": array of short strings,\n'
        '  "leaks": array of short strings (where money bleeds, cite the bucket+number),\n'
        '  "recommendations": array of concrete, actionable changes.\n'
    )
    try:
        return _parse_json(_complete(_COMMON, user, model))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --- Period-review cache keys --------------------------------------------
def scope_signature(instruments, date_range, tags) -> str:
    """Stable hash of the active filter scope (instruments + date range + tags)."""
    instr = sorted(str(i) for i in (instruments or []))
    tg = sorted(str(t) for t in (tags or []))
    if date_range and isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        dr = [str(date_range[0]), str(date_range[1])]
    else:
        dr = [str(date_range)]
    payload = json.dumps({"instruments": instr, "date_range": dr, "tags": tg},
                         sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def trade_fingerprint(tf: pd.DataFrame) -> tuple[int, str | None]:
    """(trade count, latest exit timestamp) — detects new trades in scope."""
    if tf is None or tf.empty:
        return 0, None
    latest = tf["exit_ts_utc"].max()
    return len(tf), (latest.isoformat() if hasattr(latest, "isoformat") else str(latest))
