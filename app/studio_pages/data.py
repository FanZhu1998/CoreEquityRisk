"""Data: API keys from .env, connection checks, what is on disk, loading data, data-quality exceptions."""

from datetime import date, timedelta

import polars as pl
import streamlit as st
from studio_lib import theme
from studio_lib.state import exceptions, inventory, key_status, project
from studio_lib.widgets import job_button, job_panel

theme.section("02 — Data", "Data &amp; <em>connections</em>",
              "The model reads prices from EODHD, filings from SEC EDGAR, the risk-free rate from FRED and index "
              "membership from the public fja05680 history. Keys live in the <b>.env</b> file in the project "
              "folder; the Studio only checks that they are there and never shows them.")

theme.h2("API keys (.env)")
theme.table(key_status(), ["key", "purpose", "required", "status"], pills=["status"])
c1, c2, _ = st.columns([2, 2, 4])
if c1.button("Test connections", icon=":material/network_check:", key="doctor"):
    from eqrisk.pipeline.doctor import run_doctor

    with st.spinner("Checking every source (about 20 seconds)…"):
        st.session_state["eq_doctor"] = run_doctor(project())
if c2.button("Reload .env", icon=":material/refresh:", key="reload-env", help="Pick up keys you just edited."):
    st.cache_resource.clear()
    st.rerun()
if doctor := st.session_state.get("eq_doctor"):
    theme.table([r for r in doctor if not r["check"].startswith("env ")], ["check", "status", "detail"],
                pills=["status"])

inv = inventory()
theme.h2("On disk")
theme.stats([
    ("Price sessions", f"{inv['eod_sessions']:,}", "ink"),
    ("Prices from / to", f"{inv['eod_first'] or '—'} → {inv['eod_last'] or '—'}", ""),
    ("Companies with SEC filings", f"{inv['edgar_companies']:,}", ""),
    ("Staged through", str(inv["staged_last"] or "—"), ""),
    ("Data folder", f"{inv['disk_gb']:.1f} GB", "ink"),
])
with st.expander("Reference snapshots and watermarks"):
    theme.table(inv["refs"], ["dataset", "source", "snapshot"])
    theme.table([{"watermark": k, "value": v} for k, v in sorted(inv["watermarks"].items())], ["watermark", "value"])

theme.h2("Load data")
theme.note("A daily update (Today) already downloads everything it needs. Use these for history, gaps or repairs; "
           "each runs in the background and appears below. Raw data is never overwritten: a changed download is "
           "stored as a new version.")
t_one, t_range, t_over, t_stage = st.tabs(["One session", "Date range", "Override files", "Rebuild staging"])
with t_one:
    d = st.date_input("Session", value=date.today() - timedelta(days=1), key="ing-date")
    job_button("Download this session", ["ingest", "--date", str(d)], key="ingest", needs_keys=True,
               job_label=f"Ingest {d}", icon=":material/download:")
with t_range:
    c1, c2 = st.columns(2)
    a = c1.date_input("From", value=project().config.history.price_start, key="bf-start")
    b = c2.date_input("To", value=date.today(), key="bf-end")
    theme.note("Downloads prices for every constituent in the window and SEC filings for new companies. A full "
               "ten-year history takes a few hours, mostly EDGAR's rate limit; it resumes where it stopped.")
    job_button("Download history", ["backfill", "--stage", "ingest", "--start", str(a), "--end", str(b)],
               key="bf-ingest", needs_keys=True, job_label=f"Backfill ingest {a}..{b}", icon=":material/history:")
with t_over:
    theme.note("After editing <b>configs/overrides/</b> (ticker map, CIK links, share counts, industries), download "
               "what the files name, then rebuild staging.")
    job_button("Pull override targets", ["pull-overrides"], key="overrides", needs_keys=True,
               job_label="Pull overrides", icon=":material/rule:")
with t_stage:
    end = inv["eod_last"] or str(date.today())
    theme.note(f"Rebuilds every staged table (identity, returns, market caps, fundamentals, universe) from raw data "
               f"through <b>{end}</b>. About a minute; the model is not touched.")
    job_button("Rebuild staging", ["backfill", "--stage", "stage", "--end", end], key="stage",
               job_label="Rebuild staging", icon=":material/construction:")
job_panel()

theme.h2("Data-quality exceptions")
exc = exceptions()
if exc.height:
    counts = exc.group_by("area", "issue").len().sort("len", descending=True)
    theme.table(counts.rename({"len": "count"}).to_dicts(), ["area", "issue", "count"])
    pick = st.pills("Show", counts["issue"].to_list(), selection_mode="multi", key="exc-pick")
    if pick:
        st.dataframe(exc.filter(pl.col("issue").is_in(pick)), hide_index=True, width="stretch", height=320)
    theme.note("Most are fixed with a row in <b>configs/overrides/</b>; see docs/DECISIONS.md D-008 and D-011.")
else:
    theme.note("No staged exceptions table yet.")
