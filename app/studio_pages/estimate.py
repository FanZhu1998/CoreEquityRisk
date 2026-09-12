"""Estimate: run the model for pending or chosen sessions, rebuild history, and check its health."""

from datetime import timedelta

import numpy as np
import plotly.graph_objects as go
import polars as pl
import streamlit as st
from common import factor_returns, factor_risk, regression_stats
from studio_lib import theme
from studio_lib.state import inventory, runner, summary
from studio_lib.widgets import busy_reason, confirm_dialog, job_button, job_panel

from eqrisk.model.exposures import STYLES

s = summary()
last = s["last"]
theme.section("03 — Estimate", "Factor <em>estimation</em>",
              "A USE4-style fundamental model: 1 country, 20 industries and 12 styles, estimated daily by "
              "cap-weighted constrained regression, with a four-layer factor covariance and five-layer specific risk "
              "at a one-month horizon.")
st.html('<div class="eq-steps">' + "".join(
    f'<div class="eq-step"><div class="eq-step__n">{i:02d}</div><div class="eq-step__t">{t}</div></div>'
    for i, t in enumerate(["Descriptors &amp; exposures", "Factor returns (WLS)", "Factor covariance",
                           "Specific risk", "Quality gates"], 1)) + "</div>")

with st.container(border=True):
    c1, c2 = st.columns(2, gap="large")
    with c1:
        theme.h2("Catch up")
        pend = s["pending"]
        theme.note(f"Pending sessions: <b>{', '.join(map(str, pend)) if pend else 'none'}</b>. The model is estimated "
                   f"through <b>{last}</b>.")
        job_button("Estimate pending sessions", ["run-daily"], key="est-pending", primary=True, needs_keys=True,
                   job_label="Daily update", icon=":material/play_arrow:")
    with c2:
        theme.h2("Re-estimate one session")
        opts = st.segmented_control("Data", ["Fresh download", "Stored data", "Stored staging"], default="Stored data",
                                    key="re-mode")
        day = st.date_input("Session", value=last, max_value=last, key="re-date") if last else None
        extra = {"Fresh download": [], "Stored data": ["--offline"], "Stored staging": ["--offline", "--no-stage"]}
        if day is not None:
            args = ["run-daily", "--date", str(day), "--force", *extra[opts or "Stored data"]]
            job_button(f"Re-estimate {day}", args, key="est-one", needs_keys=opts == "Fresh download",
                       job_label=f"Re-estimate {day}", icon=":material/replay:")
    with st.expander("Rebuild the whole model history"):
        end = inventory()["eod_last"] or str(last)
        theme.note(f"Recomputes exposures, factor returns, covariance and specific risk for every session through "
                   f"<b>{end}</b> (about an hour, eigen adjustment refreshed weekly) and replaces the model tables. "
                   "Needed after changing model parameters in configs/model_us_lc.yaml.")
        if st.button("Rebuild model history", key="rebuild", disabled=busy_reason() is not None,
                     icon=":material/restart_alt:"):
            confirm_dialog("Rebuild the model history?",
                           "This replaces every model table and takes about an hour. Daily sessions are recomputed "
                           "with the backfill's weekly eigen refresh.",
                           lambda: runner().start("Rebuild model history",
                                                  ["backfill", "--stage", "model", "--end", end]))
    job_panel()

if last is None:
    st.stop()

theme.h2("Model health, last twelve months")
since = last - timedelta(days=365)
rs = regression_stats().filter((pl.col("date") > since) & (pl.col("status") == "ok"))
c1, c2 = st.columns(2, gap="large")
with c1:
    fig = go.Figure(go.Scatter(x=rs["date"].to_list(), y=rs["r2_w"].to_list(), mode="lines", name="R²"))
    fig.update_layout(title=dict(text="Cross-sectional R² (cap-weighted)", font=dict(size=13)))
    st.plotly_chart(theme.style_fig(fig, 260), width="stretch", config={"displayModeBar": False})
with c2:
    shown = ["COUNTRY", "BETA", "MOMENTUM", "SIZE"]
    vol = factor_risk().filter((pl.col("date") > since) & pl.col("factor").is_in(shown))
    fig = go.Figure()
    for k in shown:
        sub = vol.filter(pl.col("factor") == k)
        fig.add_trace(go.Scatter(x=sub["date"].to_list(), y=(sub["vol_final"] * np.sqrt(12)).to_list(), name=k))
    fig.update_layout(title=dict(text="Forecast volatility, annualized", font=dict(size=13)), yaxis_tickformat=".0%")
    st.plotly_chart(theme.style_fig(fig, 260), width="stretch", config={"displayModeBar": False})

theme.h2(f"Factor returns on {last}")
fr = factor_returns().filter(pl.col("date") == last).with_columns(
    group=pl.when(pl.col("factor") == "COUNTRY").then(pl.lit("country"))
    .when(pl.col("factor").is_in(list(STYLES))).then(pl.lit("style")).otherwise(pl.lit("industry")))
st.dataframe(fr.select("group", "factor", "f", "t_stat", "f_over_sigma").sort("group", "factor"), hide_index=True,
             width="stretch", height=420, column_config={
                 "f": st.column_config.NumberColumn("return", format="percent"),
                 "t_stat": st.column_config.NumberColumn("t-stat", format="%.2f"),
                 "f_over_sigma": st.column_config.NumberColumn("return / trailing vol", format="%.2f")})
