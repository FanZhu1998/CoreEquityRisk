"""Validate: run the bias battery and read the scorecard against the blueprint's success criteria."""

import json
from datetime import date
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from common import validation_report
from studio_lib import theme
from studio_lib.state import summary
from studio_lib.widgets import job_button, job_panel

s = summary()
theme.section("04 — Validate", "Model <em>validation</em>",
              "A strictly point-in-time backtest: every forecast made at a close is scored against the next 21 "
              "sessions on non-overlapping periods. Bias statistics near 1 mean the risk was right on average; MRAD "
              "measures how steadily; the scorecard checks each success criterion of the blueprint (§1.3).")

with st.container(border=True):
    c1, c2, c3 = st.columns([2, 2, 3])
    start = c1.date_input("From", value=date(2019, 1, 2), key="val-start")
    end = c2.date_input("To", value=s["last"] or date.today(), key="val-end")
    with c3:
        st.html("<div style='height:1.7rem'></div>")
        job_button("Run validation", ["validate", "--start", str(start), "--end", str(end)], key="validate",
                   primary=True, job_label=f"Validation {start}..{end}", icon=":material/fact_check:")
    job_panel()

rep = validation_report()
if rep is None:
    theme.note("No report yet: run a validation above.")
    st.stop()
sm = rep["summary"]
tables = rep["tables"]
card = sm["scorecard"]
n_pass = sum(r["status"] == "PASS" for r in card)
n_fail = sum(r["status"] == "FAIL" for r in card)
theme.stats([
    ("Window", f"{sm['start']} → {sm['end']}", "ink"),
    ("Forecast periods", str(sm["periods"]), ""),
    ("Criteria passed", f"{n_pass} of {len(card)}", "bull"),
    ("Criteria failed", str(n_fail), "bear" if n_fail else "bull"),
    ("Not yet measurable", str(len(card) - n_pass - n_fail), "ink"),
])

theme.h2("Success criteria (§1.3)")
theme.table(card, ["area", "criterion", "value", "status"], pills=["status"])
theme.h2("External checks (§11.3)")
theme.table(sm["external"], ["area", "criterion", "value", "status"], pills=["status"])

band = (2.0 / max(int(sm["periods"]), 1)) ** 0.5
c1, c2 = st.columns(2, gap="large")
with c1:
    theme.h2("Pure factor portfolios: bias statistic")
    f = tables.get("factor")
    if f is not None and f.height:
        f = f.sort("bias")
        out = [(abs(b - 1) > band) for b in f["bias"].to_list()]
        fig = go.Figure(go.Bar(x=f["bias"].to_list(), y=f["portfolio"].to_list(), orientation="h",
                               marker_color=[theme.C["bear"] if o else theme.C["accent"] for o in out],
                               hovertemplate="%{y}: %{x:.3f}<extra></extra>"))
        fig.add_vrect(x0=1 - band, x1=1 + band, fillcolor="rgba(125,171,221,0.10)", line_width=0)
        fig.add_vline(x=1.0, line_color=theme.C["muted"], line_dash="dot")
        st.plotly_chart(theme.style_fig(fig, 640), width="stretch", config={"displayModeBar": False})
        theme.note(f"Shaded: the 95% band 1 ± {band:.2f} for {sm['periods']} periods. Red bars sit outside it.")
with c2:
    theme.h2("Eigenfactor portfolios: before and after the adjustment")
    e = tables.get("eigen")
    if e is not None and e.height:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=(e["k"] + 1).to_list(), y=e["bias_before"].to_list(), name="EWMA + Newey-West",
                                 mode="lines+markers", line=dict(color=theme.C["muted"])))
        fig.add_trace(go.Scatter(x=(e["k"] + 1).to_list(), y=e["bias_after"].to_list(), name="after eigen adjustment",
                                 mode="lines+markers", line=dict(color=theme.C["accent"])))
        fig.add_hline(y=1.0, line_dash="dot", line_color=theme.C["muted"])
        fig.update_layout(xaxis_title="eigenfactor (1 = smallest eigenvalue)")
        st.plotly_chart(theme.style_fig(fig, 300), width="stretch", config={"displayModeBar": False})
    theme.h2("Specific risk by decile")
    dec = tables.get("specific_deciles")
    if dec is not None and dec.height:
        fig = go.Figure()
        for grouping, dash in (("size decile", "solid"), ("forecast-vol decile", "dash")):
            g = dec.filter(pl.col("grouping") == grouping)
            for col, color in (("full stack", theme.C["accent"]), ("time series only", theme.C["muted"])):
                fig.add_trace(go.Scatter(x=g["decile"].to_list(), y=g[col].to_list(), name=f"{col}, {grouping}",
                                         line=dict(color=color, dash=dash)))
        fig.add_hline(y=1.0, line_dash="dot", line_color=theme.C["muted"])
        st.plotly_chart(theme.style_fig(fig, 300), width="stretch", config={"displayModeBar": False})

with st.expander("All report figures"):
    figs = rep["figures"]
    cols = st.columns(2)
    for i, fig_path in enumerate(figs):
        cols[i % 2].image(fig_path, width="stretch")
with st.expander("Tables"):
    for name in ("market", "industry", "random", "factor"):
        if name in tables:
            st.caption(name)
            st.dataframe(tables[name], hide_index=True, width="stretch")
theme.note(f"Report folder: {theme.esc(rep['dir'])} · {theme.esc(Path(rep['dir'], 'report.md'))}")
if st.checkbox("Show summary.json", key="val-json"):
    st.code(json.dumps(sm, indent=1, default=str)[:20000], language="json")
