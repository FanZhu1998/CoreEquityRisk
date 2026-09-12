"""Riskfolio-Lib adapter (blueprint 12.1, verbatim apart from this docstring).

Usage (from the blueprint):

    # usage
    port = to_riskfolio(X, F, spec_var)
    port.ainequality, port.binequality = exposure_bounds(
        X, {"SIZE": (-0.1, 0.1), "BETA": (-0.1, 0.1), "MOMENTUM": (0.2, 0.5)})
    w = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)
"""
# eqrisk/optimize/riskfolio_adapter.py
import numpy as np
import pandas as pd
import riskfolio as rp

def to_riskfolio(X: pd.DataFrame, F: pd.DataFrame, spec_var: pd.Series,
                 mu: pd.Series | None = None,
                 returns_hist: pd.DataFrame | None = None) -> rp.Portfolio:
    """Inject Sigma = X F X' + Delta into a Riskfolio Portfolio (use model='Classic')."""
    Sigma = X @ F @ X.T + np.diag(spec_var.loc[X.index])
    Sigma = pd.DataFrame(Sigma.values, index=X.index, columns=X.index)
    if returns_hist is None:
        # Riskfolio needs a returns frame for asset names and shapes; the MV risk
        # measure itself reads port.cov. Pass real recent returns when you use
        # scenario-based risk measures (CVaR, CDaR, ...).
        returns_hist = pd.DataFrame(np.random.default_rng(0).multivariate_normal(
            np.zeros(len(X)), Sigma.values / 21, size=252), columns=X.index)
    port = rp.Portfolio(returns=returns_hist)
    port.mu = (mu if mu is not None else pd.Series(0.0, index=X.index)).to_frame().T
    port.cov = Sigma
    return port

def exposure_bounds(X: pd.DataFrame, bounds: dict[str, tuple[float, float]],
                    w_bench: pd.Series | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rows of A w <= b encoding lo <= x_k(w) - x_k(bench) <= hi (active exposure bounds)."""
    xb = X.T @ w_bench if w_bench is not None else pd.Series(0.0, index=X.columns)
    A, b = [], []
    for k, (lo, hi) in bounds.items():
        A.append(X[k].values);  b.append(hi + xb[k])
        A.append(-X[k].values); b.append(-(lo + xb[k]))
    return np.array(A), np.array(b).reshape(-1, 1)
