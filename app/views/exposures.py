"""Page 4: ticker profile and history, per-style distributions, industry table with N_eff."""

import plotly.express as px
import polars as pl
import streamlit as st
from common import exposure_history, exposures, pick_date, universe

from eqrisk.model.exposures import STYLES

st.title("Exposures")
d = pick_date()
ex = exposures(d)
tickers = sorted(ex["ticker"].drop_nulls().unique().to_list())
t = st.selectbox("Ticker", tickers, index=tickers.index("AAPL") if "AAPL" in tickers else 0)
row = ex.filter(pl.col("ticker") == t).row(0, named=True)
st.markdown(f"**{t}**: {row['industry']}, {'in' if row['in_estu'] else 'not in'} the estimation universe")
st.plotly_chart(px.bar(pl.DataFrame({"style": list(STYLES), "exposure": [row[s] for s in STYLES]}).to_pandas(),
                       x="style", y="exposure"))

chosen = st.multiselect("History", list(STYLES), default=["SIZE", "BETA", "MOMENTUM"])
if chosen:
    hist = exposure_history(int(row["sid"])).select("date", *chosen).unpivot(
        index="date", variable_name="style", value_name="exposure")
    st.plotly_chart(px.line(hist.to_pandas(), x="date", y="exposure", color="style"))

st.subheader("Cross-sectional distribution")
style = st.selectbox("Style", list(STYLES))
st.plotly_chart(px.histogram(ex.to_pandas(), x=style, color="in_estu", nbins=60, barmode="overlay"))

st.subheader("Industries (estimation universe)")
estu = universe(d).filter(pl.col("in_estu"))
st.dataframe(estu.group_by("industry").agg(
    names=pl.len(), cap_weight=pl.col("capw").sum(),
    n_eff=pl.col("capw").sum() ** 2 / (pl.col("capw") ** 2).sum()).sort("cap_weight", descending=True),
    hide_index=True)
