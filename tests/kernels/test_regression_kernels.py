"""Known-answer tests for eqrisk/kernels/regression.py."""

import numpy as np
import pytest
import statsmodels.api as sm

from eqrisk.kernels import constrained_wls
from eqrisk.kernels.regression import mad_clip, restriction_matrix, wls_diagnostics


def _problem(seed=7, N=400, n_ind=6, n_sty=4):
    rng = np.random.default_rng(seed)
    ind = rng.integers(0, n_ind, N)
    ind[:n_ind] = np.arange(n_ind)
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], rng.standard_normal((N, n_sty))])
    mcap = np.exp(rng.normal(10, 1.2, N))
    capw = mcap / mcap.sum()
    ind_cols = np.arange(1, 1 + n_ind)
    ind_capw = np.array([capw[ind == i].sum() for i in range(n_ind)])
    f_true = rng.normal(0, 0.01, X.shape[1])
    f_true[ind_cols] -= ind_capw @ f_true[ind_cols]
    r = X @ f_true + rng.normal(0, 0.02, N) / (mcap / mcap.mean()) ** 0.25
    return X, np.sqrt(mcap), ind_cols, ind_capw, r


def test_restriction_matrix_matches_constrained_wls():
    X, v, ind_cols, ind_capw, r = _problem()
    f, _, _ = constrained_wls(r, X, v, ind_cols, ind_capw)
    R = restriction_matrix(X.shape[1], ind_cols, ind_capw)
    g = np.linalg.lstsq(R, f, rcond=None)[0]
    assert np.allclose(R @ g, f, atol=1e-12)
    assert abs(ind_capw @ (R @ np.random.default_rng(1).normal(size=R.shape[1]))[ind_cols]) < 1e-12


def test_mad_clip():
    r = np.array([0.0, 0.01, -0.01, 0.02, -0.02, 5.0])
    c = mad_clip(r, np.ones(6, bool), 8.0)
    med = np.median(r)
    scale = np.median(np.abs(r - med)) * 1.4826
    assert c[-1] == pytest.approx(med + 8 * scale) and np.array_equal(c[:5], r[:5])


def test_covariance_matches_statsmodels_wls():
    X, v, ind_cols, ind_capw, r = _problem()
    f, u, _ = constrained_wls(r, X, v, ind_cols, ind_capw)
    R = restriction_matrix(X.shape[1], ind_cols, ind_capw)
    r2, cond, cov_f = wls_diagnostics(r, X, v, f, R)
    fit = sm.WLS(r, X @ R, weights=v).fit()
    assert np.allclose(R @ fit.params, f, atol=1e-10)
    assert np.allclose(R @ fit.cov_params() @ R.T, cov_f, rtol=1e-8, atol=1e-14)
    V = v / v.sum()
    rbar = (V * r).sum()
    assert r2 == pytest.approx(1 - (V * u ** 2).sum() / (V * (r - rbar) ** 2).sum())
    assert 1 < cond < 1e6
