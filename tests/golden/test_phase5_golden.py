"""Phase 5 acceptance (blueprint §17) on the development window."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.config import load_project
from eqrisk.model.exposures import STYLES
from eqrisk.model.regression import fit_day, french_correlations
from eqrisk.pipeline.model_run import load_panel
from eqrisk.staging.rawio import read_reference

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "model" / "us_lc_v1"
WINDOW = (date(2018, 1, 2), date(2021, 12, 31))


def _read(name):
    root = MODEL / name
    if not root.exists():
        pytest.skip(f"no model table {name}")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


@pytest.fixture(scope="module")
def stats():
    return _read("regression_stats").filter(pl.col("date").is_between(*WINDOW) & (pl.col("status") == "ok"))


@pytest.fixture(scope="module")
def fr():
    return _read("factor_returns").filter(pl.col("date").is_between(*WINDOW))


def test_every_session_regressed_with_constraint_held(stats):
    assert stats["constraint_resid"].max() < 1e-10
    assert stats["n"].min() >= 400 and stats["cond"].max() < 1e6


def test_country_tracks_the_cap_weighted_estu(stats, fr):
    c = fr.filter(pl.col("factor") == "COUNTRY").select("date", "f").join(stats, on="date")
    mkt = c["f"] - c["country_minus_mkt"]
    assert np.corrcoef(c["f"], mkt)[0, 1] >= 0.99


def test_mean_r2_is_plausible_for_large_caps(stats):
    assert 0.20 <= stats["r2_w"].mean() <= 0.60


def test_ken_french_signs(fr):
    corr = french_correlations(fr, read_reference(ROOT / "data" / "raw", "famafrench", "daily"))
    assert corr["COUNTRY~Mkt-RF"] >= 0.95
    assert corr["SIZE~SMB"] < 0 and corr["BOOK_TO_PRICE~HML"] > 0 and corr["MOMENTUM~Mom"] > 0


def test_pure_style_portfolios_on_real_days():
    project = load_project(ROOT)
    P, _ = load_panel(project, date(2021, 12, 31))
    x = _read("exposures")
    for day in (date(2019, 6, 3), date(2020, 3, 16), date(2021, 11, 1)):
        t = P.row[day]
        fit = fit_day(P, t, x.filter(pl.col("date") == P.dates[t - 1]), project.config)
        E = fit.omega @ fit.X
        sty = [fit.factors.index(s) for s in STYLES]
        assert np.allclose(E[np.ix_(sty, sty)], np.eye(len(sty)), atol=1e-10)
        assert np.allclose(E[sty, 0], 0.0, atol=1e-10)          # dollar neutral
