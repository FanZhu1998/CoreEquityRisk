"""EDGAR's incremental pull and its watermark (DECISIONS D-026): a day without a daily index is asked
for again until a later day's index shows it was a weekend or holiday; it is never skipped for good."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from eqrisk.pipeline import ingest
from eqrisk.sources.base import IngestReport
from eqrisk.sources.edgar import parse_daily_index

APPLE = 320193
FRI, SAT, SUN, MON, TUE = (date(2026, 9, d) for d in (11, 12, 13, 14, 15))


class FakeEdgar:
    """Daily indexes for the given days only (Apple files a 10-Q on each); records every day asked for."""

    def __init__(self, published: set[date]) -> None:
        self.published = published
        self.asked: list[date] = []

    def daily_index(self, day: date) -> pl.DataFrame:
        self.asked.append(day)
        if day not in self.published:
            return parse_daily_index("")
        return pl.DataFrame({"cik": [APPLE], "company": ["APPLE INC"], "form": ["10-Q"], "filed": [day],
                             "filename": [f"edgar/data/{APPLE}/0000320193-26-000001.txt"]})


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A project whose EDGAR watermark is Friday 2026-09-11, the state before the failed run."""
    proj = SimpleNamespace(raw_dir=tmp_path / "raw", pulled=[],
                           sources=SimpleNamespace(edgar=SimpleNamespace(forms=["10-K", "10-Q"])))
    (proj.raw_dir / "edgar" / "companyfacts" / f"cik={APPLE}").mkdir(parents=True)
    (proj.raw_dir / "_state").mkdir(parents=True)
    ingest.save_watermarks(proj, {"edgar_daily_index": FRI.isoformat()})

    def pull(project: SimpleNamespace, edgar: FakeEdgar, ciks: set[int], refresh: bool) -> IngestReport:
        project.pulled.append(set(ciks))
        return IngestReport("edgar", "companyfacts")

    monkeypatch.setattr(ingest, "pull_edgar", pull)
    return proj


def test_a_weekend_and_an_unpublished_day_leave_the_watermark_alone(project: SimpleNamespace) -> None:
    edgar = FakeEdgar(set())                               # Monday evening, before SEC publishes Monday
    ingest.edgar_incremental(project, MON, edgar=edgar)    # type: ignore[arg-type]
    assert edgar.asked == [SAT, SUN, MON]
    assert ingest.load_watermarks(project)["edgar_daily_index"] == FRI.isoformat()
    assert project.pulled == [set()]


def test_the_next_run_asks_again_and_moves_to_the_last_published_day(project: SimpleNamespace) -> None:
    ingest.edgar_incremental(project, MON, edgar=FakeEdgar(set()))            # type: ignore[arg-type]
    edgar = FakeEdgar({MON})                                                 # Monday is out, Tuesday not yet
    ingest.edgar_incremental(project, TUE, edgar=edgar)                      # type: ignore[arg-type]
    assert edgar.asked == [SAT, SUN, MON, TUE]
    assert ingest.load_watermarks(project)["edgar_daily_index"] == MON.isoformat()
    assert project.pulled[-1] == {APPLE}                                     # Monday's 10-Q is picked up
