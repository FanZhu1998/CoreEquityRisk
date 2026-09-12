"""Cross-sectional regression helpers around reference.constrained_wls (blueprint §7.2-§7.3)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from eqrisk.kernels.descriptors import MAD_TO_SIGMA

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
IntArray = NDArray[np.int64]


def restriction_matrix(K: int, ind_cols: IntArray, ind_capw: FloatArray) -> FloatArray:
    """R (K x K-1) with f = R g, exactly as constrained_wls builds it: identity on the free factors,
    and the heaviest industry's row holding -w_i / w_e, so that sum_i w_i f_i = 0."""
    j_e = int(np.argmax(ind_capw))
    e = int(ind_cols[j_e])
    free = [k for k in range(K) if k != e]
    pos = {k: j for j, k in enumerate(free)}
    R = np.zeros((K, K - 1))
    for k, j in pos.items():
        R[k, j] = 1.0
    for c, w_i in zip(ind_cols, ind_capw, strict=True):
        if int(c) != e:
            R[e, pos[int(c)]] = -w_i / ind_capw[j_e]
    return R


def mad_clip(r: FloatArray, mask: BoolArray, k: float) -> FloatArray:
    """Clip to median +/- k * 1.4826 MAD, with median and MAD over `mask` & finite (§7.3)."""
    ok = mask & np.isfinite(r)
    if not ok.any():
        return r.copy()
    med = float(np.median(r[ok]))
    scale = float(np.median(np.abs(r[ok] - med))) * MAD_TO_SIGMA
    if scale == 0.0:
        return r.copy()
    return np.clip(r, med - k * scale, med + k * scale)


def wls_diagnostics(r: FloatArray, X: FloatArray, v: FloatArray, f: FloatArray,
                    R: FloatArray) -> tuple[float, float, FloatArray]:
    """Weighted R^2 (against the weighted mean), condition number of sqrt(V) X R, and the
    covariance of f = R g under WLS with weights v: Var(g) = s^2 A^-1, A = (XR)' V (XR) with V = v/sum v,
    s^2 = sum V u^2 / (n - p)."""
    V = v / v.sum()
    u = r - X @ f
    rbar = float((V * r).sum())
    ss_tot = float((V * (r - rbar) ** 2).sum())
    r2 = 1.0 - float((V * u * u).sum()) / ss_tot if ss_tot > 0 else float("nan")
    XR = X @ R
    n, p = XR.shape
    cond = float(np.linalg.cond(np.sqrt(V)[:, None] * XR))
    A = XR.T @ (XR * V[:, None])
    s2 = float((V * u * u).sum()) / max(n - p, 1)
    cov_g = s2 * np.linalg.inv(A)
    return r2, cond, R @ cov_g @ R.T
