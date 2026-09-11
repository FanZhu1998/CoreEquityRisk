"""Known-answer tests for reference_kernels.py. Run: pytest -q test_reference_kernels.py"""
import time

import numpy as np
import pytest

from eqrisk.kernels import (
    bayes_shrink, bias_statistic, combine_vol_corr, constrained_wls, coverage_gamma,
    effective_obs, eigen_adjust, eigen_bias, ewma_lag_cov, ewma_weights, factor_cov_nw,
    industry_neff, mrad, newey_west_cov, orthogonalize, qlike, risk_decomposition,
    rolling_ewma_beta, specific_ts_var, standardize, vra_lambda,
)


def _cross_section(seed: int = 7, N: int = 500, n_ind: int = 20, n_sty: int = 12):
    rng = np.random.default_rng(seed)
    ind = rng.integers(0, n_ind, N)
    ind[:n_ind] = np.arange(n_ind)                        # every industry non-empty
    mcap = np.exp(rng.normal(10, 1.2, N))
    estu = np.ones(N, bool)
    S = np.column_stack([standardize(rng.standard_normal(N), mcap, estu) for _ in range(n_sty)])
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], S])
    ind_cols = np.arange(1, 1 + n_ind)
    capw = mcap / mcap.sum()
    ind_capw = np.array([capw[ind == i].sum() for i in range(n_ind)])
    return rng, X, mcap, capw, ind_cols, ind_capw


# ---- EWMA / NW ------------------------------------------------------------ #

def test_ewma_weights_normalized_and_increasing():
    w = ewma_weights(500, 84)
    assert abs(w.sum() - 1) < 1e-12 and np.all(np.diff(w) > 0)


def test_effective_obs_hl84():
    assert abs(effective_obs(5000, 84) - 242.4) < 0.5


def test_ewma_recovers_known_sigma():
    rng = np.random.default_rng(1)
    A = rng.standard_normal((4, 4))
    S = A @ A.T + 4 * np.eye(4)
    R = rng.multivariate_normal(np.zeros(4), S, size=40000)
    E = ewma_lag_cov(R, 1e12)
    assert np.allclose(E, R.T @ R / len(R), atol=1e-8)        # huge HL == uniform
    assert np.linalg.norm(E - S) / np.linalg.norm(S) < 0.05


def test_combine_vol_corr_exact():
    rng = np.random.default_rng(2)
    A = rng.standard_normal((4, 4))
    S = A @ A.T + 4 * np.eye(4)
    Vv, Vc = 1.7 * S, S + np.diag(np.arange(1.0, 5.0))
    F = combine_vol_corr(Vv, Vc)
    corr = lambda M: M / np.sqrt(np.outer(np.diag(M), np.diag(M)))
    assert np.allclose(np.diag(F), np.diag(Vv))
    assert np.allclose(corr(F), corr(Vc))


def test_newey_west_lifts_ar1_toward_long_run_variance():
    rng = np.random.default_rng(3)
    T, rho = 60000, 0.5
    e = rng.standard_normal(T)
    x = np.empty(T)
    x[0] = e[0]
    for t in range(1, T):
        x[t] = rho * x[t - 1] + e[t]
    v0 = ewma_lag_cov(x[:, None], 1e9)[0, 0]                 # ~ 1/(1-rho^2) = 1.333
    vnw = newey_west_cov(x[:, None], 1e9, 20)[0, 0]          # long-run 1/(1-rho)^2 = 4
    assert vnw > v0 and abs(vnw - 4.0) / 4.0 < 0.10


# ---- Eigenfactor adjustment ---------------------------------------------- #

def test_eigen_smile_and_psd():
    rng = np.random.default_rng(4)
    B = rng.standard_normal((10, 10))
    F0 = B @ B.T / 10 + 0.05 * np.eye(10)
    v = eigen_bias(F0, T_sim=30, n_sims=400, rng=np.random.default_rng(1))
    Fe, gamma = eigen_adjust(F0, v, a=1.0)
    assert v[0] > 1 > v[-1]                                  # smallest eigenfactors most underforecast
    assert np.linalg.eigvalsh(Fe).min() > -1e-10
    assert np.allclose(gamma, v)                             # a = 1 -> gamma = v


def test_eigen_bias_deterministic_with_seed():
    rng = np.random.default_rng(5)
    B = rng.standard_normal((6, 6))
    F0 = B @ B.T + np.eye(6)
    v1 = eigen_bias(F0, 50, 200, np.random.default_rng(42))
    v2 = eigen_bias(F0, 50, 200, np.random.default_rng(42))
    assert np.array_equal(v1, v2)


