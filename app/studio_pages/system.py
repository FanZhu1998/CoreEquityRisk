"""System: job history, pipeline runs, the Windows schedule, settings, documentation, shutdown."""

import os
import signal
import subprocess
from datetime import time

import polars as pl
import streamlit as st
from common import ROOT, manifests
from studio_lib import theme
from studio_lib.jobs import Job
from studio_lib.state import (
    TASK_NAME,
    fmt_time,
    project,
    register_task,
    remove_task,
    runner,
    start_task,
    task_info,
)
from studio_lib.widgets import confirm_dialog, job_panel

ICON = ROOT / "app" / "assets" / "eqrisk.ico"
LAUNCHER = ROOT / "EQRisk Studio.bat"

theme.section("System", "Jobs, schedule &amp; <em>settings</em>",
              "Everything the Studio has run, the pipeline's own run records, the Windows scheduled job that can "
              "run the daily update without the Studio, and the model configuration.")

t_jobs, t_runs, t_sched, t_cfg, t_docs = st.tabs(["Studio jobs", "Pipeline runs", "Daily schedule", "Settings",
                                                  "Runbook & decisions"])

with t_jobs:
    job_panel()
    hist = runner().history(limit=40)
    if hist:
        def row(j: Job) -> dict[str, str]:
            return {"started": j.started.replace("T", " "), "job": j.label, "status": j.status,
                    "duration": f"{j.seconds / 60:.1f} min", "command": "eqrisk " + " ".join(j.args)}

        theme.table([row(j) for j in hist], ["started", "job", "status", "duration", "command"], pills=["status"])
        pick = st.selectbox("Open a log", [j.id for j in hist], format_func=lambda i: next(
            f"{j.started.replace('T', ' ')} · {j.label}" for j in hist if j.id == i), key="log-pick")
        job = next(j for j in hist if j.id == pick)
        st.code(runner().tail(job, 400) or "(empty)", language=None)
    else:
        theme.note("No Studio jobs yet.")

with t_runs:
    runs = manifests()
    if runs:
        theme.table([{"started": fmt_time(m.started_at), "command": m.command, "session": str(m.as_of or ""),
                      "status": m.status,
                      "failed gates": ", ".join(g["gate"] for g in m.gates.get("results", []) if not g["ok"]) or "—"}
                     for m in reversed(runs[-60:])], ["started", "command", "session", "status", "failed gates"],
                    pills=["status"])
    theme.note("Each run writes a manifest (config hash, git SHA, gates, timings) under data/model/us_lc_v1/manifests.")

with t_sched:
    info = task_info()
    theme.stats([("Scheduled task", "registered" if info else "not registered", "bull" if info else "ink"),
                 ("State", (info or {}).get("State", "—"), ""), ("Next run", (info or {}).get("Next") or "—", ""),
                 ("Last run", (info or {}).get("Last", "—"), ""),
                 ("Last result", str((info or {}).get("Result", "—")), "")])
    theme.note(f"Windows Task Scheduler runs <code>eqrisk run-daily</code> as task <b>{TASK_NAME}</b>, catching up "
               "after sleep. You can use it, the Studio, or both: the Studio will not start a job while the scheduled "
               "one is running. Last result 0 means success.")
    at = st.time_input("Daily at", value=time(6, 30), key="task-time")
    c1, c2, c3 = st.columns(3)

    def _done(res: subprocess.CompletedProcess[str], ok: str) -> None:
        st.cache_data.clear()
        st.session_state["eq_task_msg"] = ok if res.returncode == 0 else (res.stderr or res.stdout or "failed").strip()

    if c1.button("Register / update", key="task-reg", icon=":material/event_repeat:"):
        confirm_dialog("Register the daily job?",
                       f"Creates the Windows scheduled task <b>{TASK_NAME}</b> running <code>eqrisk run-daily</code> "
                       f"every day at {at:%H:%M}, and as soon as the PC wakes if it missed that time.",
                       lambda: _done(register_task(f"{at:%H:%M}"), "Registered."))
    if c2.button("Run now", key="task-run", disabled=not info, icon=":material/play_arrow:"):
        _done(start_task(), "Started; progress appears in Pipeline runs when it finishes.")
        st.rerun()
    if c3.button("Remove", key="task-del", disabled=not info, icon=":material/event_busy:"):
        confirm_dialog("Remove the daily job?", "Deletes the scheduled task. Data and code are not touched.",
                       lambda: _done(remove_task(), "Removed."))
    if msg := st.session_state.pop("eq_task_msg", None):
        st.info(msg)

