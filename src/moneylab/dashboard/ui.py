"""Shared presentation helpers for the dashboard: styling, data-source labels, plain-
English strategy summaries, formatting and the equity chart.

Pure functions (``data_source``, ``strategy_summary``, ``fmt_*``) hold no Streamlit
state and are unit-tested; the ``render_*`` helpers only draw.
"""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moneylab.describe import DataSource as DataSource
from moneylab.describe import condition_text as condition_text
from moneylab.describe import data_source as data_source
from moneylab.describe import strategy_summary as strategy_summary

# Chart palette (dataviz reference instance, light mode): categorical slots in fixed
# order, a neutral for the benchmark, recessive chrome.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
BENCHMARK = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
INK_MUTED = "#898781"
INK = "#0b0b0b"

CSS = """
<style>
:root {
  --ml-ink: #0b0b0b; --ml-ink-2: #52514e; --ml-muted: #898781;
  --ml-line: rgba(11,11,11,0.10); --ml-surface: #ffffff; --ml-plane: #f7f7f5;
  --ml-accent: #2a78d6;
}
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1280px; }
h1, h2, h3, h4 { letter-spacing: -0.01em; }
[data-testid="stSidebar"] { border-right: 1px solid var(--ml-line); }
[data-testid="stSidebar"] [data-testid="stRadio"] label p { font-size: 0.95rem; }
.ml-brand { font-weight: 700; font-size: 1.25rem; margin: 0; }
.ml-brand-sub { color: var(--ml-muted); font-size: 0.82rem; margin: 0 0 0.5rem 0; }
.ml-page-title { font-size: 1.9rem !important; font-weight: 700; margin: 0 !important;
  line-height: 1.2; }
.ml-page-sub { color: var(--ml-ink-2); margin: 0.3rem 0 1.2rem 0 !important;
  font-size: 1rem; }
.ml-brand { font-size: 1.3rem !important; }
.ml-eyebrow { text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem;
  color: var(--ml-muted); font-weight: 600; margin-bottom: 0.15rem; }
.ml-badge { display: inline-flex; align-items: center; gap: 0.35rem; border-radius: 999px;
  padding: 0.12rem 0.6rem; font-size: 0.78rem; font-weight: 600; border: 1px solid;
  white-space: nowrap; vertical-align: middle; }
.ml-badge-real { color: #0b5c0b; background: #e8f5e8; border-color: #b9dfb9; }
.ml-badge-demo { color: #7a4a00; background: #fff4dc; border-color: #f3d79a; }
.ml-badge-file { color: #1c4f95; background: #e9f1fc; border-color: #bcd3f3; }
.ml-badge-pass { color: #0b5c0b; background: #e8f5e8; border-color: #b9dfb9; }
.ml-badge-pending { color: #52514e; background: #f0efec; border-color: #dcdad3; }
.ml-badge-neutral { color: #52514e; background: #f7f7f5; border-color: #e1e0d9; }
.ml-dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: currentColor; }
.ml-meta { color: var(--ml-ink-2); font-size: 0.9rem; }
.ml-meta code { font-size: 0.82rem; }
.ml-summary { font-size: 0.98rem; line-height: 1.55; margin: 0.35rem 0 0 0; }
.ml-cond { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.ml-cond th { text-align: left; color: var(--ml-muted); font-weight: 600; font-size: 0.75rem;
  text-transform: uppercase; letter-spacing: 0.04em; padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--ml-line); }
.ml-cond td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--ml-line); }
.ml-cond td.num { text-align: right; font-variant-numeric: tabular-nums; }
.ml-ok { color: #006300; font-weight: 700; }
.ml-no { color: #b42323; font-weight: 700; }
.ml-na { color: var(--ml-muted); }
.ml-action { font-weight: 700; }
[data-testid="stMetricValue"] { font-size: 1.6rem; font-variant-numeric: tabular-nums; }
[data-testid="stMetricLabel"] p { color: var(--ml-ink-2); }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# Data source labels -----------------------------------------------------------------
def badge(text: str, kind: str) -> str:
    return (
        f'<span class="ml-badge ml-badge-{kind}"><span class="ml-dot"></span>'
        f"{html.escape(text)}</span>"
    )


def source_badge(source: str | None) -> str:
    ds = data_source(source)
    return badge(ds.badge, ds.kind)


def snapshot_option(meta: dict[str, Any]) -> str:
    """One-line description of a snapshot for selectors."""
    ds = data_source(meta.get("source"), meta.get("label", ""))
    syms = meta.get("symbols", [])
    shown = ", ".join(syms[:4]) + (f" +{len(syms) - 4}" if len(syms) > 4 else "")
    return (
        f"{ds.badge} · {shown} · {meta.get('first_date', '?')} to {meta.get('last_date', '?')}"
        f"  ({meta['id'][:8]})"
    )


# Strategy summary ---------------------------------------------------------------------
# Formatting ---------------------------------------------------------------------------
def fmt_pct(v: Any, digits: int = 2) -> str:
    return f"{v:.{digits}%}" if isinstance(v, int | float) and pd.notna(v) else "\u2013"


def fmt_num(v: Any, digits: int = 2) -> str:
    return f"{v:,.{digits}f}" if isinstance(v, int | float) and pd.notna(v) else "\u2013"


def fmt_money(v: Any) -> str:
    return f"${v:,.2f}" if isinstance(v, int | float) and pd.notna(v) else "\u2013"


def fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    ts = pd.Timestamp(iso)
    return ts.strftime("%b %d, %H:%M UTC") if ts.tzinfo else ts.strftime("%b %d, %H:%M")


# Layout pieces ------------------------------------------------------------------------
def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="ml-page-title">{html.escape(title)}</div>'
        f'<div class="ml-page-sub">{html.escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def eyebrow(text: str) -> None:
    st.markdown(f'<div class="ml-eyebrow">{html.escape(text)}</div>', unsafe_allow_html=True)


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def equity_chart(
    series: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame | None = None,
    benchmark_name: str = "Benchmark",
    height: int = 340,
) -> None:
    """Equity curves on one dollar axis: strategies in fixed categorical order, the
    benchmark as a dashed neutral. Direct end labels plus a legend; unified hover."""
    fig = go.Figure()
    ends: list[tuple[str, Any, float, str]] = []
    xs = [eq["session"] for eq in series.values() if len(eq)]
    for n, (name, eq) in enumerate(series.items()):
        color = SERIES[n % len(SERIES)]
        fig.add_trace(
            go.Scatter(
                x=eq["session"],
                y=eq["equity"],
                name=name,
                mode="lines",
                line={"color": color, "width": 2},
                hovertemplate="%{y:$,.0f}",
            )
        )
        if len(eq):
            ends.append((name, eq["session"].iloc[-1], float(eq["equity"].iloc[-1]), color))
    if benchmark is not None and "benchmark_equity" in benchmark:
        fig.add_trace(
            go.Scatter(
                x=benchmark["session"],
                y=benchmark["benchmark_equity"],
                name=benchmark_name,
                mode="lines",
                line={"color": BENCHMARK, "width": 1.5},
                hovertemplate="%{y:$,.0f}",
            )
        )
        if len(benchmark):
            ends.append(
                (
                    benchmark_name,
                    benchmark["session"].iloc[-1],
                    float(benchmark["benchmark_equity"].iloc[-1]),
                    BENCHMARK,
                )
            )
    for name, x, y, color in ends:
        fig.add_annotation(
            x=x,
            y=y,
            text=html.escape(name if len(name) <= 18 else name[:17] + "\u2026"),
            showarrow=False,
            xanchor="left",
            xshift=6,
            font={"size": 11, "color": INK},
            bgcolor="rgba(255,255,255,0.85)",
        )
        fig.add_trace(
            go.Scatter(
                x=[x],
                y=[y],
                mode="markers",
                marker={"size": 8, "color": color, "line": {"color": "#ffffff", "width": 2}},
                showlegend=False,
                hoverinfo="skip",
            )
        )
    axis = {
        "showgrid": False,
        "linecolor": AXIS,
        "tickfont": {"color": INK_MUTED, "size": 11},
        "zeroline": False,
        "automargin": True,
    }
    fig.update_layout(
        height=height,
        margin={"l": 64, "r": 150, "t": 36, "b": 40},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 12, "color": INK},
        },
        xaxis={
            **axis,
            "range": [min(x.min() for x in xs), max(x.max() for x in xs)] if xs else None,
            "showspikes": True,
            "spikecolor": AXIS,
            "spikethickness": 1,
        },
        yaxis={**axis, "showgrid": True, "gridcolor": GRID, "tickformat": "$,.0f"},
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif"},
    )
    st.plotly_chart(fig, width="stretch", theme=None)


def conditions_table(rows: list[dict[str, Any]]) -> str:
    """HTML table of condition checks with a check or cross, never color alone."""

    def mark(p: Any) -> str:
        if p is True:
            return '<span class="ml-ok">✓ pass</span>'
        if p is False:
            return '<span class="ml-no">✗ fail</span>'
        return '<span class="ml-na">n/a</span>'

    def num(v: Any) -> str:
        return f"{v:,.2f}" if isinstance(v, int | float) and pd.notna(v) else "\u2013"

    body = "".join(
        f"<tr><td>{html.escape(r['run'])}</td><td>{html.escape(str(r['condition']))}</td>"
        f'<td class="num">{num(r.get("observed"))}</td>'
        f'<td class="num">{num(r.get("threshold"))}</td><td>{mark(r.get("passed"))}</td></tr>'
        for r in rows
    )
    return (
        '<table class="ml-cond"><thead><tr><th>Run</th><th>Condition</th>'
        '<th style="text-align:right">Observed</th><th style="text-align:right">Threshold</th>'
        f"<th>Result</th></tr></thead><tbody>{body}</tbody></table>"
    )
