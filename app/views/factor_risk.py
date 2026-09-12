"""Page 3: factor volatilities, correlation heatmap, eigen diagnostics, VRA vs cross-sectional volatility."""

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st
from common import ANN, eigen, factor_risk, pick_date, snapshot, vra
from plotly.subplots import make_subplots

st.title("Factor risk")
d = pick_date()
snap = snapshot(d)
group = st.radio("Group", ["style", "industry", "country"], horizontal=True)
names = [snap.factors[i] for i in snap.groups[group]]
vol = factor_risk().filter(pl.col("factor").is_in(names) & (pl.col("date") <= d)).with_columns(
    vol_annualized=pl.col("vol_final") * ANN)
st.plotly_chart(px.line(vol.to_pandas(), x="date", y="vol_annualized", color="factor"))

st.subheader(f"Correlations on {d}")
s = np.sqrt(np.diag(snap.F))
st.plotly_chart(px.imshow(snap.F / np.outer(s, s), x=snap.factors, y=snap.factors, zmin=-1, zmax=1,
                          color_continuous_scale="RdBu_r", aspect="auto", height=720))

st.subheader("Eigenfactor adjustment (latest simulation)")
e = eigen(d)
if e.height:
    st.caption(f"simulated on {e['date'][0]}; k = 0 is the smallest eigenvalue")
    st.plotly_chart(px.line(e.to_pandas(), x="k", y=["v_k", "gamma_k"], markers=True))

st.subheader("Volatility regime vs cross-sectional factor volatility")
v = vra().filter(pl.col("date") <= d)
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(go.Scatter(x=v["date"].to_list(), y=v["lambda_F"].to_list(), name="lambda_F"), secondary_y=False)
fig.add_trace(go.Scatter(x=v["date"].to_list(), y=v["cs_vol"].to_list(), name="cross-sectional vol",
                         opacity=0.5), secondary_y=True)
st.plotly_chart(fig)