with t_cfg:
    cfg = project().config
    fc, sr, src = cfg.factor_cov, cfg.specific_risk, cfg.sources
    cov_txt = (f"vol half-life {fc.vol.half_life} / NW {fc.vol.nw_lags}, corr half-life {fc.corr.half_life} / "
               f"NW {fc.corr.nw_lags}, eigen a = {fc.eigen.a}, VRA half-life {fc.vra.half_life}")
    src_txt = (f"prices {src.prices}, fundamentals {src.fundamentals}, membership {src.membership}, "
               f"risk-free {src.risk_free.series}")
    theme.table([
        {"setting": "Model", "value": f"{cfg.model_id} (preset {cfg.preset}, horizon {cfg.horizon_days} sessions)"},
        {"setting": "Config hash", "value": cfg.config_hash()[:16]},
        {"setting": "Factor covariance", "value": cov_txt},
        {"setting": "Specific risk", "value": f"half-life {sr.ts.half_life}, shrinkage q = {sr.shrinkage.q}"},
        {"setting": "Data sources", "value": src_txt},
        {"setting": "Project folder", "value": str(ROOT)},
        {"setting": "Configuration file", "value": str(project().config_path)},
    ], ["setting", "value"])
    theme.note("Model parameters live in <b>configs/model_us_lc.yaml</b>. After changing them, rebuild the model "
               "history (Estimate) and run a validation.")

    theme.h2("Desktop shortcut")

    def make_icon() -> None:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, 255, 255], radius=48, fill="#080e18", outline="#1a273a", width=8)
        blue, w = "#7dabdd", 12
        d.line([(72, 72), (72, 184)], fill=blue, width=w)
        for y, x2 in ((72, 132), (128, 116), (184, 132)):
            d.line([(72, y), (x2, y)], fill=blue, width=w)
        d.line([(176, 84), (184, 172)], fill=blue, width=w)
        for cx, cy in ((176, 84), (184, 172)):
            d.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=blue)
        img.save(ICON, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])

    if st.button("Create desktop shortcut", key="shortcut", icon=":material/desktop_windows:",
                 disabled=os.name != "nt"):
        make_icon()
        cmd = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
               "(Join-Path ([Environment]::GetFolderPath('Desktop')) 'EQRisk Studio.lnk')); "
               f"$s.TargetPath = '{LAUNCHER}'; $s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{ICON}'; "
               "$s.WindowStyle = 7; $s.Description = 'EQRisk Studio'; $s.Save()")
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                             text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode == 0:
            st.success("Created 'EQRisk Studio' on your desktop.")
        else:
            st.error(res.stderr.strip() or "Could not create the shortcut.")

    theme.h2("Shut down")
    theme.note("Stops the Studio server (running jobs continue in the background and reappear next time).")
    if st.button("Shut down Studio", key="shutdown", icon=":material/power_settings_new:"):
        confirm_dialog("Shut down EQRisk Studio?", "Close the window afterwards. Start it again with EQRisk Studio.bat "
                       "or the desktop shortcut.", lambda: os.kill(os.getpid(), signal.SIGTERM))

with t_docs:
    doc = st.segmented_control("Document", ["RUNBOOK.md", "DECISIONS.md", "README.md"], default="RUNBOOK.md",
                               key="doc-pick")
    path = ROOT / ("README.md" if doc == "README.md" else f"docs/{doc or 'RUNBOOK.md'}")
    if path.exists():
        st.markdown(path.read_text(encoding="utf-8"))
_ = pl
