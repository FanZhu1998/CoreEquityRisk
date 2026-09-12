"""Five-layer specific risk on synthetic specific returns with known volatilities."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.model.exposures import STYLES
from eqrisk.model.panel import Panel
from eqrisk.model.specific_risk import run_specific_risk, size_deciles

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")
CAL = get_calendar("XNYS")
T, N, NEW = 430, 300, 10


def _setup(shock=None, seed=0):
    rng = np.random.default_rng(seed)
    dates = CAL.sessions(date(2018, 1, 2), date(2020, 12, 31))[:T]
    P = Panel(dates, np.arange(1, N + 1), ["A", "B", "C"])
    mcap = np.exp(rng.normal(23, 1.2, N))
    size = (np.log(mcap) - np.log(mcap).mean()) / np.log(mcap).std()
    true_daily = 0.015 * np.exp(-0.3 * size)                      # small names are more volatile
    U = rng.standard_normal((T, N)) * true_daily
    U[: T - 120, :NEW] = np.nan                                   # ten new listings, 120 days of history
    if shock is not None:
        U[-shock:] *= 3.0
    P["in_cov"] = np.ones((T, N), bool)
    P["in_estu"] = np.ones((T, N), bool)
    P["mcap"] = np.tile(mcap, (T, 1))
    P["capw"] = np.tile(mcap / mcap.sum(), (T, 1))
    styles = {s: rng.standard_normal(N) for s in STYLES}
    styles["SIZE"] = size
    ex = pl.DataFrame({"sid": P.sids, "industry": [["A", "B", "C"][i] for i in rng.integers(0, 3, N)], **styles})
    return P, U, {d: ex for d in dates}, true_daily


@pytest.fixture(scope="module")
def stationary():
    P, U, ex, true = _setup()
    return P, run_specific_risk(P, U, ex, CFG, P.dates[0]), true


def test_forecasts_recover_the_true_monthly_volatility(stationary):
    P, res, true = stationary
    last = res.final[-1]
    ratio = last[NEW:] / (true[NEW:] * np.sqrt(21))
    assert np.isfinite(res.final[-1]).all() and (res.final[-1] > 0).all()
    assert 0.9 < np.median(ratio) < 1.1


def test_new_listings_blend_toward_the_structural_model(stationary):
    _, res, _ = stationary
    last = res.table.filter(pl.col("date") == res.table["date"].max()).sort("sid")
    gamma = last["gamma"].to_numpy()
    assert (gamma[:NEW] < 1).all() and gamma[:NEW] == pytest.approx(0.5, abs=1e-12)   # (120 - 60) / 120
    assert np.median(gamma[NEW:]) > 0.99


def test_regime_multiplier_is_near_one_then_rises_after_a_shock(stationary):
    _, res, _ = stationary
    assert 0.85 < res.vra["lambda_S"][-100:].mean() < 1.15
    P, U, ex, _ = _setup(shock=40)
    shocked = run_specific_risk(P, U, ex, CFG, P.dates[0])
    assert shocked.vra["lambda_S"][-1] > 1.5


def test_size_deciles():
    d = size_deciles(np.arange(1.0, 101.0), np.ones(100, bool), 10)
    assert d.min() == 0 and d.max() == 9 and (np.bincount(d) == 10).all()
