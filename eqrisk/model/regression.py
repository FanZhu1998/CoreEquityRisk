"""Daily constrained cross-sectional regression (blueprint §7).

For the return from close t-1 to close t: exposures X_{t-1}, ESTU_{t-1}, regression weights
v_{t-1} (sqrt cap) and industry cap weights w_{t-1}, all fixed at the close of t-1. The
regression sample is the ESTU_{t-1} names with an unflagged return on t. Industries with no
sample names that day are dropped from the design and the constraint, and their factor return is
null. Returns are clipped at median +/- 8 * 1.4826 MAD for the regression input only; specific
returns u = r - X f use the raw return, for every coverage name with a return (out-of-sample
outside the ESTU).
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.kernels import constrained_wls
from eqrisk.kernels.regression import mad_clip, restriction_matrix, wls_diagnostics
from eqrisk.model.exposures import STYLES
from eqrisk.model.panel import Panel

COUNTRY = "COUNTRY"
_TOP_NAMES = 5          # names listed per pure factor portfolio in omega_summary


class ContractError(RuntimeError):
    """A point-in-time contract was broken (e.g. exposures not strictly before returns)."""


def factor_names(industries: list[str]) -> list[str]:
    return [COUNTRY, *industries, *STYLES]


@dataclass
class DayFit:
    date: dt.date                # `date` as a field name shadows the type
    exposure_date: dt.date      # for the fields below it, so both are qualified
    sids: np.ndarray            # regression sample
    X: np.ndarray               # sample design, columns = factors present
    factors: list[str]          # factors present (country, industries present, styles)
    f: np.ndarray
    omega: np.ndarray           # (K_present, n) pure factor portfolios
    r2: float
    cond: float
    cov_f: np.ndarray
    constraint_resid: float
    country_minus_mkt: float
    u_sids: np.ndarray          # every coverage name with a return
    u: np.ndarray
    u_in_estu: np.ndarray


def fit_day(P: Panel, t: int, x_prev: pl.DataFrame, cfg: ModelConfig) -> DayFit | None:
    """One regression; `x_prev` holds the exposures dated P.dates[t-1]. None if too few names."""
    ret_day, exp_day = P.dates[t], x_prev["date"][0]
    if not exp_day < ret_day or exp_day != P.dates[t - 1]:
        raise ContractError(f"exposures dated {exp_day} used for the return ending {ret_day}")
    cols = np.array([P.col[int(s)] for s in x_prev["sid"].to_list()])
    r = P["ret_excess"][t, cols]
    has_r = np.isfinite(r) & ~P["price_flag"][t, cols]
    estu = P["in_estu"][t - 1, cols]
    v = P["v_reg"][t - 1, cols]
    mcap = P["mcap"][t - 1, cols]
    sample = estu & has_r & np.isfinite(v) & (mcap > 0)
    if sample.sum() < cfg.regression.min_names:
        return None
    ind_names = x_prev["industry"].to_list()
    present = sorted({ind_names[i] for i in np.flatnonzero(sample)})
    code = {name: i for i, name in enumerate(present)}
    n = len(cols)
    D = np.zeros((n, len(present)))
    for i, name in enumerate(ind_names):
        if name in code:
            D[i, code[name]] = 1.0
    S = x_prev.select(list(STYLES)).to_numpy()
    X = np.column_stack([np.ones(n), D, S])
    factors = [COUNTRY, *present, *STYLES]
    ind_cols = np.arange(1, 1 + len(present))
    cap = np.where(sample, mcap, 0.0)
    ind_capw = D[sample].T @ cap[sample] / cap[sample].sum()
    rc = mad_clip(r, sample, cfg.regression.input_clip_mad)
    f, _, omega = constrained_wls(rc[sample], X[sample], v[sample], ind_cols, ind_capw)
    R = restriction_matrix(X.shape[1], ind_cols, ind_capw)
    r2, cond, cov_f = wls_diagnostics(rc[sample], X[sample], v[sample], f, R)
    capw = cap[sample] / cap[sample].sum()
    ok_u = has_r & np.all(np.isfinite(X), axis=1)
    return DayFit(
        date=ret_day, exposure_date=exp_day, sids=P.sids[cols[sample]], X=X[sample], factors=factors, f=f,
        omega=omega, r2=r2, cond=cond, cov_f=cov_f, constraint_resid=float(abs(ind_capw @ f[ind_cols])),
        country_minus_mkt=float(f[0] - capw @ r[sample]),
        u_sids=P.sids[cols[ok_u]], u=(r - X @ f)[ok_u], u_in_estu=estu[ok_u])


@dataclass
class RegressionResult:
    factor_returns: pl.DataFrame     # date, factor, f, t_stat, f_over_sigma
    stats: pl.DataFrame              # date, n, r2_w, cond, constraint_resid, country_minus_mkt, status
    specific: pl.DataFrame           # date, sid, u, in_estu
    omega: pl.DataFrame              # date, factor, gross, net, top


def run_regressions(P: Panel, exposures: pl.DataFrame, cfg: ModelConfig, first: date) -> RegressionResult:
    names = factor_names(P.industries)
    by_date = {d[0]: g for d, g in exposures.group_by("date")}
    alpha = 1.0 - 0.5 ** (1.0 / cfg.factor_cov.vol.half_life)
    ewvar: dict[str, float] = {}
    fr: list[dict[str, Any]] = []
    st: list[dict[str, Any]] = []
    sp: list[pl.DataFrame] = []
    om: list[dict[str, Any]] = []
    for t in range(P.row[first], len(P.dates)):
        day = P.dates[t]
        x_prev = by_date.get(P.dates[t - 1])
        fit = fit_day(P, t, x_prev, cfg) if x_prev is not None else None
        if fit is None:
            st.append({"date": day, "n": 0, "status": "no_exposures" if x_prev is None else "too_few_names"})
            continue
        se = np.sqrt(np.clip(np.diag(fit.cov_f), 0.0, None))
        present = dict(zip(fit.factors, range(len(fit.factors)), strict=True))
        for k in names:
            if k not in present:
                fr.append({"date": day, "factor": k, "f": None, "t_stat": None, "f_over_sigma": None})
                continue
            i = present[k]
            fk = float(fit.f[i])
            prior = ewvar.get(k)
            fr.append({"date": day, "factor": k, "f": fk, "t_stat": fk / se[i] if se[i] > 0 else None,
                       "f_over_sigma": fk / math.sqrt(prior) if prior else None})
            ewvar[k] = fk * fk if prior is None else alpha * fk * fk + (1 - alpha) * prior
            row = fit.omega[i]
            top = np.argsort(-np.abs(row))[:_TOP_NAMES]
            om.append({"date": day, "factor": k, "gross": float(np.abs(row).sum()), "net": float(row.sum()),
                       "top": json.dumps([[int(fit.sids[j]), round(float(row[j]), 6)] for j in top])})
        st.append({"date": day, "n": len(fit.sids), "r2_w": fit.r2, "cond": fit.cond,
                   "constraint_resid": fit.constraint_resid, "country_minus_mkt": fit.country_minus_mkt,
                   "status": "ok"})
        sp.append(pl.DataFrame({"date": [day] * len(fit.u), "sid": fit.u_sids, "u": fit.u, "in_estu": fit.u_in_estu}))
    return RegressionResult(
        factor_returns=pl.DataFrame(fr, schema={"date": pl.Date, "factor": pl.String, "f": pl.Float64,
                                                "t_stat": pl.Float64, "f_over_sigma": pl.Float64}),
        stats=pl.DataFrame(st, schema={"date": pl.Date, "n": pl.Int64, "r2_w": pl.Float64, "cond": pl.Float64,
                                       "constraint_resid": pl.Float64, "country_minus_mkt": pl.Float64,
                                       "status": pl.String}),
        specific=pl.concat(sp) if sp else pl.DataFrame(schema={"date": pl.Date, "sid": pl.Int64, "u": pl.Float64,
                                                               "in_estu": pl.Boolean}),
        omega=pl.DataFrame(om, schema={"date": pl.Date, "factor": pl.String, "gross": pl.Float64,
                                       "net": pl.Float64, "top": pl.String}))


def french_correlations(factor_returns: pl.DataFrame, french: pl.DataFrame) -> dict[str, float]:
    """Daily correlations with Ken French factors (§11.3): validation only, never an input."""
    wide = factor_returns.pivot(on="factor", index="date", values="f").join(french, on="date", how="inner")
    pairs = {"COUNTRY~Mkt-RF": ("COUNTRY", "mkt_rf"), "SIZE~SMB": ("SIZE", "smb"),
             "BOOK_TO_PRICE~HML": ("BOOK_TO_PRICE", "hml"), "MOMENTUM~Mom": ("MOMENTUM", "mom"),
             "EARNINGS_YIELD~HML": ("EARNINGS_YIELD", "hml")}
    out = {}
    for label, (a, b) in pairs.items():
        if a in wide.columns and b in wide.columns:
            d = wide.select(a, b).drop_nulls()
            out[label] = float(np.corrcoef(d[a], d[b])[0, 1]) if d.height > 2 else float("nan")
    return out
