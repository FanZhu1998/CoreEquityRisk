"""Bias-statistic batteries (blueprint §11.2, USE4 Appendix A).

A forecast made at the close of t is scored against the realized return over the next `horizon`
sessions (sums of daily returns, §7.3), standardized by the forecast volatility. Forecast dates are
spaced `horizon` sessions apart, so the standardized returns do not overlap.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from eqrisk.kernels import bias_statistic, mrad, qlike


def forecast_dates(dates: list[date], available: set[date], horizon: int, start: date, end: date) -> list[int]:
    """Row indices t in [start, end] with a forecast and a complete realized window, `horizon` apart."""
    out: list[int] = []
    for t, d in enumerate(dates):
        in_range = start <= d <= end and d in available and t + horizon < len(dates)
        if in_range and (not out or t - out[-1] >= horizon):
            out.append(t)
    return out


def realized(Phi: np.ndarray, t: int, horizon: int) -> np.ndarray:
    return Phi[t + 1: t + 1 + horizon].sum(axis=0)


def factor_bias(dates: list[date], Phi: np.ndarray, observed: np.ndarray, F: dict[date, np.ndarray],
                names: list[str], horizon: int, start: date, end: date) -> pl.DataFrame:
    """Per-factor bias statistic, 95% band 1 +/- sqrt(2/T), MRAD and QLIKE (pure factor portfolios)."""
    ts = forecast_dates(dates, set(F), horizon, start, end)
    b = np.full((len(ts), len(names)), np.nan)
    for i, t in enumerate(ts):
        ok = observed[t + 1: t + 1 + horizon].all(axis=0)
        vol = np.sqrt(np.diag(F[dates[t]]))
        b[i] = np.where(ok & (vol > 0), realized(Phi, t, horizon) / vol, np.nan)
    rows = []
    for k, name in enumerate(names):
        x = b[:, k][np.isfinite(b[:, k])]
        if len(x) < 3:
            continue
        rows.append({"factor": name, "n": len(x), "bias": bias_statistic(x, np.ones_like(x)),
                     "band": float(np.sqrt(2.0 / len(x))), "mrad": mrad(x[:, None]) if len(x) > 12 else None,
                     "qlike": qlike(x, np.ones_like(x))})
    return pl.DataFrame(rows)


def specific_bias(dates: list[date], U: np.ndarray, sigma: np.ndarray, capw: np.ndarray, estu: np.ndarray,
                  group: np.ndarray, horizon: int, start: date, end: date) -> dict[str, float | dict[int, float]]:
    """Cap-weighted bias statistic of standardized specific returns pooled over ESTU names and
    non-overlapping forecast dates, overall and per size decile (§9, §11.2 family g)."""
    avail = {d for t, d in enumerate(dates) if np.isfinite(sigma[t]).any()}
    ts = forecast_dates(dates, avail, horizon, start, end)
    b_all, w_all, g_all = [], [], []
    for t in ts:
        R = U[t + 1: t + 1 + horizon].sum(axis=0)
        full = np.isfinite(U[t + 1: t + 1 + horizon]).all(axis=0)
        m = estu[t] & full & np.isfinite(sigma[t]) & (sigma[t] > 0) & np.isfinite(capw[t])
        b_all.append(R[m] / sigma[t][m])
        w_all.append(capw[t][m])
        g_all.append(group[t][m])
    b, w, g = np.concatenate(b_all), np.concatenate(w_all), np.concatenate(g_all)

    def wstd(x: np.ndarray, wt: np.ndarray) -> float:
        wt = wt / wt.sum()
        mu = float((wt * x).sum())
        return float(np.sqrt((wt * (x - mu) ** 2).sum()))

    return {"overall": wstd(b, w), "n": float(len(b)), "dates": float(len(ts)),
            "by_decile": {int(k): wstd(b[g == k], w[g == k]) for k in np.unique(g) if (g == k).sum() > 10}}


def eigen_bias_battery(dates: list[date], Phi: np.ndarray, pre: dict[date, np.ndarray], post: dict[date, np.ndarray],
                       horizon: int, start: date, end: date) -> pl.DataFrame:
    """Bias of eigenfactor portfolios of the layers 1-2 matrix, forecast with the layers 1-2 matrix
    (before) and with the eigen-adjusted matrix (after). k = 0 is the smallest eigenvalue."""
    ts = forecast_dates(dates, set(pre) & set(post), horizon, start, end)
    K = Phi.shape[1]
    before = np.full((len(ts), K), np.nan)
    after = np.full((len(ts), K), np.nan)
    for i, t in enumerate(ts):
        d0, U = np.linalg.eigh(pre[dates[t]])
        r = U.T @ realized(Phi, t, horizon)
        v_after = np.einsum("ik,ij,jk->k", U, post[dates[t]], U)
        before[i] = r / np.sqrt(np.maximum(d0, 1e-300))
        after[i] = r / np.sqrt(np.maximum(v_after, 1e-300))
    return pl.DataFrame({"k": np.arange(K), "n": len(ts),
                         "bias_before": np.std(before, axis=0, ddof=1), "bias_after": np.std(after, axis=0, ddof=1)})
