"""Page 8: Riskfolio (injected covariance) and native factor-form optimization (§12) against the
cap-weighted estimation universe, with exposure bands, a tracking-error cap and a turnover limit.
The solving lives in eqrisk.optimize.service, which the desktop app calls too."""

import numpy as np
import polars as pl
import streamlit as st
from common import ANN, pick_date, snapshot

from eqrisk.optimize.service import OptimizeSpec, optimize_portfolio

st.title("Optimizer")
d = pick_date()
snap = snapshot(d)
styles = list(snap.groups["style"])
c1, c2, c3 = st.columns(3)
method = c1.radio("Method", ["Factor form (cvxpy)", "Riskfolio-Lib"])
tilt = c1.selectbox("Alpha", ["none (minimum active risk)", *[snap.factors[k] for k in styles]])
alpha_pct = c1.number_input("Alpha per unit of exposure, % a month", 0.0, 5.0, 1.0, 0.25,
                            disabled=tilt.startswith("none"))
te = c2.slider("Tracking-error cap, annualized %", 0.5, 10.0, 3.0, 0.5)
style_band = c2.slider("Style exposure band ±", 0.0, 0.5, 0.10, 0.05)
ind_band = c2.slider("Industry exposure band ±", 0.0, 0.10, 0.02, 0.01)
w_max = c3.slider("Maximum weight", 0.01, 0.20, 0.05, 0.01)
turnover = c3.slider("Turnover limit vs benchmark (L1)", 0.1, 2.0, 1.0, 0.1)
st.caption("Benchmark: the estimation universe, cap-weighted. Riskfolio minimizes total risk under the bands; "
           "its tracking error is historical, so the TE cap applies to the factor form only (§12.3).")

if st.button("Optimize"):
    spec = OptimizeSpec(method="factor" if method.startswith("Factor") else "riskfolio", te_max_ann=te / 100,
                        style_band=style_band, ind_band=ind_band, w_max=w_max, turnover_max=turnover,
                        alpha_factor=None if tilt.startswith("none") else tilt, alpha_per_unit=alpha_pct / 100)
    with st.spinner("solving"):
        res = optimize_portfolio(snap, spec)
    if res.weights is None or res.report is None:
        st.error(f"No solution: {res.status}")
    else:
        w, w_b, rep = res.weights, res.benchmark, res.report
        st.session_state["optimizer_report"] = rep
        c = st.columns(4)
        c[0].metric("Status", res.status)
        c[1].metric("Active risk (ann.)", f"{rep.sigma_ann:.2%}")
        c[2].metric("Names held", int((w > 1e-6).sum()))
        c[3].metric("Turnover vs benchmark", f"{np.abs(w - w_b).sum():.2f}")
        st.subheader("Active exposures")
        st.dataframe(rep.factors.select("factor", "group", "exposure", "pct_var").with_columns(
            vol_ann=rep.factors["vol"] * ANN), hide_index=True)
        st.subheader("Holdings")
        st.dataframe(pl.DataFrame({"ticker": snap.tickers, "weight": w, "benchmark": w_b})
                     .filter(pl.col("weight") > 1e-6).sort("weight", descending=True), hide_index=True)
