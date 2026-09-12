"""Cached reads for the Studio pages. Nothing here writes data; background jobs do that."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st
from common import CONFIG, ROOT, factor_returns, regression_stats, vra

from eqrisk.config import Project, load_project, load_settings
from studio_lib.jobs import JobRunner

TASK_NAME = "EQRisk Daily"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# .env keys the pipeline reads, what each is for, and whether the daily run needs it.
KEYS = [("eodhd_api_key", "EODHD_API_KEY", "Daily prices and volume", True),
        ("fred_api_key", "FRED_API_KEY", "Risk-free rate (FRED DTB3)", True),
        ("sec_user_agent", "SEC_USER_AGENT", "SEC EDGAR filings (your name and email)", True),
        ("databento_api_key", "DATABENTO_API_KEY", "Databento prices (optional, metered)", False),
        ("nasdaq_data_link_api_key", "NASDAQ_DATA_LINK_API_KEY", "Sharadar via Nasdaq Data Link (optional)", False)]

# Reference datasets shown on the Data page: (label, source, dataset).
REFERENCE = [("S&P 500 membership", "fja05680", "components"), ("Risk-free rate", "fred", "DTB3"),
             ("Ken French factors", "famafrench", "daily"), ("Vendor symbol list", "eodhd", "listings"),
             ("SEC ticker map", "edgar", "company_tickers"), ("SEC company names", "edgar", "cik_lookup")]


@st.cache_resource
def project() -> Project:
    return load_project(ROOT, CONFIG)


@st.cache_resource
def runner() -> JobRunner:
    return JobRunner(ROOT)


def key_status() -> list[dict[str, Any]]:
    """Which .env keys are set, re-read on every call so an edited .env shows up at once. Values
    never leave pydantic's SecretStr."""
    present = load_settings(ROOT).presence()
    return [{"key": env, "purpose": purpose, "required": "yes" if needed else "optional",
             "status": "configured" if present.get(field) else "missing"} for field, env, purpose, needed in KEYS]


def missing_required_keys() -> list[str]:
    return [k["key"] for k in key_status() if k["required"] == "yes" and k["status"] == "missing"]


@st.cache_data(ttl=30, show_spinner=False)
def summary() -> dict[str, Any]:
    """Where the model stands: last processed session, what is pending, the latest daily run."""
    from eqrisk.calendar import get_calendar
    from eqrisk.manifest import read_manifests
    from eqrisk.pipeline.daily import now_et, processed_dates, read_latest_good, target_session

    p = project()
    cal = get_calendar(p.config.calendar)
    done = processed_dates(p)
    last = max(done) if done else None
    now = now_et()
    target = target_session(p, now)
    pending = cal.sessions(cal.next_session(last), target) if last is not None and last < target else []
    runs = [m for m in read_manifests(p.model_dir) if m.command.startswith("run-daily")]
    last_run = None
    if runs:
        m = runs[-1]
        secs = m.counts.get("timings", {}).get("total_s")
        last_run = {"as_of": m.as_of, "status": m.status, "finished": m.finished_at,
                    "minutes": secs / 60 if secs else None, "gates": m.gates.get("results", []), "command": m.command}
    pointer = read_latest_good(p)
    return {"last": last, "target": target, "pending": pending, "now_et": now.replace(tzinfo=None),
            "next_session": cal.next_session(last) if last is not None else None,
            "latest_good": date.fromisoformat(pointer["as_of"]) if pointer else last,
            "last_run": last_run, "ready_after": p.config.pipeline.vendor_ready_after_et,
            "daily_runs": len(runs), "model_id": p.config.model_id}


@st.cache_data(show_spinner=False)
def day_stats(d: date) -> dict[str, Any]:
    fr = factor_returns().filter(pl.col("date") == d)
    rs = regression_stats().filter(pl.col("date") == d)
    v = vra().filter(pl.col("date") <= d).tail(1)
    country = fr.filter(pl.col("factor") == "COUNTRY")
    return {"country": float(country["f"][0]) if country.height else None,
            "r2": float(rs["r2_w"][0]) if rs.height and rs["r2_w"][0] is not None else None,
            "n": int(rs["n"][0]) if rs.height else None,
            "lambda_F": float(v["lambda_F"][0]) if v.height and v["lambda_F"][0] is not None else None,
            "lambda_S": float(v["lambda_S"][0]) if v.height and v["lambda_S"][0] is not None else None,
            "moves": fr.select("factor", "f", "t_stat", "f_over_sigma").to_dicts()}


