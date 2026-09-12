"""`eqrisk run-daily`: the daily DAG with catch-up (blueprint §13.2), gates (§11.4) and LATEST_GOOD.

Catch-up: every session after the last one processed (by the backfill or an earlier daily run),
through `through`, is ingested; staging then rebuilds once (D-010); the model step writes those
sessions. The causal states (the regression's f/sigma EWMA, both VRA multipliers, the specific
risk layers) are recomputed from the model start on every run and only the new sessions are
written, so rerunning a session reproduces it bit for bit. Each session then gets its gates and a
manifest, and LATEST_GOOD moves only when every FAIL gate passes. A quarantined session counts as
processed: fix the cause, then `eqrisk run-daily --date D --force` (docs/RUNBOOK.md).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from eqrisk.calendar import get_calendar
from eqrisk.config import Project
from eqrisk.log import get_logger
from eqrisk.manifest import RunManifest, finish, new_manifest, write_manifest
from eqrisk.model.exposures import FUNDAMENTAL_STYLES
from eqrisk.pipeline.gates import OK, gate_results, load_day, status_of
from eqrisk.pipeline.model_run import MODEL_TABLES, run_model_dates
from eqrisk.pipeline.notify import notify
from eqrisk.store import atomic_write_bytes, compact_month, refresh_catalog

log = get_logger(__name__)
LATEST_GOOD = "LATEST_GOOD.json"
_ET = "America/New_York"
_TOP_MOVES = 3


def now_et() -> datetime:
    import pandas as pd

    now: datetime = pd.Timestamp.now(tz=_ET).to_pydatetime()
    return now


def target_session(project: Project, now: datetime) -> date:
    """The latest session the vendor should have: today once `vendor_ready_after_et` has passed
    (Eastern time), otherwise the previous session."""
    cal = get_calendar(project.config.calendar)
    hh, mm = (int(x) for x in project.config.pipeline.vendor_ready_after_et.split(":"))
    today = now.date()
    if cal.is_session(today) and (now.hour, now.minute) >= (hh, mm):
        return today
    return cal.prev_session(today)


def processed_dates(project: Project) -> set[date]:
    """Sessions with a factor covariance and specific risk, from the backfill or a daily run."""
    out: set[date] | None = None
    for name in ("factor_risk_diag", "specific_risk"):
        path = project.model_dir / name
        if not path.exists():
            return set()
        days = set(pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False)
                   .select("date").unique().collect()["date"].to_list())
        out = days if out is None else out & days
    return out or set()


def read_latest_good(project: Project) -> dict[str, Any] | None:
    path = project.model_dir / LATEST_GOOD
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def set_latest_good(project: Project, d: date, run_id: str) -> None:
    body = {"model_id": project.config.model_id, "as_of": d.isoformat(), "run_id": run_id}
    atomic_write_bytes(json.dumps(body, indent=1).encode(), project.model_dir / LATEST_GOOD)


def vendor_ready(project: Project, d: date) -> bool:
    from eqrisk.sources.eodhd_px import EodhdPrices

    px = EodhdPrices(project.sources, project.settings.eodhd_api_key, project.config.pipeline.vendor_probe_codes)
    return px.fetch(d, d).filter(pl.col("date") == d).height > 0


def wait_for_vendor(project: Project, d: date) -> bool:
    """Poll until the vendor has session `d` or `vendor_wait_minutes` pass (§13.2 step 2)."""
    p = project.config.pipeline
    deadline = time.monotonic() + 60 * p.vendor_wait_minutes
    while True:
        if vendor_ready(project, d):
            return True
        if time.monotonic() + 60 * p.vendor_poll_minutes > deadline:
            return False
        log.info("waiting for vendor", session=str(d), poll_minutes=p.vendor_poll_minutes)
        time.sleep(60 * p.vendor_poll_minutes)


def pending_sessions(project: Project, through: date, force: bool) -> list[date]:
    cal = get_calendar(project.config.calendar)
    if force:
        return [through]
    done = [d for d in processed_dates(project) if d <= through]
    if not done:
        raise LookupError("no processed sessions yet; run `eqrisk backfill` first (§13.4)")
    last = max(done)
    return cal.sessions(cal.next_session(last), through) if last < through else []


def _manifest(project: Project, d: date, command: str, status: str, results: list[dict[str, Any]],
              counts: dict[str, Any], started: datetime) -> RunManifest:
    """One manifest per session, stamped with the start of the whole run (catch-up included)."""
    from eqrisk.pipeline.ingest import load_watermarks

    cfg = project.config
    m = new_manifest(root=project.root, command=command, model_id=cfg.model_id, config_hash=cfg.config_hash(),
                     industry_scheme_version=cfg.industries.scheme_version, as_of=d)
    m = m.model_copy(update={"started_at": started, "gates": {"results": results}, "counts": counts,
                             "watermarks": load_watermarks(project)})
    out = finish(m, status)  # type: ignore[arg-type]
    write_manifest(out, project.model_dir)
    return out


def summary(project: Project, runs: list[RunManifest]) -> tuple[str, str]:
    """Notification title and one-paragraph body (§13.2 step 8)."""
    last = runs[-1]
    states = ", ".join(f"{m.as_of} {m.status}" for m in runs)
    day = load_day(project, last.as_of) if last.as_of is not None and last.status != "SKIPPED" else None
    parts = [states]
    if day is not None:
        lam = " ".join(f"{k} {v:.2f}" for k, v in (("lambda_F", day.lambda_f), ("lambda_S", day.lambda_s))
                       if v is not None)
        parts.append(lam)
        if day.factors.height:
            top = day.factors.drop_nulls("f_over_sigma").sort(pl.col("f_over_sigma").abs(), descending=True)
            parts.append("largest moves " + ", ".join(f"{r['factor']} {r['f_over_sigma']:+.1f} sd"
                                                      for r in top.head(_TOP_MOVES).iter_rows(named=True)))
    warns = sorted({r["gate"] for m in runs for r in m.gates.get("results", []) if not r["ok"]})
    parts.append("gate warnings: " + (", ".join(warns) if warns else "none"))
    bad = [m for m in runs if m.status != OK]
    title = f"EQRisk {project.config.model_id}: " + ("all sessions OK" if not bad else f"{len(bad)} not OK")
    return title, ". ".join(p for p in parts if p)


def run_daily(project: Project, through: date | None = None, *, offline: bool = False, stage: bool = True,
              force: bool = False, send: bool = True) -> list[RunManifest]:
    """Process every pending session through `through` (default: the latest the vendor should have)."""
    from eqrisk.pipeline.ingest import ingest_date
    from eqrisk.pipeline.stage import run_staging

    cfg = project.config
    cal = get_calendar(cfg.calendar)
    through = through or target_session(project, now_et())
    if not cal.is_session(through):
        log.info("not a session", date=str(through))
        return []
    pending = pending_sessions(project, through, force)
    if not pending:
        log.info("up to date", through=str(through))
        return []
    if len(pending) > cfg.pipeline.max_catchup_sessions:
        raise RuntimeError(f"{len(pending)} sessions behind; use `eqrisk backfill --stage all` for gaps this long")
    command = "run-daily" + (" --force" if force else "")
    clock: dict[str, float] = {}
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    try:
        if not offline:
            if not wait_for_vendor(project, pending[-1]):
                m = _manifest(project, pending[-1], command, "SKIPPED", [], {"reason": "vendor not ready"}, started)
                if send:
                    notify(project, f"EQRisk {cfg.model_id}: waiting for data", f"{pending[-1]} not yet published")
                return [m]
            for d in pending:
                ingest_date(project, d)
            clock["ingest_s"] = time.perf_counter() - t0
        if stage:
            t1 = time.perf_counter()
            run_staging(project, through)
            clock["stage_s"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        clock.update(run_model_dates(project, pending))
        clock["model_s"] = time.perf_counter() - t1
    except Exception as exc:
        _manifest(project, pending[-1], command, "FAILED", [], {"error": f"{type(exc).__name__}: {exc}"}, started)
        if send:
            notify(project, f"EQRisk {cfg.model_id}: run FAILED", f"{pending[0]}..{pending[-1]}: {exc}")
        raise
    clock["total_s"] = time.perf_counter() - t0
    runs: list[RunManifest] = []
    for d in pending:
        results = gate_results(load_day(project, d), cfg.gates, set(FUNDAMENTAL_STYLES))
        status = status_of(results)
        m = _manifest(project, d, f"{command} --date {d}", status, results,
                      {"sessions": [str(x) for x in pending], "timings": {k: round(v, 2) for k, v in clock.items()}},
                      started)
        if status == OK:
            set_latest_good(project, d, m.run_id)
        runs.append(m)
    if send:
        notify(project, *summary(project, runs))
    return runs


def compact_tables(project: Project, month: str) -> int:
    """`eqrisk compact --month YYYY-MM`: merge the month's daily files in every year-partitioned
    model table. Returns the number of day files merged."""
    datetime.strptime(month, "%Y-%m")
    n = sum(compact_month(project.model_dir / name, month) for name, by_year in MODEL_TABLES.items() if by_year)
    refresh_catalog(project.catalog_path, {name: project.model_dir / name for name in MODEL_TABLES})
    return n
