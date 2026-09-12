"""Specific-risk kernels (blueprint §9.1, §9.3), vectorized across securities.

Column by column they reproduce reference.specific_ts_var and reference.coverage_gamma; the
tests hold them to that.
"""

from __future__ import annotations

import warnings

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

# Normal-distribution IQR in standard deviations, as in reference.coverage_gamma.
IQR_TO_SIGMA = 1.35


def compact_last(U: FloatArray, n: int) -> tuple[FloatArray, IntArray]:
    """Each column's last `n` finite values in time order, bottom-aligned in an (n, N) array
    (NaN above them), and how many there are. Missing days are skipped, not zero-filled
    (observation-sequence decay)."""
    ok = np.isfinite(U)
    order = np.argsort(ok, axis=0, kind="stable")        # NaNs first, then finite values in time order
    S = np.take_along_axis(U, order, axis=0)
    if S.shape[0] < n:
        S = np.vstack([np.full((n - S.shape[0], U.shape[1]), np.nan), S])
    return S[-n:], np.minimum(ok.sum(axis=0), n).astype(np.int64)


def _lag_moments(A: FloatArray, counts: IntArray, half_life: float, lags: int) -> FloatArray:
    """(lags + 1, N) EWMA cross-moments C_l = sum_t w_t a_{t-l} a_t of bottom-aligned sequences,
    weights on the later observation, normalized over each column's valid rows (the first l
    weights drop out without renormalization, as in reference.ewma_lag_cov)."""
    n, N = A.shape
    lam = 0.5 ** (1.0 / half_life)
    w = lam ** np.arange(n - 1, -1, -1, dtype=float)
    norm = (1.0 - lam ** counts.astype(float)) / (1.0 - lam)
    Z = np.nan_to_num(A)
    out = np.empty((lags + 1, N))
    for lag in range(lags + 1):
        prod = Z * Z if lag == 0 else np.vstack([np.zeros((lag, N)), Z[:-lag] * Z[lag:]])
        out[lag] = (w[:, None] * prod).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        res: FloatArray = out / norm
    return res


def specific_ts_panel(U: FloatArray, window: int, hl_vol: float, nw_lags: int,
                      nw_hl: float) -> tuple[FloatArray, FloatArray, IntArray]:
    """Per column, over its last `window` finite values: (daily EWMA variance at `hl_vol`, raw
    Newey-West ratio V_NW / C_0 at `nw_hl`, count). v0 * clip(ratio) equals
    reference.specific_ts_var for that column."""
    A, counts = compact_last(U, window)
    v0 = _lag_moments(A, counts, hl_vol, 0)[0]
    C = _lag_moments(A, counts, nw_hl, nw_lags)
    V = C[0] + sum((1.0 - lag / (nw_lags + 1.0)) * 2.0 * C[lag] for lag in range(1, nw_lags + 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = V / C[0]
    return v0, ratio, counts


def coverage_gamma_panel(U: FloatArray, h_min: int, h_ramp: int) -> FloatArray:
    """CNE5-style blending coefficient per column (reference.coverage_gamma, vectorized)."""
    ok = np.isfinite(U)
    h = ok.sum(axis=0)
    out = np.zeros(U.shape[1])
    cols = h >= 2
    if not cols.any():
        return out
    Uc = U[:, cols]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        q1, q3 = np.nanpercentile(Uc, [25, 75], axis=0)
        s_eq = np.nanstd(Uc, axis=0, ddof=1)
    s_rob = (q3 - q1) / IQR_TO_SIGMA
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(s_rob > 0, np.abs((s_eq - s_rob) / s_rob), np.inf)
        g = np.clip((h[cols] - h_min) / h_ramp, 0.0, 1.0) * np.clip(np.exp(1.0 - z), 0.0, 1.0)
    out[cols] = g
    return out