def _dir_size(path: Path) -> int:
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(base, f))
    return total


@st.cache_data(ttl=300, show_spinner=False)
def inventory() -> dict[str, Any]:
    """What is on disk: raw price partitions, EDGAR companies, reference snapshots, staged range."""
    from eqrisk.store import latest_dated_dir, raw_partitions

    p = project()
    raw = p.raw_dir
    eod = raw_partitions(raw / "eodhd" / "eod", "date")
    refs = []
    for label, src, ds in REFERENCE:
        hit = latest_dated_dir(raw / src / ds)
        refs.append({"dataset": label, "source": src, "snapshot": str(hit[0]) if hit else "none"})
    staged_last = None
    uni = p.staged_dir / "universe"
    if uni.exists():
        staged_last = pl.scan_parquet(str(uni / "**" / "*.parquet"), hive_partitioning=False).select(
            pl.col("date").max()).collect().item()
    wm_path = raw / "_state" / "watermarks.json"
    return {"eod_first": eod[0][0] if eod else None, "eod_last": eod[-1][0] if eod else None, "eod_sessions": len(eod),
            "edgar_companies": sum(1 for _ in (raw / "edgar" / "companyfacts").glob("cik=*"))
            if (raw / "edgar" / "companyfacts").exists() else 0,
            "refs": refs, "staged_last": staged_last,
            "watermarks": json.loads(wm_path.read_text(encoding="utf-8")) if wm_path.exists() else {},
            "disk_gb": _dir_size(p.data_dir) / 1e9}


@st.cache_data(ttl=300, show_spinner=False)
def exceptions() -> pl.DataFrame:
    path = project().staged_dir / "exceptions"
    if not path.exists():
        return pl.DataFrame()
    return pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False).collect()


def _powershell(command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "EQRISK_ROOT": str(ROOT)}
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                          text=True, timeout=timeout, creationflags=_NO_WINDOW, env=env)


@st.cache_data(ttl=60, show_spinner=False)
def task_info() -> dict[str, Any] | None:
    """The Windows scheduled task, or None if it is not registered (or not on Windows)."""
    if os.name != "nt":
        return None
    cmd = (f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; if ($t) {{ "
           "$i = $t | Get-ScheduledTaskInfo; $f = 'yyyy-MM-dd HH:mm'; "
           "[pscustomobject]@{ State = [string]$t.State; "
           "Next = $(if ($i.NextRunTime) { $i.NextRunTime.ToString($f) } else { '' }); "
           "Last = $(if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) "
           "{ $i.LastRunTime.ToString($f) } else { 'never' }); "
           "Result = $i.LastTaskResult } | ConvertTo-Json -Compress }")
    try:
        out = _powershell(cmd).stdout.strip()
        return json.loads(out) if out else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def scheduled_job_running() -> bool:
    info = task_info()
    return bool(info and info.get("State") == "Running")


def register_task(at: str) -> subprocess.CompletedProcess[str]:
    """Register (or replace) the daily task from docs/RUNBOOK.md section 2."""
    cmd = ("$ErrorActionPreference = 'Stop'; $root = $env:EQRISK_ROOT; "
           "$exe = Join-Path $root '.venv\\Scripts\\eqrisk.exe'; "
           "$action = New-ScheduledTaskAction -Execute $exe -Argument 'run-daily' -WorkingDirectory $root; "
           f"$trigger = New-ScheduledTaskTrigger -Daily -At '{at}'; "
           "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
           f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger "
           "-Settings $settings -Force | Out-Null")
    return _powershell(cmd)


def remove_task() -> subprocess.CompletedProcess[str]:
    return _powershell("$ErrorActionPreference = 'Stop'; "
                       f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false")


def start_task() -> subprocess.CompletedProcess[str]:
    return _powershell(f"$ErrorActionPreference = 'Stop'; Start-ScheduledTask -TaskName '{TASK_NAME}'")


def fmt_time(ts: datetime | str | None) -> str:
    if ts is None:
        return "—"
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t.tzinfo else t.strftime("%Y-%m-%d %H:%M")
