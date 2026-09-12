"""Cross-checks for the Sec. 12 optimizer code (extracted verbatim from the blueprint)."""
import numpy as np
import pandas as pd
import cvxpy as cp

from eqrisk.optimize.factor_form import optimize_active
from eqrisk.optimize.riskfolio_adapter import exposure_bounds, to_riskfolio


def _model(seed=11, N=500, n_ind=20, n_sty=12):
    rng = np.random.default_rng(seed)
    K = 1 + n_ind + n_sty
    ind = rng.integers(0, n_ind, N)
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], rng.standard_normal((N, n_sty))])
    B = rng.standard_normal((K, K)) * 0.01
    F = B @ B.T + 1e-4 * np.eye(K)                               # monthly
    spec = (rng.uniform(0.04, 0.12, N) / np.sqrt(12)) ** 2         # monthly
    mcap = np.exp(rng.normal(10, 1.2, N))
    return rng, X, F, spec, mcap / mcap.sum(), n_ind, K


def test_factor_form_matches_dense_min_variance():
    _, X, F, spec, _, _, _ = _model()
    Sigma = X @ F @ X.T + np.diag(spec)
    w1 = cp.Variable(len(spec))
    cp.Problem(cp.Minimize(cp.quad_form(w1, cp.psd_wrap(Sigma))), [cp.sum(w1) == 1, w1 >= 0]).solve(solver=cp.CLARABEL)
    w2, status = optimize_active(X, F, spec, np.zeros(len(spec)), w_max=1.0)
    assert status == "optimal" and np.abs(w1.value - w2).max() < 1e-4


def test_active_optimization_respects_te_and_exposure_bands():
    rng, X, F, spec, w_b, n_ind, K = _model()
    alpha = rng.normal(0, 0.002, len(spec))
    sty, ind = np.arange(1 + n_ind, K), np.arange(1, 1 + n_ind)
    w, status = optimize_active(X, F, spec, w_b, alpha=alpha, te_max_ann=0.03,
                                style_idx=sty, ind_idx=ind)
    a = w - w_b
    te = np.sqrt((a @ X @ F @ X.T @ a + np.sum(a**2 * spec)) * 12)
    y = X.T @ a
    assert status == "optimal" and te <= 0.03 + 1e-6
    assert np.abs(y[sty]).max() <= 0.10 + 1e-6 and np.abs(y[ind]).max() <= 0.02 + 1e-6


def test_riskfolio_adapter_bounds_and_agreement_with_factor_form():
    rng, Xa, Fa, speca, _, n_ind, K = _model(seed=5, N=120, n_ind=8, n_sty=5)
    assets = [f"A{i:03d}" for i in range(120)]
    factors = ["COUNTRY"] + [f"IND_{i}" for i in range(n_ind)] + ["SIZE", "BETA", "MOM", "RESVOL", "BTOP"]
    X = pd.DataFrame(Xa, index=assets, columns=factors)
    F = pd.DataFrame(Fa, index=factors, columns=factors)
    spec = pd.Series(speca, index=assets)
    bounds = {"SIZE": (-0.1, 0.1), "BETA": (-0.1, 0.1), "MOM": (0.2, 0.5)}
    port = to_riskfolio(X, F, spec)
    port.ainequality, port.binequality = exposure_bounds(X, bounds)
    w_rp = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)["weights"].values
    x = Xa.T @ w_rp
    for k, (lo, hi) in bounds.items():
        j = factors.index(k)
        assert lo - 1e-6 <= x[j] <= hi + 1e-6
    # same problem in native factor form
    d, U = np.linalg.eigh(Fa)
    L = U * np.sqrt(np.maximum(d, 0))
    w = cp.Variable(120)
    y = Xa.T @ w
    var = cp.sum_squares(L.T @ y) + cp.sum_squares(cp.multiply(np.sqrt(speca), w))
    cons = [cp.sum(w) == 1, w >= 0]
    for k, (lo, hi) in bounds.items():
        j = factors.index(k)
        cons += [y[j] >= lo, y[j] <= hi]
    cp.Problem(cp.Minimize(var), cons).solve(solver=cp.CLARABEL)
    Sigma = Xa @ Fa @ Xa.T + np.diag(speca)
    v_rp, v_ff = w_rp @ Sigma @ w_rp, w.value @ Sigma @ w.value
    assert abs(np.sqrt(v_rp) / np.sqrt(v_ff) - 1) < 1e-3     # same optimum risk
    assert np.abs(w_rp - w.value).max() < 1e-3
