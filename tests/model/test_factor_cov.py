"""Four-layer factor covariance on synthetic factor returns with a known covariance and a regime shift."""

import hashlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.model.factor_cov import date_seed, factor_vra, one_day_sigma, run_factor_cov
from eqrisk.validation.bias import eigen_bias_battery, factor_bias

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")
FAST = CFG.model_copy(update={"factor_cov": CFG.factor_cov.model_copy(update={
    "eigen": CFG.factor_cov.eigen.model_copy(update={"n_sims": 60}), "min_history_days": 300})})
CAL = get_calendar("XNYS")


def _returns(T=900, K=6, seed=0, shift_at=None, factor=3.0):
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((K, K))
    S = (A @ A.T + K * np.eye(K)) * 1e-5
    Phi = rng.multivariate_normal(np.zeros(K), S, size=T)
    if shift_at is not None:
        Phi[shift_at:] *= factor
    dates = CAL.sessions(date(2018, 1, 2), date(2023, 12, 29))[:T]
    return dates, Phi, np.ones_like(Phi, dtype=bool), S


def test_date_seed_is_sha256_not_python_hash():
    expect = int.from_bytes(hashlib.sha256(b"m|2020-03-16").digest()[:8], "big")
    assert date_seed("m", date(2020, 3, 16)) == expect


def test_one_day_sigma_uses_only_the_past():
    Phi = np.array([[0.01], [-0.01], [0.01], [0.05]])
    s = one_day_sigma(Phi, np.ones_like(Phi, bool), half_life=1e9, min_obs=1)
    assert np.isnan(s[0, 0]) and s[3, 0] == pytest.approx(0.01)     # the 5% move is not in its own forecast


def test_vra_rises_in_a_volatility_regime_shift():
    dates, Phi, obs, _ = _returns(T=900, shift_at=600, factor=3.0)
    sig = one_day_sigma(Phi, obs, 84, 63)
    _, lam = factor_vra(Phi, obs, sig, 42, 10.0)
    assert 0.8 < np.median(lam[300:590]) < 1.2
    assert lam[600:700].max() > 1.5                                    # the forecast lagged the new regime
    assert lam[850:] .mean() < lam[600:700].max()                      # and relaxes once the EWMA catches up


@pytest.fixture(scope="module")
def run():
    dates, Phi, obs, S = _returns()
    names = [f"F{k}" for k in range(Phi.shape[1])]
    return dates, Phi, obs, S, names, run_factor_cov(dates, Phi, obs, names, FAST, "m", "weekly")


def test_every_forecast_symmetric_psd_and_near_the_truth(run):
    dates, _, _, S, _, res = run
    for F in res.final.values():
        assert np.allclose(F, F.T) and np.linalg.eigvalsh(F).min() > 0
    F_last = res.final[dates[-1]]
    vol_ratio = np.sqrt(np.diag(F_last) / (21 * np.diag(S)))
    assert np.all((vol_ratio > 0.6) & (vol_ratio < 1.6))


def test_reruns_are_bit_identical(run):
    dates, Phi, obs, _, names, res = run
    again = run_factor_cov(dates, Phi, obs, names, FAST, "m", "weekly")
    assert again.final.keys() == res.final.keys()
    assert all(np.array_equal(again.final[d], res.final[d]) for d in res.final)


def test_daily_mode_forecast_does_not_depend_on_other_dates(run):
    # What the incremental daily pipeline relies on: one date's forecast is a function of its own
    # window and seed only.
    dates, Phi, obs, _, names, _ = run
    one = run_factor_cov(dates, Phi, obs, names, FAST, "m", "daily", only={dates[-1]})
    two = run_factor_cov(dates, Phi, obs, names, FAST, "m", "daily", only={dates[-1], dates[-2]})
    assert np.array_equal(one.final[dates[-1]], two.final[dates[-1]])


def test_bias_batteries_run_and_eigen_adjustment_lifts_small_eigenfactors(run):
    dates, Phi, obs, _, names, res = run
    fb = factor_bias(dates, Phi, obs, res.final, names, 21, dates[300], dates[-30])
    assert fb.height == len(names) and fb["bias"].is_between(0.5, 2.0).all()
    eb = eigen_bias_battery(dates, Phi, res.pre, res.post_eigen, 21, dates[300], dates[-30])
    assert eb["bias_after"][0] < eb["bias_before"][0] + 1e-12       # the adjustment raises the smallest's forecast
