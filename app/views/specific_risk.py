"""Page 5: distribution, time series vs structural vs blended vs final, gamma, bias by size decile."""

import plotly.express as px
import polars as pl
import streamlit as st
from common import ANN, pick_date, specific, validation_report

LAYERS = ["sigma_ts", "sigma_str", "sigma_blend", "sigma_sh", "sigma_final"]

st.title("Specific risk")
d = pick_date()
sp = specific(d).with_columns([(pl.col(c) * ANN).alias(c) for c in LAYERS]).to_pandas()
st.caption("Annualized; sigma_final is the model's forecast (Layers 1-5).")
st.plotly_chart(px.histogram(sp, x="sigma_final", color="in_estu", nbins=60, barmode="overlay"))
c1, c2 = st.columns(2)
c1.plotly_chart(px.scatter(sp, x="sigma_ts", y="sigma_final", color="gamma", hover_name="ticker",
                           title="time series vs final"))
c2.plotly_chart(px.histogram(sp, x="gamma", nbins=40, title="gamma (weight on the time-series estimate)"))

tickers = sorted(t for t in sp["ticker"].dropna().unique())
t = st.selectbox("Ticker", tickers, index=tickers.index("AAPL") if "AAPL" in tickers else 0)
st.dataframe(sp[sp["ticker"] == t][["ticker", *LAYERS, "gamma", "c_nw", "lambda_S", "size_decile"]], hide_index=True)

st.subheader("Bias by decile")
rep = validation_report()
if rep and "specific_deciles" in rep["tables"]:
    st.caption(f"from {rep['dir']}")
    st.dataframe(rep["tables"]["specific_deciles"], hide_index=True)
else:
    st.info("Run `eqrisk validate` for the decile bias statistics.")