@pytest.mark.slow
def test_eigen_timing_k33():
    rng = np.random.default_rng(6)
    B = rng.standard_normal((33, 600)) * 0.01
    Fd = B @ B.T / 600
    t0 = time.time()
    v_eq = eigen_bias(Fd, T_sim=242, n_sims=1000, rng=np.random.default_rng(3))
    t1 = time.time()
    est = lambda R: factor_cov_nw(R, 84, 5, 504, 2)
    v_same = eigen_bias(Fd, T_sim=1000, n_sims=100, rng=np.random.default_rng(3), estimator=est)
    t2 = time.time()
    print(f"\nequal_weight_neff M=1000: {t1 - t0:.2f}s  same-estimator M=100: {t2 - t1:.2f}s")
    assert v_eq[0] > 1 > v_eq[-1] and v_same[0] > 1
    assert t1 - t0 < 30 and t2 - t1 < 30


# ---- Volatility regime adjustment ---------------------------------------- #

def test_vra_fixed_points():
    assert np.isclose(vra_lambda(np.ones(500), 42)[-1], 1.0, atol=1e-6)
    assert abs(vra_lambda(np.full(2000, 4.0), 42)[-1] - 2.0) < 1e-3


# ---- Cross-sectional regression ------------------------------------------ #

def test_standardize_moments():
    rng, X, mcap, *_ = _cross_section()
    z = standardize(rng.lognormal(size=500), mcap, np.ones(500, bool))
    assert abs(np.average(z, weights=mcap)) < 1e-12 and abs(z.std() - 1) < 1e-12


def test_orthogonalize_residual_uncorrelated():
    rng = np.random.default_rng(8)
    z = rng.standard_normal(400)
    y = 0.8 * z + rng.standard_normal(400)
    w = rng.uniform(0.5, 2.0, 400)
    res = orthogonalize(y, z, w, np.ones(400, bool))
    assert abs(np.sum(w * res * z)) < 1e-8 and abs(np.sum(w * res)) < 1e-8


def test_csr_recovers_factor_returns_and_constraint():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    f_true = rng.normal(0, 0.01, X.shape[1])
    f_true[ind_cols] -= ind_capw @ f_true[ind_cols]          # enforce sum w_i f_i = 0
    f_hat, u, Om = constrained_wls(X @ f_true, X, np.sqrt(mcap), ind_cols, ind_capw)
    assert np.allclose(f_hat, f_true, atol=1e-10)
    assert abs(ind_capw @ f_hat[ind_cols]) < 1e-12
    assert np.allclose(u, 0, atol=1e-12)


def test_pure_style_portfolios_unit_exposure_and_dollar_neutral():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    _, _, Om = constrained_wls(rng.standard_normal(500), X, np.sqrt(mcap), ind_cols, ind_capw)
    sty = np.arange(1 + len(ind_cols), X.shape[1])
    E = Om[sty] @ X                                           # exposures of style portfolios
    assert np.allclose(E[:, sty], np.eye(len(sty)), atol=1e-10)
    assert np.allclose(E[:, 0], 0, atol=1e-10)                # dollar-neutral (country exp = 0)


def test_country_factor_tracks_cap_weighted_market():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    fc, mk = [], []
    for _ in range(250):
        f_t = rng.normal(0, 0.01, X.shape[1])
        f_t[ind_cols] -= ind_capw @ f_t[ind_cols]
        r = X @ f_t + rng.normal(0, 0.02, 500) / np.sqrt(mcap / mcap.mean()) ** 0.25
        fh, _, _ = constrained_wls(r, X, np.sqrt(mcap), ind_cols, ind_capw)
        fc.append(fh[0])
        mk.append(capw @ r)
    assert np.corrcoef(fc, mk)[0, 1] > 0.99


def test_industry_neff():
    assert np.isclose(industry_neff(np.ones(12)), 12.0)
    assert industry_neff(np.array([100.0, 1, 1, 1])) < 1.1


# ---- Descriptors ---------------------------------------------------------- #

