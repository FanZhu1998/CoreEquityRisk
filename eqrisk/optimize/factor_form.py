"""Native factor-form optimizer (blueprint 12.2, verbatim apart from this docstring)."""
# eqrisk/optimize/factor_form.py
import numpy as np
import cvxpy as cp

def optimize_active(X, F, spec_var, w_b, alpha=None, te_max_ann=None,
                    style_idx=None, style_bound=0.10, ind_idx=None, ind_bound=0.02,
                    w_max=0.05, w_prev=None, turnover_max=None, risk_aversion=10.0):
    """F and spec_var at the monthly horizon; te_max_ann is annualized."""
    d, U = np.linalg.eigh(F)
    L = U * np.sqrt(np.maximum(d, 0.0))                 # F = L L'
    w = cp.Variable(X.shape[0])
    a = w - w_b
    y = X.T @ a                                         # active factor exposures (K)
    active_var = cp.sum_squares(L.T @ y) + cp.sum_squares(cp.multiply(np.sqrt(spec_var), a))
    cons = [cp.sum(w) == 1, w >= 0, w <= w_max]
    if style_idx is not None:
        cons += [cp.abs(y[style_idx]) <= style_bound]
    if ind_idx is not None:
        cons += [cp.abs(y[ind_idx]) <= ind_bound]
    if te_max_ann is not None:
        cons += [active_var <= te_max_ann**2 / 12.0]
    if turnover_max is not None and w_prev is not None:
        cons += [cp.norm1(w - w_prev) <= turnover_max]
    obj = (cp.Minimize(active_var) if alpha is None
           else cp.Maximize(alpha @ w - risk_aversion * active_var))
    prob = cp.Problem(obj, cons)
    prob.solve(solver=cp.CLARABEL)
    return w.value, prob.status
