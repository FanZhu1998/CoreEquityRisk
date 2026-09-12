"""Page 2: cumulative returns by group, the day's returns with t-stats, R², pure factor portfolios."""

import json
from datetime import timedelta

import plotly.express as px
import polars as pl
import streamlit as st
from common import factor_returns, omega, pick_date, regression_stats, snapshot, universe

st.title("Factor returns")
d = pick_date()
snap = snapshot(d)
fr = factor_returns()
c1, c2 = st.columns(2)
group = c1.radio("Group", ["style", "industry", "country"], horizontal=True)
years = c2.select_slider("Window (years)", options=[1, 2, 3, 5, 10], value=3)
start = d - timedelta(days=round(365.25 * years))
names = [snap.factors[i] for i in snap.groups[group]]
cum = (fr.filter(pl.col("factor").is_in(names) & pl.col("date").is_between(start, d)).sort("date")
       .with_columns(cumulative=((1 + pl.col("f")).cum_prod() - 1).over("factor")))
st.plotly_chart(px.line(cum.to_pandas(), x="date", y="cumulative", color="factor"))

st.subheader(f"Returns on {d}")
st.dataframe(fr.filter(pl.col("date") == d).select("factor", "f", "t_stat", "f_over_sigma"), hide_index=True)

st.subheader("Regression fit (cap-weighted R²)")
stats = regression_stats().filter(pl.col("date").is_between(start, d) & (pl.col("status") == "ok"))
st.plotly_chart(px.line(stats.to_pandas(), x="date", y="r2_w"))

st.subheader("Pure factor portfolio (row of Ω)")
k = st.selectbox("Factor", snap.factors, index=snap.factors.index("MOMENTUM") if "MOMENTUM" in snap.factors else 0)
om = omega(d).with_columns(pl.col("factor").cast(pl.String))
row = om.filter(pl.col("factor").is_in([k, str(snap.factors.index(k))]))
if row.height:
    r = row.row(0, named=True)
    tick = dict(universe(d).select("sid", "ticker").iter_rows())
    top = pl.DataFrame([{"ticker": tick.get(sid, str(sid)), "weight": w} for sid, w in json.loads(r["top"])])
    st.caption(f"gross {r['gross']:.2f}, net {r['net']:.3f}; largest holdings of the return-ending-{d} regression")
    st.dataframe(top, hide_index=True)
else:
    st.info(f"No regression summary for {k} on {d}.")
