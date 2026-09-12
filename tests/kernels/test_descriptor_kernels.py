"""Known-answer tests for eqrisk/kernels/descriptors.py (rule 3: tested before wiring)."""

import numpy as np
import pytest

from eqrisk.kernels import ewma_weights
from eqrisk.kernels.descriptors import (
    block_turnover,
    cmra,
    growth_rate,
    lag_rows,
    robust_zscore,
    rolling_ewma_moments,
    vif,
    weighted_corr,
)


def test_lag_rows():
    X = np.arange(10.0).reshape(5, 2)
    L = lag_rows(X, 2)
    assert np.isnan(L[:2]).all() and np.array_equal(L[2:], X[:3])
    assert np.array_equal(lag_rows(X, 0), X)


def test_rolling_ewma_moments_match_direct_computation_with_gaps():
    rng = np.random.default_rng(0)
    X = rng.normal(0, 0.02, (300, 4))
    X[rng.random(X.shape) < 0.1] = np.nan
    mean, std = rolling_ewma_moments(X, window=100, half_life=30, min_obs=50)
    t, n = 250, 2
    x = X[t - 99:t + 1, n]
    w = ewma_weights(100, 30)
    ok = np.isfinite(x)
    wn = w[ok] / w[ok].sum()
    m = (wn * x[ok]).sum()
    assert mean[t, n] == pytest.approx(m)
    assert std[t, n] == pytest.approx(np.sqrt((wn * (x[ok] - m) ** 2).sum()))
    assert np.isnan(mean[:99]).all()


def test_rolling_ewma_moments_min_obs_and_constant_series():
    X = np.full((60, 2), 0.01)
    X[:, 1] = np.nan
    X[-5:, 1] = 0.01
    mean, std = rolling_ewma_moments(X, window=20, half_life=5, min_obs=10)
    assert mean[-1, 0] == pytest.approx(0.01) and std[-1, 0] == pytest.approx(0.0)
    assert np.isnan(mean[-1, 1])                                  # only 5 of 20 values


def test_cmra_on_a_constant_drift():
    a = 0.001
    X = np.full((300, 1), a)
    got = cmra(X, months=12, month_len=21, min_obs=200, z_floor=-0.99)
    assert got[-1, 0] == pytest.approx(np.log1p(252 * a) - np.log1p(21 * a))
    assert np.isnan(got[250, 0]) and np.isfinite(got[251, 0])


def test_cmra_floor_keeps_it_finite_after_a_crash():
    X = np.full((252, 1), -0.02)
    assert np.isfinite(cmra(X, 12, 21, 200, -0.99)[-1, 0])


def test_block_turnover_constant_and_scaled_for_gaps():
    u = 0.004
    T = np.full((260, 2), u)
    T[-10:, 1] = np.nan                                           # 11 of 21 days in the last block
    stom = block_turnover(T, 21, 1, 0.5)
    stoa = block_turnover(T, 21, 12, 0.5)
    assert stom[-1, 0] == pytest.approx(np.log(21 * u)) and stoa[-1, 0] == pytest.approx(np.log(21 * u))
    assert stom[-1, 1] == pytest.approx(np.log(21 * u))            # scaled up from 11 valid days
    T[-15:, 1] = np.nan                                           # 6 of 21: block no longer counts
    assert np.isnan(block_turnover(T, 21, 1, 0.5)[-1, 1])
    assert np.isfinite(block_turnover(T, 21, 3, 0.5)[-1, 1])       # earlier blocks still count
    assert np.isnan(block_turnover(np.zeros((30, 1)), 21, 1, 0.5)[-1, 0])   # zero turnover


def test_growth_rate():
    assert growth_rate(np.array([1.0, 2, 3, 4, 5]), 3) == pytest.approx(1 / 3)
    assert growth_rate(np.array([-5.0, -4, -3, -2, -1]), 3) == pytest.approx(1 / 3)   # |mean|
    assert growth_rate(np.array([np.nan, np.nan, 3.0, 4.0, 5.0]), 3) == pytest.approx(1 / 4)
    assert np.isnan(growth_rate(np.array([np.nan, np.nan, np.nan, 4.0, 5.0]), 3))


def test_robust_zscore():
    x = np.array([1.0, 2, 3, 4, 100, np.nan])
    z = robust_zscore(x, np.ones(6, bool))
    assert z[2] == pytest.approx(0.0) and z[3] == pytest.approx(1 / 1.4826)
    assert np.isnan(z[5])


def test_weighted_corr_matches_pearson_with_equal_weights():
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=200), rng.normal(size=200)
    b = 0.6 * a + b
    assert weighted_corr(a, b, np.ones(200), np.ones(200, bool)) == pytest.approx(np.corrcoef(a, b)[0, 1])


def test_vif_orthogonal_and_collinear():
    rng = np.random.default_rng(2)
    n = 2000
    z1, z2 = rng.normal(size=n), rng.normal(size=n)
    X = np.column_stack([np.ones(n), z1, z2, z1 + 0.1 * rng.normal(size=n)])
    v = vif(X, np.ones(n), np.ones(n, bool), [1, 2])
    assert v[1] == pytest.approx(1.0, abs=0.01)                    # z2 is orthogonal to the rest
    assert v[0] > 50                                              # z1 is nearly the 4th column
