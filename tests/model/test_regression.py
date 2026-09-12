"""The daily regression on a synthetic day with a known answer."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.model.exposures import STYLES
from eqrisk.model.panel import Panel
from eqrisk.model.regression import COUNTRY, ContractError, fit_day, run_regressions

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")
CAL = get_calendar("XNYS")
INDUSTRIES = ["A", "B", "C", "D", "EMPTY"]


def _day(seed=0, N=400):
    rng = np.random.default_rng(seed)
    dates = CAL.sessions(date(2024, 3, 1), date(2024, 3, 8))[:3]
    P = Panel(dates, np.arange(1, N + 1), INDUSTRIES)
    ind = rng.integers(0, 4, N)                            # nobody in "EMPTY"
    mcap = np.exp(rng.normal(23, 1.2, N))
    S = rng.standard_normal((N, len(STYLES)))
    X = np.column_stack([np.ones(N), np.eye(4)[ind], S])
    sample = np.arange(N) >= 10                            # names 1-10 are coverage-only below
    capw = np.where(sample, mcap, 0.0) / mcap[sample].sum()
    ind_capw = np.array([capw[ind == i].sum() for i in range(4)])   # the constraint uses sample weights
    f = rng.normal(0, 0.01, X.shape[1])
    f[1:5] -= ind_capw @ f[1:5]
    r = X @ f
    P["ret_excess"] = np.vstack([np.full(N, np.nan), r, r])
    P["price_flag"] = np.zeros((3, N), bool)
    P["in_estu"] = np.ones((3, N), bool)
    P["in_estu"][0, :10] = False                           # coverage-only on the exposure date
    P["v_reg"] = np.tile(np.sqrt(mcap) / np.sqrt(mcap).sum(), (3, 1))
    P["mcap"] = np.tile(mcap, (3, 1))
    ex = pl.DataFrame({"date": [dates[0]] * N, "sid": P.sids, "industry": [INDUSTRIES[i] for i in ind],
                       **{s: S[:, k] for k, s in enumerate(STYLES)}})
    return P, ex, f, r


def test_recovers_factor_returns_and_satisfies_the_constraint():
    P, ex, f, _ = _day()
    fit = fit_day(P, 1, ex, CFG)
    assert fit.factors[:5] == [COUNTRY, "A", "B", "C", "D"] and "EMPTY" not in fit.factors
    assert np.allclose(fit.f, f, atol=1e-10) and fit.constraint_resid < 1e-12
    assert np.allclose(fit.u, 0.0, atol=1e-12)


def test_misdated_exposures_break_the_contract():
    P, ex, _, _ = _day()
    with pytest.raises(ContractError):
        fit_day(P, 1, ex.with_columns(date=pl.lit(P.dates[1])), CFG)


def test_flagged_and_non_estu_names():
    P, ex, _, _ = _day()
    P["price_flag"][1, 20] = True
    fit = fit_day(P, 1, ex, CFG)
    assert 21 not in fit.sids and 21 not in fit.u_sids           # flagged: out of sample and of u
    assert 1 not in fit.sids and 1 in fit.u_sids                 # coverage-only: out-of-sample u
    assert not fit.u_in_estu[list(fit.u_sids).index(1)]


def test_run_writes_nulls_for_empty_industries_and_skips_thin_days():
    P, ex, f, _ = _day()
    res = run_regressions(P, ex, CFG, P.dates[1])
    fr = res.factor_returns.filter(pl.col("date") == P.dates[1])
    assert fr.filter(pl.col("factor") == "EMPTY")["f"].item() is None
    assert fr.filter(pl.col("factor") == "BETA")["f"].item() == pytest.approx(f[5])
    assert res.stats.filter(pl.col("date") == P.dates[2])["status"].item() == "no_exposures"
    thin = CFG.model_copy(update={"regression": CFG.regression.model_copy(update={"min_names": 10_000})})
    assert run_regressions(P, ex, thin, P.dates[1]).stats["status"].to_list()[0] == "too_few_names"
