"""EQRisk reference kernels for a USE4-style factor risk model.

Pure functions: numpy arrays in, numpy arrays out. No I/O, no pandas.

Conventions
-----------
* Time-series arrays are shaped (T, K): rows = dates, oldest first, newest last.
* Half-lives are in observations (trading days).
* Second moments are taken about zero (no demeaning); daily means are ~0.
* Kernels return daily-horizon quantities; callers apply the 21-day horizon.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

Estimator = Callable[[np.ndarray], np.ndarray]

# --------------------------------------------------------------------------- #
# EWMA / Newey-West (USE4 Sec. 4.1)                                           #
# --------------------------------------------------------------------------- #


def ewma_weights(T: int, half_life: float) -> np.ndarray:
    """Normalized exponential weights, newest observation last (largest)."""
    lam = 0.5 ** (1.0 / half_life)
    w = lam ** np.arange(T - 1, -1, -1, dtype=float)
    return w / w.sum()


def effective_obs(T: int, half_life: float) -> float:
    """Effective number of observations 1 / sum(w^2)."""
    w = ewma_weights(T, half_life)
    return float(1.0 / np.sum(w**2))


def ewma_lag_cov(R: np.ndarray, half_life: float, lag: int = 0) -> np.ndarray:
    """EWMA cross-moment C_lag = sum_t w_t r_{t-lag} r_t' (weights on the later obs).

    The first `lag` weights drop out without renormalization.
    Returns K x K (not symmetric for lag > 0).
    """
    T = R.shape[0]
    w = ewma_weights(T, half_life)
    if lag == 0:
        return (R * w[:, None]).T @ R
    lead, lagged = R[lag:], R[:-lag]
    return (lagged * w[lag:, None]).T @ lead


def newey_west_cov(R: np.ndarray, half_life: float, lags: int) -> np.ndarray:
    """Bartlett-weighted HAC covariance: C0 + sum_l (1 - l/(L+1)) (C_l + C_l')."""
    V = ewma_lag_cov(R, half_life, 0)
    for lag in range(1, lags + 1):
        C = ewma_lag_cov(R, half_life, lag)
        V = V + (1.0 - lag / (lags + 1.0)) * (C + C.T)
    return 0.5 * (V + V.T)


def psd_clip(A: np.ndarray, floor: float = 1e-14) -> np.ndarray:
    """Symmetrize and clip eigenvalues at `floor`."""
    A = 0.5 * (A + A.T)
    d, U = np.linalg.eigh(A)
    return (U * np.maximum(d, floor)) @ U.T


def combine_vol_corr(V_vol: np.ndarray, V_corr: np.ndarray) -> np.ndarray:
    """Volatilities from V_vol, correlations from V_corr: F = D_s C D_s (USE4 Eq. 4.1)."""
    s = np.sqrt(np.clip(np.diag(V_vol), 0.0, None))
    c = np.sqrt(np.clip(np.diag(V_corr), 1e-300, None))
    C = V_corr / np.outer(c, c)
    return psd_clip(np.outer(s, s) * C)


def factor_cov_nw(R: np.ndarray, hl_vol: float, lags_vol: int,
                  hl_corr: float, lags_corr: int) -> np.ndarray:
    """Daily-horizon factor covariance, split half-lives, NW-adjusted (Layers 1-2)."""
    return combine_vol_corr(newey_west_cov(R, hl_vol, lags_vol),
                            newey_west_cov(R, hl_corr, lags_corr))


# --------------------------------------------------------------------------- #
# Eigenfactor risk adjustment (USE4 Sec. 4.2, Appendix B)                     #
# --------------------------------------------------------------------------- #


def eigen_bias(F0: np.ndarray, T_sim: int, n_sims: int, rng: np.random.Generator,
               estimator: Estimator | None = None, batch: int = 100) -> np.ndarray:
    """Simulated volatility bias v(k) (Eq. B7), eigenvalues in ascending order.

    estimator: callable (T, K) -> (K, K). None = equal-weighted second moment.
    Pass the production estimator, e.g. lambda R: factor_cov_nw(R, 84, 5, 504, 2),
    with T_sim = actual window length to follow USE4's "same estimator" logic.
    """
    K = F0.shape[0]
    d0, U0 = np.linalg.eigh(F0)
    d0 = np.maximum(d0, 1e-14)
    acc = np.zeros(K)
    done = 0
    while done < n_sims:
        m = min(batch, n_sims - done)
        b = rng.standard_normal((m, T_sim, K)) * np.sqrt(d0)    # eigen-space returns
        f = b @ U0.T                                            # (m, T, K) factor returns
        if estimator is None:
            Fm = np.einsum("mtk,mtj->mkj", f, f) / T_sim
        else:
            Fm = np.stack([estimator(f[i]) for i in range(m)])
        dm, Um = np.linalg.eigh(Fm)                             # batched
        dm = np.maximum(dm, 1e-14)
        true_var = np.einsum("mki,kl,mli->mi", Um, F0, Um)       # diag(Um' F0 Um)
        acc += (true_var / dm).sum(axis=0)
        done += m
    return np.sqrt(acc / n_sims)


def eigen_adjust(F: np.ndarray, v: np.ndarray, a: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Apply gamma(k) = a (v(k) - 1) + 1 to the eigenvalues. a = 1 is Eq. B7."""
    d0, U0 = np.linalg.eigh(F)
    gamma = a * (v - 1.0) + 1.0
    F_eig = (U0 * (np.maximum(d0, 1e-14) * gamma**2)) @ U0.T
    return 0.5 * (F_eig + F_eig.T), gamma


# --------------------------------------------------------------------------- #
# Volatility regime adjustment (USE4 Eq. 4.3-4.5, 5.10-5.12)                  #
# --------------------------------------------------------------------------- #


def vra_lambda(bias_sq: np.ndarray, half_life: float) -> np.ndarray:
    """Causal lambda_t = sqrt(EWMA_{<=t}(B^2)); bias_sq is a 1-D daily series."""
    alpha = 1.0 - 0.5 ** (1.0 / half_life)
    out = np.empty(len(bias_sq), dtype=float)
    s = float(bias_sq[0])
    for t, b in enumerate(bias_sq):
        s = alpha * float(b) + (1.0 - alpha) * s if t else float(b)
        out[t] = np.sqrt(s)
    return out


def factor_bias_sq(f_t: np.ndarray, sigma_prev: np.ndarray, z_cap: float = 10.0) -> float:
    """(B^F_t)^2 = mean_k (f_kt / sigma_kt)^2; sigma = one-day forecast from t-1."""
    z = np.clip(f_t / sigma_prev, -z_cap, z_cap)
    return float(np.mean(z**2))


def specific_bias_sq(u_t: np.ndarray, sigma_prev: np.ndarray, capw: np.ndarray,
                     z_cap: float = 10.0) -> float:
    """(B^S_t)^2 = sum_n w_n (u_nt / sigma_nt)^2 over ESTU, cap weights."""
    z = np.clip(u_t / sigma_prev, -z_cap, z_cap)
    w = capw / capw.sum()
    return float(np.sum(w * z**2))


# --------------------------------------------------------------------------- #
# Cross-sectional machinery (USE4 Sec. 2-3)                                   #
# --------------------------------------------------------------------------- #


def standardize(x: np.ndarray, capw: np.ndarray, estu: np.ndarray) -> np.ndarray:
    """USE4 Eq. 2.4: cap-weighted mean, equal-weighted std, moments on ESTU."""
    ok = estu & np.isfinite(x)
    mu = np.average(x[ok], weights=capw[ok])
    sd = np.std(x[ok], ddof=0)
    return (x - mu) / sd


def trim_sigma(x: np.ndarray, estu: np.ndarray, k: float = 3.0) -> np.ndarray:
    """Clip to mean +/- k*std (equal-weighted moments on ESTU)."""
    ok = estu & np.isfinite(x)
    mu, sd = x[ok].mean(), x[ok].std()
    return np.clip(x, mu - k * sd, mu + k * sd)


def orthogonalize(y: np.ndarray, Z: np.ndarray, w: np.ndarray,
                  fit_mask: np.ndarray) -> np.ndarray:
    """Residual of WLS y ~ [1, Z] fitted on fit_mask with weights w, applied to all."""
    A = np.column_stack([np.ones(len(y)), Z])
    ok = fit_mask & np.isfinite(y) & np.all(np.isfinite(A), axis=1)
    sw = np.sqrt(w[ok])
    beta, *_ = np.linalg.lstsq(A[ok] * sw[:, None], y[ok] * sw, rcond=None)
    return y - A @ beta


def industry_neff(v: np.ndarray) -> float:
    """Effective number of names (sum v)^2 / sum v^2 for regression weights v."""
    return float(v.sum() ** 2 / np.sum(v**2))


def constrained_wls(r: np.ndarray, X: np.ndarray, v: np.ndarray, ind_cols: np.ndarray,
                    ind_capw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """USE4 Eq. 3.1-3.3 with the constraint sum_i w_i f_i = 0 over industry columns.

    r (N,) excess returns; X (N, K) = [country | industries | styles];
    v (N,) regression weights (e.g. sqrt mcap); ind_cols: industry column indices;
    ind_capw: industry cap weights summing to 1. All industries must be non-empty.
    Returns f (K,), u (N,), Omega (K, N) with f = Omega r (pure factor portfolios).
    """
    K = X.shape[1]
    j_e = int(np.argmax(ind_capw))
    e = int(ind_cols[j_e])                              # eliminate the heaviest industry
    free = [k for k in range(K) if k != e]
    pos = {k: j for j, k in enumerate(free)}
    R = np.zeros((K, K - 1))
    for k, j in pos.items():
        R[k, j] = 1.0
    for c, w_i in zip(ind_cols, ind_capw):
        if int(c) != e:
            R[e, pos[int(c)]] = -w_i / ind_capw[j_e]    # f_e = -sum_{i!=e} (w_i/w_e) f_i
    XR = X @ R
    V = v / v.sum()
    A = XR.T @ (XR * V[:, None])
    Omega = R @ np.linalg.solve(A, (XR * V[:, None]).T)
    f = Omega @ r
    return f, r - X @ f, Omega


# --------------------------------------------------------------------------- #
# Descriptors                                                                 #
# --------------------------------------------------------------------------- #


def rolling_ewma_beta(r_ex: np.ndarray, m_ex: np.ndarray, window: int = 252,
                      hl: float = 63, min_obs: int = 63,
                      chunk: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """HBETA and HSIGMA: EWMA-weighted market-model regression on a trailing window.

    r_ex (T, N) stock excess returns (NaN allowed); m_ex (T,) market excess returns.
    Returns (beta, hsigma), each (T, N); rows before the first full window are NaN.
    """
    T, N = r_ex.shape
    w = ewma_weights(window, hl)
    beta = np.full((T, N), np.nan)
    hsig = np.full((T, N), np.nan)
    Rw = sliding_window_view(r_ex, window, axis=0)       # (T-W+1, N, W) view, no copy
    Mw = sliding_window_view(m_ex, window)               # (T-W+1, W)
    for s in range(0, Rw.shape[0], chunk):
        Rc = Rw[s:s + chunk]
        M = Mw[s:s + chunk][:, None, :]
        ok = np.isfinite(Rc)
        ww = np.where(ok, w, 0.0)
        sw = ww.sum(-1)
        Rz = np.where(ok, Rc, 0.0)
        mb = (ww * M).sum(-1) / sw
        rb = (ww * Rz).sum(-1) / sw
        dm = M - mb[..., None]
        dr = Rz - rb[..., None]
        var = (ww * dm**2).sum(-1) / sw
        b = (ww * dr * dm).sum(-1) / sw / var
        e = np.where(ok, dr - b[..., None] * dm, 0.0)
        hs = np.sqrt((ww * e**2).sum(-1) / sw)
        bad = ok.sum(-1) < min_obs
        b[bad] = np.nan
        hs[bad] = np.nan
        beta[window - 1 + s: window - 1 + s + len(Rc)] = b
        hsig[window - 1 + s: window - 1 + s + len(Rc)] = hs
    return beta, hsig


# --------------------------------------------------------------------------- #
# Specific risk (USE4 Sec. 5)                                                 #
# --------------------------------------------------------------------------- #


def specific_ts_var(u: np.ndarray, hl_vol: float, nw_lags: int, nw_hl: float,
                    floor_frac: float = 0.25, cap_frac: float = 4.0) -> float:
    """One stock's daily specific variance with a bounded NW multiplier (USE4 Eq. 5.2).

    sigma^2 = C_NW * EWMA_{hl_vol}(u^2), C_NW = V_NW / V_0 (both at nw_hl),
    bounded to [floor_frac, cap_frac] so bid-ask bounce cannot collapse the estimate.
    Missing days are skipped (observation-sequence decay).
    """
    x = u[np.isfinite(u)][:, None]
    v0 = ewma_lag_cov(x, hl_vol, 0)[0, 0]
    c0 = ewma_lag_cov(x, nw_hl, 0)[0, 0]
    cnw = newey_west_cov(x, nw_hl, nw_lags)[0, 0] / c0
    return float(v0 * np.clip(cnw, floor_frac, cap_frac))


def coverage_gamma(u_window: np.ndarray, h_min: int = 60, h_ramp: int = 120) -> float:
    """CNE5-style blending coefficient (not spelled out in the USE4 notes)."""
    x = u_window[np.isfinite(u_window)]
    h = len(x)
    if h < 2:
        return 0.0
    q1, q3 = np.percentile(x, [25, 75])
    s_rob = (q3 - q1) / 1.35
    s_eq = x.std(ddof=1)
    z = abs((s_eq - s_rob) / s_rob) if s_rob > 0 else np.inf
    return float(min(1.0, max(0.0, (h - h_min) / h_ramp)) * min(1.0, max(0.0, np.exp(1 - z))))


def bayes_shrink(sigma: np.ndarray, capw: np.ndarray, group: np.ndarray,
                 q: float = 0.1) -> np.ndarray:
    """USE4 Eq. 5.6-5.9: shrink toward the cap-weighted mean of each size group."""
    out = sigma.copy()
    for g in np.unique(group):
        m = group == g
        mu = np.average(sigma[m], weights=capw[m])
        delta = np.sqrt(np.mean((sigma[m] - mu) ** 2))
        dev = np.abs(sigma[m] - mu)
        den = delta + q * dev
        vshr = np.divide(q * dev, den, out=np.zeros_like(dev), where=den > 0)
        out[m] = vshr * mu + (1.0 - vshr) * sigma[m]
    return out


# --------------------------------------------------------------------------- #
# Risk analytics (Sec. 10)                                                    #
# --------------------------------------------------------------------------- #


def risk_decomposition(X: np.ndarray, F: np.ndarray, spec_var: np.ndarray,
                       h: np.ndarray) -> dict[str, np.ndarray | float]:
    """Total risk, Euler factor contributions, specific variance, MCTR (monthly units)."""
    x = X.T @ h
    Fx = F @ x
    fac_var = float(x @ Fx)
    spc_var = float(np.sum(h**2 * spec_var))
    sigma = np.sqrt(fac_var + spc_var)
    return {
        "sigma": sigma,
        "exposures": x,
        "factor_contrib_var": x * Fx,                   # sums to fac_var
        "specific_var": spc_var,
        "mctr": (X @ Fx + spec_var * h) / sigma,         # sum(h * mctr) == sigma
        "xsr_corr": Fx / (np.sqrt(np.diag(F)) * sigma),  # rho(k, p) for x-sigma-rho
    }


# --------------------------------------------------------------------------- #
# Validation (USE4 Appendix A)                                                #
# --------------------------------------------------------------------------- #


def bias_statistic(ret: np.ndarray, vol: np.ndarray) -> float:
    """USE4 Eq. A2: standard deviation of standardized returns (ddof=1)."""
    return float(np.std(ret / vol, ddof=1))


def mrad(b: np.ndarray, window: int = 12) -> float:
    """Mean rolling absolute deviation of rolling bias statistics from 1.

    b: (T, P) standardized returns for P portfolios on NON-OVERLAPPING periods.
    """
    b = np.atleast_2d(b.T).T
    stats = [np.std(b[t - window:t], axis=0, ddof=1) for t in range(window, len(b) + 1)]
    return float(np.mean(np.abs(np.array(stats) - 1.0)))


def qlike(ret: np.ndarray, vol: np.ndarray, eps: float = 1e-12) -> float:
    """Q-likelihood loss mean(b^2 - ln b^2); minimized in expectation at the true vol."""
    b2 = (ret / vol) ** 2 + eps
    return float(np.mean(b2 - np.log(b2)))
