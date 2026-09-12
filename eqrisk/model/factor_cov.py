"""Factor covariance in four layers (blueprint §8).

Layer 1-2  EWMA second moments with split half-lives and Newey-West lags (volatilities from
           HL 84 / 5 lags, correlations from HL 504 / 2 lags), recombined and PSD-clipped.
Layer 3    Eigenfactor risk adjustment: simulated volatility bias v(k) with the same estimator
           on paths of the actual window length, gamma(k) = a (v(k) - 1) + 1.
Layer 4    Volatility regime adjustment: lambda_t^2 = EWMA of the cross-sectional mean of
           (f_kt / sigma_kt)^2, sigma_kt being the one-day lag-0 EWMA forecast made at t-1.
Everything is daily until the forecast is scaled once to the horizon (x21).

Missing factor returns (an industry with no members that day) are zero-filled for layers 1-3
(v1, §8.1) and excluded from the regime bias. Seeds come from sha256(model_id|date) (D-004).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.kernels import effective_obs, eigen_adjust, eigen_bias, factor_bias_sq, factor_cov_nw, vra_lambda


def date_seed(model_id: str, d: date) -> int:
    """Stable across processes (Python's hash() of str is salted per process): DECISIONS D-004."""
    return int.from_bytes(hashlib.sha256(f"{model_id}|{d.isoformat()}".encode()).digest()[:8], "big")


def factor_panel(fr: pl.DataFrame, names: list[str]) -> tuple[list[date], np.ndarray, np.ndarray]:
    """(dates, Phi zero-filled (T, K), observed mask) from long factor returns."""
    wide = fr.pivot(on="factor", index="date", values="f").sort("date")
    dates = wide["date"].to_list()
    cols = [wide[n].cast(pl.Float64).to_numpy() if n in wide.columns else np.full(len(dates), np.nan) for n in names]
    Phi = np.column_stack(cols).astype(float)
    observed = np.isfinite(Phi)
    return dates, np.where(observed, Phi, 0.0), observed


def one_day_sigma(Phi: np.ndarray, observed: np.ndarray, half_life: float, min_obs: int) -> np.ndarray:
    """sigma_t (T, K): the one-day forecast made at the close of t-1, i.e. sqrt of the lag-0 EWMA of
    f^2 over observed rows before t, renormalized over observed days. NaN until `min_obs` observations."""
    lam = 0.5 ** (1.0 / half_life)
    T, K = Phi.shape
    num, den, cnt = np.zeros(K), np.zeros(K), np.zeros(K)
    out = np.full((T, K), np.nan)
    for t in range(T):
        ok = (cnt >= min_obs) & (den > 0)
        out[t, ok] = np.sqrt(num[ok] / den[ok])
        num = lam * num + np.where(observed[t], Phi[t] ** 2, 0.0)
        den = lam * den + observed[t]
        cnt = cnt + observed[t]
    return out


def factor_vra(Phi: np.ndarray, observed: np.ndarray, sigma: np.ndarray, half_life: float,
               z_cap: float) -> tuple[np.ndarray, np.ndarray]:
    """(B^2_t, lambda_t): causal recursion seeded at the first day with a bias; lambda = 1 before."""
    T = Phi.shape[0]
    b2 = np.full(T, np.nan)
    for t in range(T):
        ok = observed[t] & np.isfinite(sigma[t]) & (sigma[t] > 0)
        if ok.any():
            b2[t] = factor_bias_sq(Phi[t, ok], sigma[t, ok], z_cap)
    lam = np.ones(T)
    valid = np.flatnonzero(np.isfinite(b2))
    if len(valid):
        first = valid[0]
        series = b2[first:].copy()
        for i in range(1, len(series)):             # a day with no usable factor keeps yesterday's state
            if not np.isfinite(series[i]):
                series[i] = series[i - 1]
        lam[first:] = vra_lambda(series, half_life)
    return b2, lam


def _estimator(cfg: ModelConfig, n_window: int) -> tuple[int, Callable[[np.ndarray], np.ndarray] | None]:
    fc = cfg.factor_cov
    if fc.eigen.estimator == "same":
        return n_window, lambda R: factor_cov_nw(R, fc.vol.half_life, fc.vol.nw_lags,
                                                 fc.corr.half_life, fc.corr.nw_lags)
    if fc.eigen.estimator == "equal_weight_neff":
        return int(round(effective_obs(n_window, fc.vol.half_life))), None
    return n_window, None


@dataclass
class FactorCovResult:
    names: list[str]
    cov: pl.DataFrame           # date, factor_i, factor_j, cov_final, cov_pre_eigen (upper triangle)
    risk: pl.DataFrame          # date, factor, vol_final, vol_pre_eigen, lambda_F, provisional
    eigen: pl.DataFrame         # date, k, eigenvalue, v_k, gamma_k (on simulation dates)
    vra: pl.DataFrame           # date, b2, lambda_F, cs_vol
    final: dict[date, np.ndarray] = field(default_factory=dict)   # monthly F_t
    pre: dict[date, np.ndarray] = field(default_factory=dict)     # monthly layers 1-2
    post_eigen: dict[date, np.ndarray] = field(default_factory=dict)  # monthly layers 1-3


def run_factor_cov(dates: list[date], Phi: np.ndarray, observed: np.ndarray, names: list[str], cfg: ModelConfig,
                   model_id: str, refresh: str, only: set[date] | None = None) -> FactorCovResult:
    """Forecasts for every date with at least `min_history_days` of factor returns (or just `only`).
    `refresh` is 'daily' (simulate every date) or 'weekly' (first session of each ISO week)."""
    fc, h = cfg.factor_cov, cfg.horizon_days
    K = len(names)
    sigma = one_day_sigma(Phi, observed, fc.vol.half_life, fc.vra.min_obs)
    b2, lam = factor_vra(Phi, observed, sigma, fc.vra.half_life, fc.vra.z_cap)
    iu, ju = np.triu_indices(K)
    res = FactorCovResult(names, pl.DataFrame(), pl.DataFrame(), pl.DataFrame(), pl.DataFrame())
    cov_rows: list[pl.DataFrame] = []
    risk_rows: list[dict[str, Any]] = []
    eig_rows: list[dict[str, Any]] = []
    v_last: np.ndarray | None = None
    week_last: tuple[int, int] | None = None
    for t in range(fc.min_history_days - 1, len(dates)):
        if only is not None and dates[t] not in only:
            continue
        W = Phi[max(0, t + 1 - fc.history_cap_days): t + 1]
        F0d = factor_cov_nw(W, fc.vol.half_life, fc.vol.nw_lags, fc.corr.half_life, fc.corr.nw_lags)
        if fc.eigen.enabled:
            week = tuple(dates[t].isocalendar()[:2])
            if refresh == "daily" or v_last is None or week != week_last:
                T_sim, est = _estimator(cfg, W.shape[0])
                rng = np.random.default_rng(date_seed(model_id, dates[t]))
                v_last = eigen_bias(F0d, T_sim, fc.eigen.n_sims, rng, est)
                week_last = week
                d0 = np.linalg.eigvalsh(F0d)
                gam = fc.eigen.a * (v_last - 1.0) + 1.0
                eig_rows.extend({"date": dates[t], "k": k, "eigenvalue": float(d0[k] * h), "v_k": float(v_last[k]),
                                 "gamma_k": float(gam[k])} for k in range(K))
            Fe, _ = eigen_adjust(F0d, v_last, fc.eigen.a)
        else:
            Fe = F0d
        lam_t = float(lam[t]) if fc.vra.enabled else 1.0
        F = h * lam_t * lam_t * Fe
        F = 0.5 * (F + F.T)
        Fpre = h * F0d
        res.final[dates[t]], res.pre[dates[t]], res.post_eigen[dates[t]] = F, Fpre, h * Fe
        cov_rows.append(pl.DataFrame({"date": [dates[t]] * len(iu), "factor_i": [names[i] for i in iu],
                                      "factor_j": [names[j] for j in ju], "cov_final": F[iu, ju],
                                      "cov_pre_eigen": Fpre[iu, ju]}))
        vol, volp = np.sqrt(np.diag(F)), np.sqrt(np.diag(Fpre))
        provisional = t + 1 < fc.provisional_until_days
        risk_rows.extend({"date": dates[t], "factor": names[k], "vol_final": float(vol[k]),
                          "vol_pre_eigen": float(volp[k]), "lambda_F": lam_t, "provisional": provisional}
                         for k in range(K))
    res.cov = pl.concat(cov_rows) if cov_rows else pl.DataFrame()
    res.risk = pl.DataFrame(risk_rows)
    res.eigen = pl.DataFrame(eig_rows)
    cs = np.sqrt(np.where(observed, Phi ** 2, 0.0).sum(1) / np.maximum(observed.sum(1), 1))
    res.vra = pl.DataFrame({"date": dates, "b2": b2, "lambda_F": lam, "cs_vol": cs})
    return res
