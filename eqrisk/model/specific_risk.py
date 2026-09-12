"""Specific risk in five layers (blueprint §9). Every sigma column is a monthly volatility.

Layer 1  time series: 21 x C_NW x EWMA_84(u^2) over each name's last 252 valid specific returns,
         C_NW = V_NW / C_0 at half-life 252 with 5 lags, bounded to [0.25, 4]; floored at 1%/month
Layer 2  structural: sqrt(cap)-weighted regression of ln sigma_TS on industry dummies and styles,
         fitted on names with gamma >= 0.99, E0 = the weighted smearing estimator
Layer 3  blend: gamma sigma_TS + (1 - gamma) sigma_STR, gamma CNE5-style
Layer 4  Bayesian shrinkage toward the cap-weighted mean of each market-cap decile (q = 0.1)
Layer 5  regime: lambda_S^2 = EWMA of the cap-weighted ESTU mean of (u_t / sigma_{t-1})^2, the
         one-day forecast from t-1 with the NW adjustment removed; sigma = lambda_S sigma_SH
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.kernels import bayes_shrink, specific_bias_sq
from eqrisk.kernels.specific import coverage_gamma_panel, specific_ts_panel
from eqrisk.model.exposures import STYLES
from eqrisk.model.panel import Panel

SPECIFIC_COLUMNS = ("sigma_ts", "c_nw", "gamma", "sigma_str", "sigma_blend", "sigma_sh", "lambda_S", "sigma_final",
                    "size_decile")


def structural_sigma(sig_ts: np.ndarray, gamma: np.ndarray, S: np.ndarray, industry: np.ndarray, n_ind: int,
                     mcap: np.ndarray, cov: np.ndarray, cfg: ModelConfig) -> np.ndarray:
    """Layer 2 prediction for every coverage name with exposures (NaN where it cannot be fitted)."""
    st = cfg.specific_risk.structural
    with np.errstate(invalid="ignore"):
        w = np.sqrt(np.where(mcap > 0, mcap, np.nan))
    rows_ok = np.all(np.isfinite(S), axis=1) & (industry >= 0)
    fit = cov & rows_ok & (gamma >= st.gamma_fit_threshold) & np.isfinite(sig_ts) & (sig_ts > 0) & np.isfinite(w)
    out = np.full(len(sig_ts), np.nan)
    if fit.sum() < S.shape[1] + 2:
        return out
    y = np.log(sig_ts)
    use_ind = fit.sum() >= st.min_fit_names
    present = np.unique(industry[fit]) if use_ind else np.array([], dtype=int)
    D = (industry[:, None] == present[None, :]).astype(float) if use_ind else np.ones((len(y), 1))
    A = np.column_stack([D, S])
    sw = np.sqrt(w[fit])
    b, *_ = np.linalg.lstsq(A[fit] * sw[:, None], y[fit] * sw, rcond=None)
    eps = y[fit] - A[fit] @ b
    e0 = float((w[fit] * np.exp(eps)).sum() / w[fit].sum())
    pred = A @ b
    if use_ind:
        # an industry with no fit names today takes the fit-weighted average industry level
        counts = (D[fit] * w[fit][:, None]).sum(axis=0)
        level = float(counts @ b[:len(present)] / counts.sum())
        orphan = rows_ok & (D.sum(axis=1) == 0)
        pred[orphan] = level + S[orphan] @ b[len(present):]
    out[rows_ok] = e0 * np.exp(pred[rows_ok])
    return out


def size_deciles(mcap: np.ndarray, mask: np.ndarray, n_groups: int) -> np.ndarray:
    """0 (smallest) .. n_groups-1 by market cap among `mask`; -1 elsewhere."""
    out = np.full(len(mcap), -1, dtype=np.int64)
    m = mask & np.isfinite(mcap) & (mcap > 0)
    if m.sum() >= n_groups:
        edges = np.quantile(mcap[m], np.linspace(0, 1, n_groups + 1)[1:-1])
        out[m] = np.searchsorted(edges, mcap[m], side="right")
    return out


@dataclass
class SpecificRiskResult:
    table: pl.DataFrame          # date, sid, and SPECIFIC_COLUMNS
    vra: pl.DataFrame            # date, b2, lambda_S
    final: np.ndarray            # (T, N) monthly sigma_final, NaN outside forecasts
    ts_only: np.ndarray          # (T, N) monthly sigma_TS
    decile: np.ndarray           # (T, N) size decile


def run_specific_risk(P: Panel, U: np.ndarray, exposures: dict[date, pl.DataFrame], cfg: ModelConfig,
                      first_return: date) -> SpecificRiskResult:
    sr, h = cfg.specific_risk, cfg.horizon_days
    T, N = P.shape
    start = P.row[first_return] + sr.ts.window - 1
    alpha = 1.0 - 0.5 ** (1.0 / sr.vra.half_life)
    state: float | None = None
    prev_daily = np.full(N, np.nan)
    final, ts_only = np.full((T, N), np.nan), np.full((T, N), np.nan)
    deciles = np.full((T, N), -1, dtype=np.int64)
    frames: list[pl.DataFrame] = []
    vra_rows: list[dict[str, Any]] = []
    code = {ind: i for i, ind in enumerate(P.industries)}
    for t in range(start, T):
        cov, mcap = P["in_cov"][t], P["mcap"][t]
        b2 = float("nan")
        if t > start:
            est = P["in_estu"][t - 1] & np.isfinite(U[t]) & np.isfinite(prev_daily) & (prev_daily > 0)
            if est.any():
                cw = np.nan_to_num(P["capw"][t - 1][est])
                if cw.sum() > 0:
                    b2 = specific_bias_sq(U[t, est], prev_daily[est], cw, sr.vra.z_cap)
                    state = b2 if state is None else alpha * b2 + (1.0 - alpha) * state
        lam = math.sqrt(state) if state is not None else 1.0
        vra_rows.append({"date": P.dates[t], "b2": b2, "lambda_S": lam})

        v0, ratio, cnt = specific_ts_panel(U[max(0, t + 1 - sr.ts.lookback_days): t + 1], sr.ts.window,
                                           sr.ts.half_life, sr.ts.nw_lags, sr.ts.nw_half_life)
        cnw = np.clip(ratio, *sr.ts.c_nw_bounds)
        with np.errstate(invalid="ignore"):
            sig_ts = np.where(cnt >= 2, np.maximum(np.sqrt(h * v0 * cnw), sr.ts.min_sigma_monthly), np.nan)
        gam = coverage_gamma_panel(U[max(0, t + 1 - sr.ts.window): t + 1], sr.gamma.h_min, sr.gamma.h_ramp)

        S = np.full((N, len(STYLES)), np.nan)
        ind = np.full(N, -1, dtype=np.int64)
        ex = exposures.get(P.dates[t])
        if ex is not None:
            cols = np.array([P.col[int(s)] for s in ex["sid"].to_list()])
            S[cols] = ex.select(list(STYLES)).to_numpy()
            ind[cols] = np.array([code.get(x, -1) if x is not None else -1 for x in ex["industry"].to_list()])
        sig_str = structural_sigma(sig_ts, gam, S, ind, len(P.industries), mcap, cov, cfg)
        blend = np.where(np.isfinite(sig_ts) & np.isfinite(sig_str), gam * sig_ts + (1 - gam) * sig_str,
                         np.where(np.isfinite(sig_str), sig_str, sig_ts))
        dec = size_deciles(mcap, cov & np.isfinite(blend), sr.shrinkage.n_groups)
        sh = blend.copy()
        m = dec >= 0
        if m.any():
            sh[m] = bayes_shrink(blend[m], mcap[m], dec[m], sr.shrinkage.q)
        fin = lam * sh
        final[t], ts_only[t], deciles[t] = np.where(cov, fin, np.nan), np.where(cov, sig_ts, np.nan), dec
        with np.errstate(invalid="ignore"):
            prev_daily = sh / np.sqrt(h * np.where(np.isfinite(cnw), cnw, 1.0))
        keep = cov & np.isfinite(fin)
        frames.append(pl.DataFrame({
            "date": [P.dates[t]] * int(keep.sum()), "sid": P.sids[keep], "sigma_ts": sig_ts[keep],
            "c_nw": cnw[keep], "gamma": gam[keep], "sigma_str": sig_str[keep], "sigma_blend": blend[keep],
            "sigma_sh": sh[keep], "lambda_S": np.full(int(keep.sum()), lam), "sigma_final": fin[keep],
            "size_decile": dec[keep]}))
    table = pl.concat(frames) if frames else pl.DataFrame()
    return SpecificRiskResult(table, pl.DataFrame(vra_rows), final, ts_only, deciles)
