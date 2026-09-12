"""Descriptor and exposure-QA kernels (blueprint §6.2-§6.4).

Pure functions: numpy arrays in, numpy arrays out. Panels are (T, N): rows are sessions, oldest
first; columns are securities. Missing values are NaN and never count as zero returns.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

from eqrisk.kernels.reference import ewma_weights

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

# Consistency constant: for normal data, 1.4826 x MAD estimates the standard deviation.
MAD_TO_SIGMA = 1.4826


def lag_rows(X: FloatArray, lag: int) -> FloatArray:
    """Row t holds X[t - lag]; the first `lag` rows are NaN."""
    out = np.full(X.shape, np.nan)
    if lag == 0:
        out[:] = X
    elif lag < X.shape[0]:
        out[lag:] = X[:-lag]
    return out


def rolling_ewma_moments(X: FloatArray, window: int, half_life: float, min_obs: int,
                         chunk: int = 32) -> tuple[FloatArray, FloatArray]:
    """EWMA-weighted mean and standard deviation of the `window` rows ending at each row.

    Weights put the newest observation largest and are renormalized over non-missing values.
    Rows before the first full window, or with fewer than `min_obs` values, are NaN.
    """
    T, N = X.shape
    mean = np.full((T, N), np.nan)
    std = np.full((T, N), np.nan)
    if window > T:
        return mean, std
    w = ewma_weights(window, half_life)
    V = sliding_window_view(X, window, axis=0)                  # (T - W + 1, N, W)
    for s in range(0, V.shape[0], chunk):
        Xc = V[s:s + chunk]
        ok = np.isfinite(Xc)
        ww = np.where(ok, w, 0.0)
        sw = ww.sum(-1)
        Z = np.where(ok, Xc, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = (ww * Z).sum(-1) / sw
            v = (ww * (Z - m[..., None]) ** 2).sum(-1) / sw
        few = ok.sum(-1) < min_obs
        m[few] = np.nan
        v[few] = np.nan
        rows = slice(window - 1 + s, window - 1 + s + Xc.shape[0])
        mean[rows] = m
        std[rows] = np.sqrt(v)
    return mean, std


def cmra(X: FloatArray, months: int, month_len: int, min_obs: int, z_floor: float) -> FloatArray:
    """Cumulative range of monthly returns: ln(1 + Z_max) - ln(1 + Z_min) (blueprint §6.2).

    Z(m) is the sum of the last m * month_len log excess returns (missing counted as 0),
    m = 1..months, floored at `z_floor` (> -1). NaN where the months * month_len window holds
    fewer than `min_obs` valid returns.
    """
    T, N = X.shape
    span = months * month_len
    out = np.full((T, N), np.nan)
    if span > T:
        return out
    c = np.vstack([np.zeros((1, N)), np.cumsum(np.nan_to_num(X), axis=0)])
    n = np.vstack([np.zeros((1, N)), np.cumsum(np.isfinite(X), axis=0)])
    ends = np.arange(span, T + 1)                               # c-index just past rows span-1 .. T-1
    zmax = np.full((len(ends), N), -np.inf)
    zmin = np.full((len(ends), N), np.inf)
    for m in range(1, months + 1):
        Z = c[ends] - c[ends - m * month_len]
        zmax = np.maximum(zmax, Z)
        zmin = np.minimum(zmin, Z)
    val = np.log1p(np.maximum(zmax, z_floor)) - np.log1p(np.maximum(zmin, z_floor))
    val[(n[ends] - n[ends - span]) < min_obs] = np.nan
    out[span - 1:] = val
    return out


def block_turnover(turnover: FloatArray, block: int, n_blocks: int, min_fraction: float) -> FloatArray:
    """ln of the mean, over the last `n_blocks` non-overlapping blocks of `block` rows, of summed
    daily turnover (blueprint §6.2 STOM/STOQ/STOA). A block counts when at least `min_fraction`
    of its rows are valid, and its sum is then scaled to a full block. NaN when no block counts
    or turnover is zero. n_blocks = 1 gives STOM.
    """
    T, N = turnover.shape
    c = np.vstack([np.zeros((1, N)), np.cumsum(np.nan_to_num(turnover), axis=0)])
    n = np.vstack([np.zeros((1, N)), np.cumsum(np.isfinite(turnover), axis=0)])
    total = np.zeros((T, N))
    count = np.zeros((T, N))
    rows = np.arange(T)
    for j in range(n_blocks):
        end = rows - j * block                                 # inclusive last row of block j
        start = end - block + 1
        inside = start >= 0
        hi = np.clip(end + 1, 0, T)
        lo = np.clip(start, 0, T)
        S = c[hi] - c[lo]
        K = n[hi] - n[lo]
        valid = inside[:, None] & (min_fraction * block <= K)
        total += np.where(valid, S * block / np.maximum(K, 1.0), 0.0)
        count += valid
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(count > 0, np.log(total / np.maximum(count, 1.0)), np.nan)
    out[~np.isfinite(out)] = np.nan
    return out


def growth_rate(values: FloatArray, min_years: int) -> float:
    """Slope of an OLS fit of annual values (oldest first) on 1..n, divided by |mean| (§6.2 EGRO).

    Missing years keep their position. NaN with fewer than `min_years` values or a zero mean.
    """
    y = np.asarray(values, dtype=float)
    t = np.arange(1, len(y) + 1, dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < min_years:
        return float("nan")
    yo, to = y[ok], t[ok]
    mean = float(yo.mean())
    if mean == 0.0:
        return float("nan")
    tc = to - to.mean()
    slope = float((tc * (yo - mean)).sum() / (tc * tc).sum())
    return slope / abs(mean)


def robust_zscore(x: FloatArray, mask: BoolArray) -> FloatArray:
    """(x - median) / (1.4826 MAD), with the median and MAD taken over `mask` & finite."""
    ok = mask & np.isfinite(x)
    if not ok.any():
        return np.full(x.shape, np.nan)
    med = float(np.median(x[ok]))
    mad = float(np.median(np.abs(x[ok] - med))) * MAD_TO_SIGMA
    if mad == 0.0:
        return np.where(np.isfinite(x), 0.0, np.nan)
    return (x - med) / mad


def weighted_corr(a: FloatArray, b: FloatArray, w: FloatArray, mask: BoolArray) -> float:
    """Weighted Pearson correlation over `mask` (the §6.4 factor stability coefficient)."""
    ok = mask & np.isfinite(a) & np.isfinite(b) & np.isfinite(w) & (w > 0)
    if ok.sum() < 3:
        return float("nan")
    ww = w[ok] / w[ok].sum()
    ao, bo = a[ok], b[ok]
    da, db = ao - (ww * ao).sum(), bo - (ww * bo).sum()
    va, vb = float((ww * da * da).sum()), float((ww * db * db).sum())
    if va <= 0.0 or vb <= 0.0:
        return float("nan")
    return float((ww * da * db).sum()) / float(np.sqrt(va * vb))


def vif(X: FloatArray, w: FloatArray, mask: BoolArray, targets: list[int]) -> FloatArray:
    """Variance inflation 1 / (1 - R^2_k) of each target column regressed (weighted) on all
    other columns of X (§6.4). X must contain an intercept or columns spanning one."""
    ok = mask & np.all(np.isfinite(X), axis=1) & np.isfinite(w) & (w > 0)
    Xo, wo = X[ok], w[ok]
    sw = np.sqrt(wo)
    out = np.full(len(targets), np.nan)
    for i, k in enumerate(targets):
        y = Xo[:, k]
        A = np.delete(Xo, k, axis=1)
        beta, *_ = np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)
        resid = y - A @ beta
        ybar = float((wo * y).sum() / wo.sum())
        ss_tot = float((wo * (y - ybar) ** 2).sum())
        if ss_tot <= 0.0:
            continue
        r2 = 1.0 - float((wo * resid ** 2).sum()) / ss_tot
        out[i] = 1.0 / (1.0 - r2) if r2 < 1.0 else np.inf
    return out
