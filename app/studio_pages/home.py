"""Today: where the model stands, and one button to bring it up to date."""

from datetime import timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from common import vra
from studio_lib import theme
from studio_lib.state import day_stats, fmt_time, inventory, runner, summary, task_info
from studio_lib.widgets import busy_reason, job_button, job_panel

from eqrisk.model.exposures import STYLES

s = summary()
last = s["last"]
theme.section("01 — Today", f"US large-cap <em>risk model</em> · {theme.esc(s['model_id'])}",
              "Daily factor estimation for the point-in-time S&amp;P 500. Each update loads the latest session, "
              "restages the data, re-estimates exposures, factor returns, the factor covariance and specific risk, "
              "and publishes only if the quality gates pass.")

ds = day_stats(last) if last else {}
lr = s["last_run"]
country = ds.get("country")
theme.stats([
    ("Model as of", str(last) if last else "no data", "ink"),
    ("Latest good (published)", str(s["latest_good"]) if s["latest_good"] else "—", ""),
    ("Last daily update", f"{lr['status']} · {lr['as_of']}" if lr else "none yet",
     "bull" if lr and lr["status"] == "OK" else ("bear" if lr else "")),
    ("Market (country factor)", f"{country:+.2%}" if country is not None else "—",
     "bull" if (country or 0) >= 0 else "bear"),
    ("Factor regime λF / λS", f"{ds['lambda_F']:.2f} / {ds['lambda_S']:.2f}"
     if ds.get("lambda_F") and ds.get("lambda_S") else "—", ""),
    ("Cross-sectional R²", f"{ds['r2']:.1%} · {ds['n']} names" if ds.get("r2") is not None else "—", ""),
])

with st.container(border=True):
    active = runner().active()
    if active is not None:
        job_panel()
    else:
        pending = s["pending"]
        if pending:
            n = len(pending)
            theme.h2(f"Ready to estimate {n} session{'s' if n > 1 else ''}: "
                     + ", ".join(str(d) for d in pending[:5]) + (" …" if n > 5 else ""))
            theme.note("Loads prices, filings and reference data for each session, restages, re-estimates and runs "
                       "the gates. About 5 minutes for one session. API keys come from <b>.env</b>.")
            have_raw = (inventory()["eod_last"] or "") >= str(pending[-1])   # stored prices cover the sessions
            c1, c2, _ = st.columns([2, 2, 3])
            with c1:
                job_button("Run today's update", ["run-daily"], key="run-daily", primary=True, needs_keys=True,
                           job_label="Daily update", icon=":material/play_arrow:")
            if have_raw:
                with c2:
                    job_button("Use stored data only", ["run-daily", "--offline"], key="run-offline",
                               help="Skip downloads and estimate from the raw data already on disk.",
                               job_label="Daily update (offline)")
        else:
            nxt = s["next_session"]
            theme.h2(f"Up to date through {last}.")
            theme.note(f"The next session, <b>{nxt}</b>, can be estimated after {s['ready_after']} ET that day "
                       "(vendors publish end-of-day data in the evening). Re-running a session recomputes it "
                       "from the current raw data and replaces its outputs.")
            c1, _ = st.columns([2, 5])
            with c1:
                if last is not None:
                    job_button(f"Re-run {last}", ["run-daily", "--date", str(last), "--force"], key="rerun-last",
                               needs_keys=True, job_label=f"Re-estimate {last}", icon=":material/replay:")
        if (reason := busy_reason(needs_keys=True)) and "Missing" in reason:
            st.warning(reason + ". Add them to the .env file in the project folder (see Data).",
                       icon=":material/key:")
        job_panel()

task = task_info()
if task:
    sched = (f"<b>{theme.esc(task.get('State'))}</b> · next run {theme.esc(task.get('Next'))} · "
             f"last run {theme.esc(task.get('Last'))}")
else:
    sched = "<b>not registered</b> — set it up under System › Jobs &amp; settings, or keep running updates from here."
theme.note("Scheduled daily job: " + sched)

if last is None:
    st.stop()

left, right = st.columns([3, 2], gap="large")
with left:
    theme.h2(f"Style factor returns on {last} (in units of their trailing volatility)")
    moves = pl.DataFrame(ds["moves"]) if ds.get("moves") else pl.DataFrame()
    if moves.height:
        sty = moves.filter(pl.col("factor").is_in(list(STYLES))).drop_nulls("f_over_sigma").sort("f_over_sigma")
        colors = [theme.C["bull"] if v >= 0 else theme.C["bear"] for v in sty["f_over_sigma"].to_list()]
        fig = go.Figure(go.Bar(x=sty["f_over_sigma"].to_list(), y=sty["factor"].to_list(), orientation="h",
                               marker_color=colors, hovertemplate="%{y}: %{x:+.2f} sd<extra></extra>"))
        st.plotly_chart(theme.style_fig(fig, 380), width="stretch", config={"displayModeBar": False})
        ind = moves.filter(~pl.col("factor").is_in([*STYLES, "COUNTRY"])).drop_nulls("f").sort("f")
        if ind.height:
            lo, hi = ind.head(3), ind.tail(3).reverse()
            theme.note("Industries — best: " + ", ".join(f"{r['factor'].title()} {r['f']:+.2%}" for r in hi.to_dicts())
                       + " · worst: " + ", ".join(f"{r['factor'].title()} {r['f']:+.2%}" for r in lo.to_dicts()))
with right:
    theme.h2("Quality gates of the last daily update")
    if lr and lr["gates"]:
        theme.table([{"gate": g["gate"], "level": g["level"], "result": "pass" if g["ok"] else g["level"].lower(),
                      "detail": g["detail"]} for g in lr["gates"]], ["gate", "result", "detail"], pills=["result"])
        theme.note(f"{theme.esc(lr['command'])} · finished {fmt_time(lr['finished'])}"
                   + (f" · {lr['minutes']:.1f} min" if lr["minutes"] else ""))
    else:
        theme.note("Gates are evaluated by daily updates; the history built by the backfill has none.")

theme.h2("Volatility regime over the last year")
v = vra().filter(pl.col("date") > last - timedelta(days=365))
fig = go.Figure()
for col, name in (("lambda_F", "λF factor"), ("lambda_S", "λS specific")):
    fig.add_trace(go.Scatter(x=v["date"].to_list(), y=v[col].to_list(), name=name, mode="lines"))
fig.add_hline(y=1.0, line_dash="dot", line_color=theme.C["muted"])
st.plotly_chart(theme.style_fig(fig, 260), width="stretch", config={"displayModeBar": False})