def test_rolling_beta_matches_direct_wls():
    rng = np.random.default_rng(0)
    T, N, W = 600, 50, 252
    m = rng.normal(0, 0.01, T)
    true_b = rng.uniform(0.5, 1.5, N)
    r = m[:, None] * true_b + rng.normal(0, 0.015, (T, N))
    r[rng.random((T, N)) < 0.02] = np.nan
    b, hs = rolling_ewma_beta(r, m)
    t, n = 450, 7
    w = ewma_weights(W, 63)
    y, x = r[t - W + 1:t + 1, n], m[t - W + 1:t + 1]
    ok = np.isfinite(y)
    A = np.column_stack([np.ones(ok.sum()), x[ok]])
    sw = np.sqrt(w[ok])
    coef, *_ = np.linalg.lstsq(A * sw[:, None], y[ok] * sw, rcond=None)
    assert np.isclose(b[t, n], coef[1])
    assert np.all(np.isnan(b[:W - 1]))
    assert np.nanmean(np.abs(b[-1] - true_b)) < 0.15


# ---- Specific risk -------------------------------------------------------- #

def test_specific_ts_var_iid():
    u = np.random.default_rng(9).normal(0, 0.015, 400)
    assert abs(np.sqrt(specific_ts_var(u, 84, 5, 252)) / 0.015 - 1) < 0.15


def test_specific_ts_var_nw_floor_on_bid_ask_bounce():
    e = np.random.default_rng(10).normal(0, 0.01, 2001)
    u = e[1:] - e[:-1]                                        # MA(1), rho_1 = -0.5: NW ratio ~ 1/6
    raw = newey_west_cov(u[:, None], 252, 5)[0, 0] / ewma_lag_cov(u[:, None], 252)[0, 0]
    v0 = ewma_lag_cov(u[:, None], 84)[0, 0]
    assert raw < 0.25                                         # unbounded NW would collapse the estimate
    assert np.isclose(specific_ts_var(u, 84, 5, 252), 0.25 * v0)


def test_coverage_gamma():
    rng = np.random.default_rng(11)
    assert coverage_gamma(rng.normal(0, 1, 252)) > 0.95
    assert coverage_gamma(rng.normal(0, 1, 50)) == 0.0
    x = rng.normal(0, 1, 252)
    x[:5] = 20.0                                              # a few huge outliers: sd ~ 3x robust sd
    assert coverage_gamma(x) < 0.5


def test_bayes_shrink_bounds_and_monotone_in_q():
    rng = np.random.default_rng(12)
    sig = np.abs(rng.normal(0.08, 0.03, 500))
    capw = np.exp(rng.normal(10, 1.2, 500))
    dec = np.repeat(np.arange(10), 50)
    mus = np.array([np.average(sig[dec == g], weights=capw[dec == g]) for g in dec])
    s1 = bayes_shrink(sig, capw, dec, q=0.1)
    s2 = bayes_shrink(sig, capw, dec, q=1.0)
    assert np.all(s1 >= np.minimum(sig, mus) - 1e-12) and np.all(s1 <= np.maximum(sig, mus) + 1e-12)
    assert np.all(np.abs(s2 - mus) <= np.abs(s1 - mus) + 1e-12)


# ---- Analytics ------------------------------------------------------------ #

def test_risk_decomposition_adds_up():
    rng, X, mcap, capw, *_ = _cross_section()
    K = X.shape[1]
    B = rng.standard_normal((K, K)) * 0.01
    F = B @ B.T + 1e-4 * np.eye(K)
    spec = (rng.uniform(0.04, 0.12, 500) / np.sqrt(12)) ** 2
    d = risk_decomposition(X, F, spec, capw)
    s = d["sigma"]
    assert np.isclose(d["factor_contrib_var"].sum() + d["specific_var"], s**2)
    assert np.isclose(capw @ d["mctr"], s)
    xsr = d["exposures"] * np.sqrt(np.diag(F)) * d["xsr_corr"]   # x * sigma * rho
    assert np.isclose(xsr.sum() + d["specific_var"] / s, s)


# ---- Validation statistics ------------------------------------------------ #

def test_bias_statistic_under_truth():
    rng = np.random.default_rng(13)
    vol = np.full(5000, 0.02)
    assert abs(bias_statistic(vol * rng.standard_normal(5000), vol) - 1) < 0.03


def test_mrad_normal_is_about_0_17():
    b = np.random.default_rng(14).standard_normal((240, 200))
    assert abs(mrad(b, 12) - 0.17) < 0.01


def test_qlike_minimized_at_true_scale():
    rng = np.random.default_rng(15)
    vol = np.full(20000, 0.02)
    r = vol * rng.standard_normal(20000)
    assert qlike(r, vol) < qlike(r, 0.8 * vol) and qlike(r, vol) < qlike(r, 1.25 * vol)
