"""Presentation layer: dark theme CSS, app header, and custom KPI cards.

Keeps the visual chrome out of app.py. Cards are rendered as controlled HTML
so values can be color-coded by sign (green/red) — something st.metric can't do.
"""

from __future__ import annotations

import html

import streamlit as st

# Palette — mirrors .streamlit/config.toml. Charts import these too.
BG = "#0e1117"
BG_2 = "#15171f"
CARD = "#1a1d27"
CARD_BORDER = "#262a36"
ACCENT = "#6c5ce7"
GREEN = "#21c07a"
RED = "#f5455f"
TEXT = "#e6e8ee"
MUTED = "#8a8f9c"
GRID = "#2a2e38"

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {{
  --bg: {BG}; --card: {CARD}; --card-border: {CARD_BORDER};
  --accent: {ACCENT}; --green: {GREEN}; --red: {RED};
  --text: {TEXT}; --muted: {MUTED};
}}

html, body, [class*="css"], [data-testid="stAppViewContainer"], .stApp,
[data-testid="stSidebar"] {{
  font-family: 'Inter', system-ui, sans-serif;
}}

[data-testid="stAppViewContainer"] {{
  background: linear-gradient(180deg, {BG} 0%, {BG_2} 100%);
}}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.2rem; }}

[data-testid="stSidebar"] {{
  background: #12141b;
  border-right: 1px solid var(--card-border);
}}

/* ---- app header ---- */
.app-header {{ display:flex; align-items:center; gap:14px; padding: 2px 2px 16px; }}
.app-header .logo {{
  width:40px; height:40px; border-radius:11px;
  background: linear-gradient(135deg, {ACCENT}, #8f7bff);
  display:flex; align-items:center; justify-content:center;
  font-weight:800; color:#fff; font-size:18px;
  box-shadow: 0 4px 14px rgba(108,92,231,.35);
}}
.app-header .title {{ font-size:22px; font-weight:700; color:var(--text); line-height:1.15; }}
.app-header .subtitle {{ font-size:13px; color:var(--muted); margin-top:1px; }}

/* ---- KPI cards ---- */
.kpi-grid {{ display:grid; gap:14px; margin: 4px 0 8px; }}
.kpi-card {{
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,.25);
  transition: transform .12s ease, border-color .12s ease;
}}
.kpi-card:hover {{ transform: translateY(-2px); border-color: #353b50; }}
.kpi-label {{
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--muted); font-weight: 600;
}}
.kpi-value {{ font-size: 24px; font-weight: 700; margin-top: 6px; color: var(--text); }}
.kpi-value.pos {{ color: var(--green); }}
.kpi-value.neg {{ color: var(--red); }}
.kpi-sub {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
.kpi-hero {{
  padding: 22px 24px;
  background: linear-gradient(135deg, {CARD} 0%, #20232f 100%);
  border-color: #333852;
}}
.kpi-hero .kpi-label {{ font-size: 12px; }}
.kpi-hero .kpi-value {{ font-size: 40px; margin-top: 8px; }}

/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid var(--card-border); }}
.stTabs [data-baseweb="tab"] {{
  height: 42px; padding: 0 18px; color: var(--muted); font-weight: 600;
}}
.stTabs [aria-selected="true"] {{ color: var(--text); }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: var(--accent); }}

/* ---- st.metric fallback (Edges/Cross-check, anything not carded) ---- */
[data-testid="stMetric"] {{
  background: var(--card); border: 1px solid var(--card-border);
  border-radius: 14px; padding: 14px 16px;
}}
[data-testid="stMetricLabel"] p {{ color: var(--muted); font-weight: 600; }}

/* ---- containers / dataframes / buttons ---- */
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
.stButton > button {{
  border-radius: 10px; font-weight: 600; border: 1px solid var(--card-border);
}}
.section-title {{ font-size: 15px; font-weight: 700; color: var(--text); margin: 16px 0 2px; }}
.section-cap {{ font-size: 12px; color: var(--muted); margin-bottom: 8px; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def app_header(title: str, subtitle: str, logo: str = "A") -> None:
    st.markdown(
        f'<div class="app-header">'
        f'<div class="logo">{html.escape(logo)}</div>'
        f'<div><div class="title">{html.escape(title)}</div>'
        f'<div class="subtitle">{html.escape(subtitle)}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def tone_of(x: float | None) -> str:
    """pos / neg / neutral by sign — drives card value color."""
    if x is None:
        return "neutral"
    try:
        if x > 0:
            return "pos"
        if x < 0:
            return "neg"
    except TypeError:
        return "neutral"
    return "neutral"


def _card_html(c: dict) -> str:
    tone = c.get("tone") or "neutral"
    hero = " kpi-hero" if c.get("hero") else ""
    label = html.escape(str(c["label"]))
    value = html.escape(str(c["value"]))
    sub = f'<div class="kpi-sub">{html.escape(str(c["sub"]))}</div>' if c.get("sub") else ""
    return (
        f'<div class="kpi-card{hero}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value {tone}">{value}</div>'
        f'{sub}</div>'
    )


def render_cards(cards: list[dict], template: str) -> None:
    """Render a CSS-grid row of cards.

    Each card dict: {label, value, tone?: pos|neg|neutral, sub?, hero?: bool}.
    `template` is a CSS grid-template-columns value, e.g. 'repeat(4, 1fr)'.
    """
    items = "".join(_card_html(c) for c in cards)
    st.markdown(
        f'<div class="kpi-grid" style="grid-template-columns:{template}">{items}</div>',
        unsafe_allow_html=True,
    )


def section_title(text: str, caption: str | None = None) -> None:
    st.markdown(f'<div class="section-title">{html.escape(text)}</div>', unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="section-cap">{html.escape(caption)}</div>', unsafe_allow_html=True)
