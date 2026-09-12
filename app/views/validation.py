"""Page 7: bias statistics, rolling bias and MRAD, eigenfactor smile, deciles, Ken French correlations."""

import polars as pl
import streamlit as st
from common import validation_report

st.title("Validation")
rep = validation_report()
if rep is None:
    st.info("No validation report yet: run `eqrisk validate`.")
    st.stop()
s = rep["summary"]
st.caption(f"{s['start']} to {s['end']}, {s['periods']} non-overlapping periods ({rep['dir']})")
st.subheader("Success criteria (§1.3)")
st.dataframe(pl.DataFrame(s["scorecard"]), hide_index=True)
st.subheader("External checks (§11.3)")
st.dataframe(pl.DataFrame(s["external"]), hide_index=True)
for fig in rep["figures"]:
    st.image(fig)
tables = rep["tables"]
for name, title in (("factor", "Pure factor portfolios"), ("market", "Estimation universe portfolios"),
                    ("industry", "Industry portfolios"), ("specific_deciles", "Specific risk by decile")):
    if name in tables:
        st.subheader(title)
        st.dataframe(tables[name], hide_index=True)
