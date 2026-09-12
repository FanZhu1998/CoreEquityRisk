"""Phase 10 acceptance (blueprint §17) on a sandbox copy of the development model tables.

The staged and raw data are read in place (offline, no re-staging); everything the pipeline writes
(model tables, manifests, LATEST_GOOD, catalog, logs) goes to a temporary directory.
"""

import dataclasses
import shutil
import time
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import Project, load_project
from eqrisk.pipeline.daily import processed_dates, read_latest_good, run_daily, set_latest_good
from eqrisk.pipeline.model_run import MODEL_TABLES
from eqrisk.store import parquet_bytes, upsert_dates

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]


@dataclasses.dataclass(frozen=True)
class SandboxProject(Project):
    sandbox: Path = Path()

    @property
    def catalog_path(self) -> Path:
        return self.sandbox / "catalog.duckdb"

    @property
    def logs_dir(self) -> Path:
        return self.sandbox / "logs"


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    real = load_project(ROOT)
    if not (real.model_dir / "specific_risk").exists():
        pytest.skip("no model tables; run `eqrisk backfill --stage model`")
    box = tmp_path_factory.mktemp("eqrisk")
    shutil.copytree(real.model_dir, box / "model" / real.config.model_id,
                    ignore=shutil.ignore_patterns("manifests", "run_manifest", "LATEST_GOOD.json"))
    cfg = real.config.model_copy(update={
        "outputs": real.config.outputs.model_copy(update={"root": box / "model"}),
        "pipeline": real.config.pipeline.model_copy(update={"notify": "log"})})
    fields = {f.name: getattr(real, f.name) for f in dataclasses.fields(real)}
    return SandboxProject(**{**fields, "config": cfg}, sandbox=box)


def _dates(project, name):
    path = project.model_dir / name
    return set(pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False).select("date").unique()
               .collect()["date"].to_list())


def _truncate(project, keep_through):
    """Remove every model row after `keep_through`: a gap for the pipeline to catch up."""
    for name, by_year in MODEL_TABLES.items():
        if (project.model_dir / name).exists():
            later = sorted(d for d in _dates(project, name) if d > keep_through)
            if later:
                upsert_dates(pl.DataFrame(), project.model_dir / name, later, by_year)


def _session_bytes(project, d):
    """Every stored byte of session d: its day files, and its rows of the single-file tables."""
    out = {}
    for name, by_year in MODEL_TABLES.items():
        if by_year:
            f = project.model_dir / name / f"year={d.year}" / f"day={d.isoformat()}.parquet"
            out[name] = f.read_bytes() if f.exists() else b""
        else:
            df = pl.read_parquet(project.model_dir / name / f"{name}.parquet")
            out[name] = parquet_bytes(df.filter(pl.col("date") == d))
    return out


def test_catch_up_rerun_and_quarantine(sandbox):
    cal = get_calendar(sandbox.config.calendar)
    last = max(processed_dates(sandbox))
    before_gap = cal.offset(last, -3)
    _truncate(sandbox, before_gap)
    set_latest_good(sandbox, before_gap, "backfill")

    # A three-session gap is caught up in one invocation.
    t0 = time.perf_counter()
    runs = run_daily(sandbox, last, offline=True, stage=False, send=False)
    elapsed = time.perf_counter() - t0
    assert [m.as_of for m in runs] == cal.sessions(cal.offset(last, -2), last)
    for m in runs:
        assert m.status == "OK", [g for g in m.gates["results"] if not g["ok"]]
    assert read_latest_good(sandbox)["as_of"] == last.isoformat()
    print(f"\ncatch-up of 3 sessions: {elapsed:.0f} s, steps {runs[0].counts['timings']}")

    # Rerunning a completed session reproduces it byte for byte.
    stored = _session_bytes(sandbox, last)
    again = run_daily(sandbox, last, offline=True, stage=False, force=True, send=False)
    assert [m.status for m in again] == ["OK"] and _session_bytes(sandbox, last) == stored
    one_day = again[0].counts["timings"]["total_s"]
    print(f"one session, model step only: {one_day:.0f} s")
    assert one_day < 600

    # A forced FAIL writes the session as QUARANTINED and leaves LATEST_GOOD alone.
    prev = cal.offset(last, -1)
    _truncate(sandbox, prev)
    set_latest_good(sandbox, prev, "earlier")
    strict = dataclasses.replace(sandbox, config=sandbox.config.model_copy(update={
        "gates": sandbox.config.gates.model_copy(update={"regression_n_min": 10**6})}))
    failed = run_daily(strict, last, offline=True, stage=False, send=False)
    assert [m.status for m in failed] == ["QUARANTINED"]
    assert [g["gate"] for g in failed[0].gates["results"] if not g["ok"] and g["level"] == "FAIL"] == ["regression"]
    assert read_latest_good(sandbox)["as_of"] == prev.isoformat()
