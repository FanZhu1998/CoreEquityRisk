"""Page 1: last run, gate results, watermarks, lambda_F and lambda_S, run history, quarantine banner."""

import plotly.express as px
import polars as pl
import streamlit as st
from common import latest, manifests, pick_date, store, vra

st.title("Status")
pick_date()
pointer = store().latest_good_pointer()
runs = manifests()
daily = [m for m in runs if m.command.startswith("run-daily")]
last = daily[-1] if daily else None
if last is not None and last.status == "QUARANTINED":
    st.error(f"Session {last.as_of} is QUARANTINED. Consumers stay on LATEST_GOOD "
             f"{pointer['as_of'] if pointer else '(none)'}; see docs/RUNBOOK.md §3.")

v = vra()
lam_f, lam_s = v.drop_nulls("lambda_F"), v.drop_nulls("lambda_S")
cols = st.columns(4)
cols[0].metric("LATEST_GOOD", pointer["as_of"] if pointer else str(latest()))
cols[1].metric("Last daily run", f"{last.as_of} {last.status}" if last else "none yet")
cols[2].metric("λ_F", f"{lam_f['lambda_F'][-1]:.2f}" if lam_f.height else "n/a")
cols[3].metric("λ_S", f"{lam_s['lambda_S'][-1]:.2f}" if lam_s.height else "n/a")

st.subheader("Gate results" + (f" for {last.as_of}" if last else ""))
if last and last.gates.get("results"):
    st.dataframe(pl.DataFrame(last.gates["results"]), hide_index=True)
else:
    st.info("Gates are evaluated by `eqrisk run-daily`; the backfill has no gate results.")

st.subheader("Volatility regime multipliers")
st.plotly_chart(px.line(v.to_pandas(), x="date", y=["lambda_F", "lambda_S"]))

st.subheader("Run history")
st.dataframe(pl.DataFrame([
    {"started": m.started_at, "command": m.command, "as_of": m.as_of, "status": m.status,
     "minutes": round((m.finished_at - m.started_at).total_seconds() / 60, 1) if m.finished_at else None}
    for m in reversed(runs[-50:])]), hide_index=True)

st.subheader("Data watermarks")
st.json(store().watermarks())
