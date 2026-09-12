"""Look and feel: the fan-zhu.com navy palette and fonts, plus small HTML building blocks."""
# ruff: noqa: E501
# (the CSS block below keeps one rule per line)

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import streamlit as st

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MARK = ASSETS / "eqrisk-mark.svg"

# Tokens from fanzhu-site/assets/css/style.css (dark theme).
C = {"page": "#080e18", "surface": "#0d1523", "surface2": "#121d2e", "surface3": "#1a273a",
     "ink": "#e8eef7", "ink2": "#a7b8ce", "muted": "#7f92a9", "accent": "#7dabdd", "accent_hi": "#a7c8ee",
     "accent2": "#628ab8", "bull": "#46bf94", "bear": "#e5776c", "warn": "#e2b86b",
     "line": "rgba(232,238,247,0.10)", "line2": "rgba(232,238,247,0.16)"}
RAMP = ["#0f1e33", "#16304f", "#1c4470", "#235c92", "#4d8bc4", "#7fb0dd", "#b0d2f0"]

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400..700&family=Libre+Franklin:wght@300..800&display=swap');
:root {
  --eq-page:#080e18; --eq-surface:#0d1523; --eq-surface2:#121d2e; --eq-surface3:#1a273a;
  --eq-ink:#e8eef7; --eq-ink2:#a7b8ce; --eq-muted:#7f92a9; --eq-accent:#7dabdd; --eq-accent-hi:#a7c8ee;
  --eq-bull:#46bf94; --eq-bear:#e5776c; --eq-warn:#e2b86b;
  --eq-line:rgba(232,238,247,0.10); --eq-line2:rgba(232,238,247,0.16);
  --eq-mono:'IBM Plex Mono', ui-monospace, Consolas, monospace;
  --eq-display:'Libre Franklin', 'Franklin Gothic Medium', 'Segoe UI', sans-serif;
}
h1, h2, h3, h4 { font-family: var(--eq-display) !important; letter-spacing: -0.02em; }
[data-testid="stHeader"] { background: rgba(8,14,24,0.86); backdrop-filter: blur(14px) saturate(140%);
  border-bottom: 1px solid var(--eq-line); }
[data-testid="stMainBlockContainer"], .block-container { max-width: 1320px; padding-top: 4.6rem; }
footer { visibility: hidden; }

.eq-eyebrow { font-family: var(--eq-mono); font-size: .72rem; font-weight: 500; letter-spacing: .18em;
  text-transform: uppercase; color: var(--eq-accent); display: flex; align-items: center; gap: .75rem;
  margin: .2rem 0 .7rem; }
.eq-eyebrow::after { content: ""; height: 1px; flex: 1; background: linear-gradient(90deg, var(--eq-line2), transparent); }
.eq-title { font-family: var(--eq-display); font-weight: 300; font-size: clamp(1.9rem, 3.1vw, 2.55rem);
  line-height: 1.08; letter-spacing: -0.03em; color: var(--eq-ink); margin: 0 0 .55rem; }
.eq-title em { font-style: normal; font-weight: 700; color: var(--eq-accent); }
.eq-lede { color: var(--eq-ink2); max-width: 76ch; font-size: .98rem; line-height: 1.65; margin: 0 0 1.2rem; }
.eq-h2 { font-family: var(--eq-display); font-weight: 600; font-size: 1.12rem; color: var(--eq-ink);
  margin: 1.2rem 0 .5rem; letter-spacing: -0.01em; }
.eq-note { color: var(--eq-muted); font-size: .82rem; line-height: 1.6; }
.eq-note b, .eq-note strong { color: var(--eq-ink2); font-weight: 600; }

.eq-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 1px;
  background: var(--eq-line); border: 1px solid var(--eq-line); border-radius: 4px; overflow: hidden; margin: .3rem 0 1.3rem; }
.eq-stat { background: var(--eq-surface); padding: 1rem 1.2rem .95rem; }
.eq-stat__val { font-family: var(--eq-display); font-size: 1.5rem; font-weight: 600; color: var(--eq-accent);
  letter-spacing: -0.02em; font-variant-numeric: tabular-nums; line-height: 1.1; margin-bottom: .4rem; }
.eq-stat__val.bull { color: var(--eq-bull); } .eq-stat__val.bear { color: var(--eq-bear); }
.eq-stat__val.ink { color: var(--eq-ink); } .eq-stat__val.warn { color: var(--eq-warn); }
.eq-stat__lab { font-family: var(--eq-mono); font-size: .64rem; letter-spacing: .09em; text-transform: uppercase;
  color: var(--eq-muted); line-height: 1.45; }

.eq-steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 1px;
  background: var(--eq-line); border: 1px solid var(--eq-line); border-radius: 4px; overflow: hidden; margin: .4rem 0 .9rem; }
.eq-step { background: var(--eq-surface2); padding: .8rem .95rem; }
.eq-step__n { font-family: var(--eq-mono); font-size: .64rem; letter-spacing: .11em; color: var(--eq-muted); margin-bottom: .3rem; }
.eq-step__t { font-size: .88rem; font-weight: 600; color: var(--eq-ink2); }
.eq-step.done .eq-step__n, .eq-step.done .eq-step__t { color: var(--eq-bull); }
.eq-step.now { box-shadow: inset 0 -2px 0 var(--eq-accent); }
.eq-step.now .eq-step__n, .eq-step.now .eq-step__t { color: var(--eq-accent); }

