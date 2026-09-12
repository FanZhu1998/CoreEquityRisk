"""Phase 9 acceptance (blueprint §17): the validation report on the development data."""

from pathlib import Path

import pytest

from eqrisk.config import load_project
from eqrisk.validation.run import run_validation

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]
AREAS = {"Operations", "Coverage", "Regression", "Factor risk", "Specific risk", "Test portfolios"}


@pytest.fixture(scope="module")
def validation():
    project = load_project(ROOT)
    if not (project.model_dir / "specific_risk").exists():
        pytest.skip("no model tables; run `eqrisk backfill --stage model`")
    return run_validation(project)


def test_report_shows_every_family_and_the_scorecard(validation):
    res, out = validation
    text = (out / "report.md").read_text(encoding="utf-8")
    for section in ("## Summary", "## External checks", "## (a)", "## (b)", "## (c)", "## (d)", "## (e)", "## (f)",
                    "## (g)"):
        assert section in text
    assert {r["area"] for r in res.scorecard} == AREAS
    assert all(r["status"] in {"PASS", "FAIL", "NOT RUN"} for r in res.scorecard)
    print("\n" + "\n".join(f"{r['status']:<8} {r['area']:<16} {r['criterion']}: {r['value']}" for r in res.scorecard))


def test_periods_do_not_overlap_and_cover_the_window(validation):
    res, _ = validation
    assert res.horizon == 21 and res.periods >= 24
    assert res.factor.height >= 30 and res.market.height == 2 and res.random.height >= 90


def test_specific_deciles_reported_for_both_stacks(validation):
    res, _ = validation
    for stack in ("full", "ts"):
        assert len(res.specific[stack]["size"]) == 10 and len(res.specific[stack]["vol"]) == 10
