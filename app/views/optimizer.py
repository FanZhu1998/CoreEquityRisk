"""Page 8: Riskfolio (injected covariance) and native factor-form optimization (§12) against the
cap-weighted estimation universe, with exposure bands, a tracking-error cap and a turnover limit."""

import numpy as np
import pandas as pd
import polars as pl
import streamlit as st
from common import ANN, pick_date, snapshot

from eqrisk.analytics.risk import market_portfolio, portfolio_risk

st.title("Optimizer")
d = pick_date()
snap = snapshot(d)
w_b = market_portfolio(snap)
styles, inds = list(snap.groups["style"]), list(snap.groups["industry"])
c1, c2, c3 = st.columns(3)
method = c1.radio("Method", ["Factor form (cvxpy)", "Riskfolio-Lib"])
tilt = c1.selectbox("Alpha", ["none (minimum active risk)", *[snap.factors[k] for k in styles]])
te = c2.slider("Tracking-error cap, annualized %", 0.5, 10.0, 3.0, 0.5)
style_band = c2.slider("Style exposure band ±", 0.0, 0.5, 0.10, 0.05)
ind_band = c2.slider("Industry exposure band ±", 0.0, 0.10, 0.02, 0.01)
w_max = c3.slider("Maximum weight", 0.01, 0.20, 0.05, 0.01)
turnover = c3.slider("Turnover limit vs benchmark (L1)", 0.1, 2.0, 1.0, 0.1)
st.caption("Benchmark: the estimation universe, cap-weighted. Riskfolio minimizes total risk under the bands; "
           "its tracking error is historical, so the TE cap applies to the factor form only (§12.3).")

if st.button("Optimize"):
    alpha = None if tilt.startswith("none") else snap.X[:, snap.factors.index(tilt)] * 0.01
    with st.spinner("solving"):
        if method.startswith("Factor"):
            from eqrisk.optimize.factor_form import optimize_active

            w, status = optimize_active(snap.X, snap.F, snap.spec_var, w_b, alpha=alpha, te_max_ann=te / 100,
                                        style_idx=styles, style_bound=style_band, ind_idx=inds, ind_bound=ind_band,
                                        w_max=w_max, w_prev=w_b, turnover_max=turnover)
        else:
            from eqrisk.optimize.riskfolio_adapter import exposure_bounds, to_riskfolio

            ids = [str(s) for s in snap.sids]
            X = pd.DataFrame(snap.X, index=ids, columns=snap.factors)
            port = to_riskfolio(X, pd.DataFrame(snap.F, index=snap.factors, columns=snap.factors),
                                pd.Series(snap.spec_var, index=ids))
            bounds = {snap.factors[k]: (-style_band, style_band) for k in styles}
            port.ainequality, port.binequality = exposure_bounds(X, bounds, pd.Series(w_b, index=ids))
            port.upperlng = w_max
            res = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)
            w, status = (None, "infeasible") if res is None else (res["weights"].reindex(ids).to_numpy(), "optimal")
    if w is None:
        st.error(f"No solution: {status}")
    else:
        w = np.clip(np.asarray(w, dtype=float), 0.0, None)
        rep = portfolio_risk(snap, w, w_b)
        st.session_state["optimizer_report"] = rep
        c = st.columns(4)
        c[0].metric("Status", status)
        c[1].metric("Active risk (ann.)", f"{rep.sigma_ann:.2%}")
        c[2].metric("Names held", int((w > 1e-6).sum()))
        c[3].metric("Turnover vs benchmark", f"{np.abs(w - w_b).sum():.2f}")
        st.subheader("Active exposures")
        st.dataframe(rep.factors.select("factor", "group", "exposure", "pct_var").with_columns(
            vol_ann=rep.factors["vol"] * ANN), hide_index=True)
        st.subheader("Holdings")
        st.dataframe(pl.DataFrame({"ticker": snap.tickers, "weight": w, "benchmark": w_b})
                     .filter(pl.col("weight") > 1e-6).sort("weight", descending=True), hide_index=True)
