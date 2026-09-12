"""`eqrisk validate`: the §11.2 bias battery and the §1.3 scorecard, written to reports/ (Phase 9)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from eqrisk.calendar import get_calendar
from eqrisk.config import Project, ValidationCriteriaCfg
from eqrisk.manifest import read_manifests
from eqrisk.validation.backtest import ValidationResult, compute_validation, load_inputs
from eqrisk.validation.report import write_report

DAILY_COMMAND = "run-daily"


def operations(project: Project, c: ValidationCriteriaCfg) -> dict[str, Any]:
    """Operations criteria from the manifests `eqrisk run-daily` writes (one per session)."""
    runs = sorted((m for m in read_manifests(project.model_dir)
                   if m.command.startswith(DAILY_COMMAND) and m.finished_at is not None and m.as_of is not None),
                  key=lambda m: m.started_at)
    rerun_text = "checked by the Phase 10 rerun test, not by manifests"
    if not runs:
        none = "no run-daily manifests yet"
        return {"fast": None, "fast_text": none, "unattended": None, "streak_text": none, "rerun": None,
                "rerun_text": rerun_text}
    # The pipeline's own clock (counts.timings.total_s) covers the whole catch-up; manifests are
    # written per session at the end, so their started_at is not the run's start.
    minutes = [float(m.counts.get("timings", {}).get("total_s", (m.finished_at - m.started_at).total_seconds())) / 60
               for m in runs if m.finished_at is not None]
    cal = get_calendar(project.config.calendar)
    done = sorted({m.as_of for m in runs if m.status == "OK" and m.as_of is not None})
    best = streak = 0
    prev: date | None = None
    for d in done:
        streak = streak + 1 if prev is not None and cal.next_session(prev) == d else 1
        best, prev = max(best, streak), d
    # A streak shorter than required is not yet evidence either way, so it stays NOT RUN.
    return {"fast": max(minutes) < c.daily_run_minutes_max,
            "fast_text": f"slowest of {len(minutes)} runs {max(minutes):.1f} min",
            "unattended": True if best >= c.unattended_sessions_min else None,
            "streak_text": f"longest streak of consecutive sessions completed OK: {best} of "
                           f"{c.unattended_sessions_min}",
            "rerun": None, "rerun_text": rerun_text}


def run_validation(project: Project, start: date | None = None,
                   end: date | None = None) -> tuple[ValidationResult, Path]:
    """Score every forecast in [start, end] (default: all of them) and write the report."""
    cfg = project.config
    inp = load_inputs(project)
    avail = sorted(set(inp.F["final"]) & set(inp.specific["date"].unique().to_list()))
    if not avail:
        raise LookupError("no forecasts to validate; run `eqrisk backfill --stage model` first")
    s = max(start or avail[0], avail[0])
    e = min(end or avail[-1], avail[-1])
    res = compute_validation(inp, cfg, cfg.config_hash(), s, e, operations(project, cfg.validation.criteria))
    out = project.reports_dir / f"validation_{cfg.model_id}_{s}_{e}"
    write_report(res, out)
    return res, out
