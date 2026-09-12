"""Page 6: holdings CSV -> total or active risk, groups, x-sigma-rho, MCTR, contributors, beta."""

import polars as pl
import streamlit as st
from common import ANN, SAMPLE_HOLDINGS, pick_date, snapshot

from eqrisk.analytics.risk import holdings_vectors, portfolio_risk

st.title("Portfolio analyzer")
d = pick_date()
snap = snapshot(d)
up = st.file_uploader("Holdings CSV: ticker, weight[, bench_weight]", type="csv")
frame = pl.read_csv(up) if up is not None else pl.read_csv(SAMPLE_HOLDINGS)
if up is None:
    st.caption(f"Showing the sample file {SAMPLE_HOLDINGS.name}.")
h, hb, unmatched = holdings_vectors(snap, frame.with_columns(pl.col("ticker").str.strip_chars().str.to_uppercase()))
if unmatched:
    st.warning("Not in the model on this date: " + ", ".join(unmatched))
rep = portfolio_risk(snap, h, hb)
st.session_state["portfolio_report"] = rep
total = rep.sigma ** 2

c = st.columns(4)
c[0].metric("Active risk (ann.)" if rep.active else "Total risk (ann.)", f"{rep.sigma_ann:.2%}")
c[1].metric("Factor share", f"{rep.factor_var / total:.1%}" if total else "n/a")
c[2].metric("Specific share", f"{rep.specific_var / total:.1%}" if total else "n/a")
c[3].metric("Beta vs benchmark" if rep.active else "Beta vs ESTU", f"{rep.beta:.3f}")

st.subheader("Risk by group")
st.dataframe(rep.groups, hide_index=True)
st.subheader("Factors: exposure, volatility, x-σ-ρ")
st.dataframe(rep.factors.filter(pl.col("exposure").abs() > 1e-12).with_columns(
    vol_ann=pl.col("vol") * ANN, xsr_ann=pl.col("xsr") * ANN).sort(pl.col("xsr").abs(), descending=True)
    .select("factor", "group", "exposure", "vol_ann", "corr", "xsr_ann", "pct_var"), hide_index=True)
st.subheader("Largest risk contributors")
st.dataframe(rep.assets.filter(pl.col("weight") != 0).with_columns(mctr_ann=pl.col("mctr") * ANN)
             .sort(pl.col("contrib").abs(), descending=True).head(20)
             .select("ticker", "weight", "mctr_ann", "pct_risk", "beta"), hide_index=True)
