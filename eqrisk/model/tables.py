"""Staged store -> model inputs: the (T, N) panel and the canonical factor order.

These live in the model layer, not the pipeline, so anything that re-estimates the model
(the daily run, `eqrisk validate`, a notebook) loads its inputs the same way without
importing orchestration code.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from eqrisk.calendar import get_calendar
from eqrisk.config import Project
from eqrisk.model.exposures import STYLES
from eqrisk.model.panel import Panel, build_panel
from eqrisk.model.regression import COUNTRY
from eqrisk.store import read_table

PANEL_TABLES = ("prices", "mcap", "universe", "rf", "security_master", "fundamentals_pit")


def load_panel(project: Project, end: date | None = None) -> tuple[Panel, dict[str, pl.DataFrame]]:
    """The model panel through `end` (default: the last staged session), plus the staged tables."""
    s = project.staged_dir
    t = {n: read_table(s, n) for n in PANEL_TABLES}
    last = t["universe"]["date"].max()
    assert isinstance(last, date)
    end = min(end, last) if end else last
    sessions = get_calendar(project.config.calendar).sessions(project.config.history.price_start, end)
    return build_panel(t["prices"], t["mcap"], t["universe"], t["rf"], sessions), t


def factor_order(fr: pl.DataFrame) -> list[str]:
    """Country, then industries alphabetically, then the styles in their declared order."""
    inds = sorted(set(fr["factor"].unique().to_list()) - {COUNTRY} - set(STYLES))
    return [COUNTRY, *inds, *STYLES]