table.eq-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
table.eq-table th { font-family: var(--eq-mono); font-size: .63rem; letter-spacing: .08em; text-transform: uppercase;
  color: var(--eq-muted); font-weight: 500; text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--eq-line2); }
table.eq-table td { padding: .5rem .7rem; border-bottom: 1px solid var(--eq-line); color: var(--eq-ink2);
  font-variant-numeric: tabular-nums; vertical-align: top; }
table.eq-table td:first-child { color: var(--eq-ink); }
.eq-pill { font-family: var(--eq-mono); font-size: .62rem; letter-spacing: .08em; padding: .16rem .5rem;
  border-radius: 2px; border: 1px solid currentColor; white-space: nowrap; text-transform: uppercase; }
.eq-pill.pass { color: var(--eq-bull); } .eq-pill.fail { color: var(--eq-bear); } .eq-pill.warn { color: var(--eq-warn); }
.eq-pill.info, .eq-pill.notrun { color: var(--eq-muted); } .eq-pill.run { color: var(--eq-accent); }

[data-testid="stMetricLabel"] p { font-family: var(--eq-mono); font-size: .66rem !important; letter-spacing: .09em;
  text-transform: uppercase; color: var(--eq-muted); }
[data-testid="stMetricValue"] { font-family: var(--eq-display); color: var(--eq-accent); font-variant-numeric: tabular-nums; }
.stButton button, .stDownloadButton button, .stLinkButton a, .stFormSubmitButton button {
  font-family: var(--eq-mono); letter-spacing: .09em; text-transform: uppercase; font-size: .72rem; }
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"] { color: var(--eq-page); font-weight: 600; }
[data-testid="stCode"] pre, [data-testid="stCode"] code { font-family: var(--eq-mono) !important; font-size: .76rem; }
[data-testid="stExpander"] summary p { font-family: var(--eq-mono); font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; }
</style>
"""


def inject_css() -> None:
    st.html(_CSS)


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def section(eyebrow: str, title_html: str, lede: str | None = None) -> None:
    """Website-style section head. `title_html` may use <em> for the accented word."""
    st.html(f'<div class="eq-eyebrow">{esc(eyebrow)}</div><h1 class="eq-title">{title_html}</h1>'
            + (f'<p class="eq-lede">{lede}</p>' if lede else ""))


def h2(text: str) -> None:
    st.html(f'<div class="eq-h2">{esc(text)}</div>')


def note(text_html: str) -> None:
    st.html(f'<div class="eq-note">{text_html}</div>')


def stats(items: Iterable[tuple[str, str, str]]) -> None:
    """A stat rail: (label, value, tone) with tone in '', 'bull', 'bear', 'ink', 'warn'."""
    cells = "".join(f'<div class="eq-stat"><div class="eq-stat__val {esc(tone)}">{esc(val)}</div>'
                    f'<div class="eq-stat__lab">{esc(lab)}</div></div>' for lab, val, tone in items)
    st.html(f'<div class="eq-stats">{cells}</div>')


def steps(names: Sequence[str], current: int, finished: bool) -> None:
    """Progress strip: steps before `current` are done; `current` is running (or done if finished)."""
    cells = []
    for i, name in enumerate(names):
        cls = "done" if i < current or (finished and i <= current) else ("now" if i == current else "")
        mark = "✓ " if cls == "done" else ""
        cells.append(f'<div class="eq-step {cls}"><div class="eq-step__n">{mark}{i + 1:02d}</div>'
                     f'<div class="eq-step__t">{esc(name)}</div></div>')
    st.html(f'<div class="eq-steps">{"".join(cells)}</div>')


_PILL = {"pass": "pass", "ok": "pass", "succeeded": "pass", "configured": "pass", "fail": "fail", "failed": "fail",
         "quarantined": "fail", "missing": "fail", "warn": "warn", "skipped": "warn", "stopped": "warn",
         "not run": "notrun", "info": "info", "ended": "info", "running": "run", "registered": "pass"}


def pill(status: Any) -> str:
    s = str(status)
    return f'<span class="eq-pill {_PILL.get(s.lower(), "info")}">{esc(s)}</span>'


def table(rows: Sequence[dict[str, Any]], columns: Sequence[str], pills: Iterable[str] = (),
          headers: dict[str, str] | None = None) -> None:
    """A light HTML table in the site's style; columns named in `pills` render as status pills."""
    pills = set(pills)
    head = "".join(f"<th>{esc((headers or {}).get(c, c))}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{pill(r.get(c)) if c in pills else esc(r.get(c))}</td>" for c in columns)
                   + "</tr>" for r in rows)
    st.html(f'<table class="eq-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def style_fig(fig: Any, height: int = 320) -> Any:
    """Plotly figure on the navy surface with the site's type and hairlines."""
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, Segoe UI, sans-serif", color=C["ink2"]),
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11)),
                      colorway=[C["accent"], C["bull"], C["bear"], C["accent_hi"], C["warn"], C["accent2"]])
    fig.update_xaxes(gridcolor="rgba(232,238,247,0.06)", zerolinecolor="rgba(232,238,247,0.16)")
    fig.update_yaxes(gridcolor="rgba(232,238,247,0.06)", zerolinecolor="rgba(232,238,247,0.16)")
    return fig
