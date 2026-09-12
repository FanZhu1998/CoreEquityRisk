"""Strictly point-in-time model backtest and the §1.3 scorecard (blueprint §11.2, §11.3, §1.3).

A forecast made at the close of t (X_t, F_t, Delta_t) is scored against the excess return summed
over the next `horizon` sessions, standardized by the forecast volatility: b = R / sigma. Forecast
dates are `horizon` sessions apart, so the b do not overlap. Families (§11.2):
(a) pure factor portfolios; (b) eigenfactor portfolios before and after Layer 3; (c) minimum-risk
factor portfolios for fixed random alphas, optimized and scored with each layer's matrix [USE4
Figs. 4.2, 4.5]; (d) the estimation universe, cap- and equal-weighted; (e) cap-weighted industry
portfolios; (f) random equal-weighted long-only portfolios, redrawn each date; (g) specific
returns by size and forecast-volatility decile, full stack vs time series only.

Inside a window a missing daily return counts as zero (the position is held as cash). Family (c)
uses, on each date, the factors estimated throughout its window.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig, Project, ValidationCriteriaCfg
from eqrisk.kernels import bias_statistic, mrad, qlike
from eqrisk.model.exposures import STYLES
from eqrisk.model.factor_cov import factor_panel
from eqrisk.model.panel import Panel, pivot
from eqrisk.model.regression import COUNTRY, french_correlations
from eqrisk.model.tables import factor_order, load_panel
from eqrisk.staging.rawio import read_reference
from eqrisk.store import read_table
from eqrisk.validation.bias import eigen_bias_battery, forecast_dates, realized, specific_bias

PASS, FAIL, NOT_RUN, INFO = "PASS", "FAIL", "NOT RUN", "INFO"
SUMMARY_SCHEMA: dict[str, Any] = {"portfolio": pl.String, "n": pl.Int64, "bias": pl.Float64, "band": pl.Float64,
                                  "inside": pl.Boolean, "mrad": pl.Float64, "qlike": pl.Float64}


def _rng(model_id: str, label: str, d: date | None = None) -> np.random.Generator:
    """Deterministic generator per (model, purpose, date), like the eigen simulation seed (D-004)."""
    key = f"{model_id}|validation|{label}|{d.isoformat() if d else '-'}"
    return np.random.default_rng(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))


@dataclass
class Inputs:
    names: list[str]                          # factor order: COUNTRY, industries, styles
    fdates: list[date]                        # sessions with factor returns
    Phi: np.ndarray                           # (T_f, K) daily factor returns
    observed: np.ndarray                      # (T_f, K)
    F: dict[str, dict[date, np.ndarray]]      # "pre" (layers 1-2), "post" (1-3), "final" (1-4)
    P: Panel
    U: np.ndarray                             # (T, N) specific returns on the panel's sessions
    sigma: dict[str, np.ndarray]              # "full", "ts": (T, N) monthly specific volatility
    size_decile: np.ndarray                   # (T, N), -1 where absent
    specific: pl.DataFrame                    # date, sid, sigma_final
    exposures: pl.DataFrame                   # date, sid, industry, styles
    stats: pl.DataFrame                       # regression_stats
    factor_returns: pl.DataFrame
    vra: pl.DataFrame                         # date, lambda_F, lambda_S
    french: pl.DataFrame | None


@dataclass
class ValidationResult:
    model_id: str
    config_hash: str
    start: date
    end: date
    horizon: int
    periods: int
    scorecard: list[dict[str, str]]
    external: list[dict[str, str]]
    factor: pl.DataFrame
    eigen: pl.DataFrame
    random_alpha: dict[str, pl.DataFrame]
    market: pl.DataFrame
    industry: pl.DataFrame
    random: pl.DataFrame
    specific: dict[str, dict[str, Any]]
    rolling: dict[str, tuple[list[date], np.ndarray]]
    vra: pl.DataFrame
    metrics: dict[str, Any]


def _matrices(cov: pl.DataFrame, names: list[str], col: str) -> dict[date, np.ndarray]:
    pos = {n: i for i, n in enumerate(names)}
    out: dict[date, np.ndarray] = {}
    for (d,), g in cov.group_by("date"):
        i = g["factor_i"].replace_strict(pos).to_numpy()
        j = g["factor_j"].replace_strict(pos).to_numpy()
        M = np.zeros((len(names), len(names)))
        M[i, j] = g[col].to_numpy()
        M[j, i] = g[col].to_numpy()
        assert isinstance(d, date)
        out[d] = M
    return out


def load_inputs(project: Project) -> Inputs:
    m = project.model_dir
    fr = read_table(m, "factor_returns")
    names = factor_order(fr)
    fdates, Phi, observed = factor_panel(fr, names)
    cov = read_table(m, "factor_cov")
    final, pre = _matrices(cov, names, "cov_final"), _matrices(cov, names, "cov_pre_eigen")
    vra_f = read_table(m, "vra_factor").select("date", "lambda_F")
    lam = dict(vra_f.iter_rows())
    P, _ = load_panel(project)
    sr = read_table(m, "specific_risk")
    try:
        french: pl.DataFrame | None = read_reference(project.raw_dir, "famafrench", "daily")
    except FileNotFoundError:
        french = None
    vra = vra_f.join(read_table(m, "vra_specific").select("date", "lambda_S"), on="date", how="full",
                     coalesce=True).sort("date")
    return Inputs(
        names=names, fdates=fdates, Phi=Phi, observed=observed,
        F={"pre": pre, "post": {d: M / lam[d] ** 2 for d, M in final.items() if d in lam}, "final": final},
        P=P, U=pivot(read_table(m, "specific_returns"), "u", P.dates, P.sids),
        sigma={"full": pivot(sr, "sigma_final", P.dates, P.sids), "ts": pivot(sr, "sigma_ts", P.dates, P.sids)},
        size_decile=pivot(sr, "size_decile", P.dates, P.sids, fill=-1, dtype=np.int64),
        specific=sr.select("date", "sid", "sigma_final"),
        exposures=read_table(m, "exposures").select("date", "sid", "industry", *STYLES),
        stats=read_table(m, "regression_stats"), factor_returns=fr, vra=vra, french=french)


# ---- statistics ---------------------------------------------------------------------------------

def summarize(b: np.ndarray, labels: list[str], window: int) -> pl.DataFrame:
    """Per portfolio: periods, bias statistic, its 95% band sqrt(2/T), inside?, MRAD, QLIKE."""
    rows = []
    for k, label in enumerate(labels):
        x = b[:, k][np.isfinite(b[:, k])]
        if len(x) < 3:
            continue
        bias, band = bias_statistic(x, np.ones_like(x)), float(np.sqrt(2.0 / len(x)))
        rows.append({"portfolio": label, "n": len(x), "bias": bias, "band": band, "inside": abs(bias - 1.0) <= band,
                     "mrad": mrad(x[:, None], window) if len(x) > window else None,
                     "qlike": qlike(x, np.ones_like(x))})
    return pl.DataFrame(rows, schema=SUMMARY_SCHEMA)


def rolling_bias(x: np.ndarray, window: int) -> np.ndarray:
    """Bias statistic over each trailing `window` periods (NaN until enough finite periods)."""
    out = np.full(len(x), np.nan)
    for t in range(window, len(x) + 1):
        seg = x[t - window: t]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 3:
            out[t - 1] = float(np.std(seg, ddof=1))
    return out


def portfolio_sigma(X: np.ndarray, F: np.ndarray, spec_var: np.ndarray, H: np.ndarray) -> np.ndarray:
    """Forecast volatility of each column of holdings H (N, P) under X F X' + diag(spec_var)."""
    XH = X.T @ H
    var = np.einsum("kp,kp->p", XH, F @ XH) + (H * H * spec_var[:, None]).sum(axis=0)
    out: np.ndarray = np.sqrt(np.maximum(var, 0.0))
    return out


def pure_factor_b(dates: list[date], Phi: np.ndarray, observed: np.ndarray, F: dict[date, np.ndarray],
                  ts: list[int], horizon: int) -> np.ndarray:
    """(a) Standardized returns of the pure factor portfolios (rows of Omega)."""
    b = np.full((len(ts), Phi.shape[1]), np.nan)
    for i, t in enumerate(ts):
        ok = observed[t + 1: t + 1 + horizon].all(axis=0)
        vol = np.sqrt(np.diag(F[dates[t]]))
        with np.errstate(invalid="ignore", divide="ignore"):
            b[i] = np.where(ok & (vol > 0), realized(Phi, t, horizon) / vol, np.nan)
    return b


def min_risk_factor_b(dates: list[date], Phi: np.ndarray, observed: np.ndarray, F: dict[date, np.ndarray],
                      ts: list[int], horizon: int, alphas: np.ndarray) -> np.ndarray:
    """(c) Minimum-risk factor portfolios w = M^-1 alpha, one per column of `alphas` (K, P), built and
    scored with the same matrix M. A matrix that underestimates the risk of its small eigen-directions
    is exploited by the optimizer and shows bias > 1 [USE4 §4.2]."""
    b = np.full((len(ts), alphas.shape[1]), np.nan)
    for i, t in enumerate(ts):
        M = F.get(dates[t])
        if M is None:
            continue
        ok = observed[t + 1: t + 1 + horizon].all(axis=0)
        Ms = M[np.ix_(ok, ok)]
        W = np.linalg.solve(Ms, alphas[ok])
        sig = np.sqrt(np.einsum("kp,kp->p", W, Ms @ W))
        b[i] = (W.T @ realized(Phi, t, horizon)[ok]) / sig
    return b


def asset_family_b(inp: Inputs, ts: list[int], horizon: int,
                   cfg: ModelConfig) -> dict[str, tuple[np.ndarray, list[str]]]:
    """(d) ESTU cap- and equal-weighted, (e) cap-weighted industry, (f) random long-only portfolios."""
    P, v = inp.P, cfg.validation
    pos = {n: i for i, n in enumerate(inp.names)}
    inds = [n for n in inp.names if n != COUNTRY and n not in STYLES]
    style_idx = np.array([pos[s] for s in STYLES])
    K, m = len(inp.names), v.random_portfolio_names
    wanted = [P.dates[t] for t in ts]
    by_date = {k[0]: g for k, g in inp.exposures.filter(pl.col("date").is_in(wanted)).group_by("date")}
    b = {"market": np.full((len(ts), 2), np.nan), "industry": np.full((len(ts), len(inds)), np.nan),
         "random": np.full((len(ts), v.random_portfolios), np.nan)}
    for i, t in enumerate(ts):
        d = P.dates[t]
        g, F = by_date.get(d), inp.F["final"].get(d)
        if g is None or F is None:
            continue
        cols = np.array([P.col[int(s)] for s in g["sid"].to_list()])
        styles = g.select(list(STYLES)).to_numpy()
        ind = np.array([pos.get(x, -1) if x is not None else -1 for x in g["industry"].to_list()])
        spec = inp.sigma["full"][t, cols] ** 2
        ok = np.isfinite(spec) & np.isfinite(styles).all(axis=1) & (ind >= 0)
        cols, styles, ind, spec = cols[ok], styles[ok], ind[ok], spec[ok]
        N = len(cols)
        X = np.zeros((N, K))
        X[:, 0] = 1.0
        X[np.arange(N), ind] = 1.0
        X[:, style_idx] = styles
        R = np.nansum(P["ret_excess"][t + 1: t + 1 + horizon, cols], axis=0)
        estu = P["in_estu"][t, cols]
        capw = np.where(estu, np.nan_to_num(P["capw"][t, cols]), 0.0)
        H_m = np.column_stack([capw / capw.sum(), estu / estu.sum()])
        H_i = np.zeros((N, len(inds)))
        for k, name in enumerate(inds):
            wk = np.where(ind == pos[name], capw, 0.0)
            if wk.sum() > 0:
                H_i[:, k] = wk / wk.sum()
        H_r = np.zeros((N, v.random_portfolios))
        pool = np.flatnonzero(estu)
        if len(pool) >= m:
            rng = _rng(cfg.model_id, "random", d)
            for p in range(v.random_portfolios):
                H_r[rng.choice(pool, size=m, replace=False), p] = 1.0 / m
        for fam, H in (("market", H_m), ("industry", H_i), ("random", H_r)):
            sig = portfolio_sigma(X, F, spec, H)
            with np.errstate(invalid="ignore", divide="ignore"):
                b[fam][i] = np.where(sig > 0, (H.T @ R) / sig, np.nan)
    return {"market": (b["market"], ["ESTU cap-weighted", "ESTU equal-weighted"]), "industry": (b["industry"], inds),
            "random": (b["random"], [f"R{p:03d}" for p in range(v.random_portfolios)])}


def vol_deciles(sigma: np.ndarray, estu: np.ndarray, n: int) -> np.ndarray:
    """Group 0..n-1 of each ESTU name's forecast volatility on each date, -1 elsewhere."""
    g = np.full(sigma.shape, -1, dtype=np.int64)
    for t in range(sigma.shape[0]):
        m = estu[t] & np.isfinite(sigma[t])
        k = int(m.sum())
        if k >= n:
            g[t, np.flatnonzero(m)] = sigma[t][m].argsort().argsort() * n // k
    return g


def coverage_complete(inp: Inputs, start: date, end: date) -> tuple[float, date | None]:
    """Worst day's share of coverage names with complete exposures and a finite, positive sigma."""
    complete = pl.all_horizontal([pl.col(s).is_not_null() & pl.col(s).is_finite() for s in STYLES])
    window = pl.col("date").is_between(start, end)
    ex = inp.exposures.filter(window & complete & pl.col("industry").is_not_null()).select("date", "sid")
    sr = inp.specific.filter(window & pl.col("sigma_final").is_finite() & (pl.col("sigma_final") > 0))
    per = ex.join(sr.select("date", "sid"), on=["date", "sid"]).group_by("date").len()
    days = set(inp.specific.filter(window)["date"].unique().to_list())
    cov = pl.DataFrame({"date": inp.P.dates, "cov": inp.P["in_cov"].sum(axis=1)}).filter(pl.col("date").is_in(days))
    if cov.height == 0:
        return float("nan"), None
    j = cov.join(per, on="date", how="left").with_columns(frac=pl.col("len").fill_null(0) / pl.col("cov"))
    worst = j.sort("frac", "date").row(0, named=True)
    return float(worst["frac"]), worst["date"]


def regression_metrics(inp: Inputs, start: date, end: date) -> dict[str, float]:
    st = inp.stats.filter((pl.col("status") == "ok") & pl.col("date").is_between(start, end))
    fc = inp.factor_returns.filter(pl.col("factor") == COUNTRY).select("date", "f")
    mk = (st.select("date", "country_minus_mkt").join(fc, on="date")
          .with_columns(mkt=pl.col("f") - pl.col("country_minus_mkt")))
    resid = st["constraint_resid"].abs().max()
    return {"constraint_resid_max": float(resid) if isinstance(resid, (int, float)) else float("nan"),
            "country_corr": float(np.corrcoef(mk["f"].to_numpy(), mk["mkt"].to_numpy())[0, 1]),
            "regression_days": float(st.height)}


# ---- scorecard ----------------------------------------------------------------------------------

def _row(area: str, criterion: str, value: str, ok: bool | None) -> dict[str, str]:
    return {"area": area, "criterion": criterion, "value": value,
            "status": NOT_RUN if ok is None else (PASS if ok else FAIL)}


def scorecard(m: dict[str, Any], c: ValidationCriteriaCfg, rolling: int) -> list[dict[str, str]]:
    """One row per §1.3 criterion: PASS, FAIL, or NOT RUN when nothing measures it yet."""
    ops = m["operations"]
    rows = [
        _row("Operations", f"incremental daily run < {c.daily_run_minutes_max:g} min", ops["fast_text"], ops["fast"]),
        _row("Operations", f"unattended for {c.unattended_sessions_min} consecutive sessions", ops["streak_text"],
             ops["unattended"]),
        _row("Operations", "reruns are bit-identical", ops["rerun_text"], ops["rerun"]),
        _row("Coverage", f">= {c.coverage_min:.0%} of coverage with complete exposures and specific risk, every day",
             f"worst {m['coverage_min']:.2%} on {m['coverage_min_date']}", m["coverage_min"] >= c.coverage_min),
        _row("Regression", f"constraint residual < {c.constraint_resid_max:.0e}",
             f"max {m['constraint_resid_max']:.1e}", m["constraint_resid_max"] < c.constraint_resid_max),
        _row("Regression", f"corr(country factor, cap-weighted ESTU excess return) >= {c.country_corr_min}",
             f"{m['country_corr']:.4f}", m["country_corr"] >= c.country_corr_min),
        _row("Factor risk", "every F_t symmetric positive definite", f"min eigenvalue {m['min_eigenvalue']:.2e}",
             m["min_eigenvalue"] > 0),
    ]
    lo, hi = c.factor_bias_mean
    rows.append(_row("Factor risk", f"mean per-factor bias statistic in [{lo}, {hi}]", f"{m['factor_bias_mean']:.3f}",
                     lo <= m["factor_bias_mean"] <= hi))
    rows.append(_row("Factor risk", "eigenfactor smile flattened by Layer 3",
                     f"mean |b - 1| {m['eigen_dev_before']:.3f} -> {m['eigen_dev_after']:.3f}",
                     m["eigen_dev_after"] < m["eigen_dev_before"]))
    lo, hi = c.specific_bias
    rows.append(_row("Specific risk", f"cap-weighted bias in [{lo}, {hi}]", f"{m['specific_bias']:.3f}",
                     lo <= m["specific_bias"] <= hi))
    lo, hi = c.specific_decile_bias
    dec = m["specific_size_deciles"]
    rows.append(_row("Specific risk", f"each size decile in [{lo}, {hi}]", f"{min(dec):.3f} to {max(dec):.3f}",
                     all(lo <= x <= hi for x in dec)))
    per = ", ".join(f"{k} {x:.3f}" for k, x in m["mrad_by_family"].items())
    rows.append(_row("Test portfolios", f"{rolling}-period MRAD <= {c.mrad_max} (0.17 ideal under normality)",
                     f"{m['mrad_mean']:.3f} ({per})", m["mrad_mean"] <= c.mrad_max))
    return rows


def external_checks(fr: pl.DataFrame, french: pl.DataFrame | None, vra: pl.DataFrame,
                    c: ValidationCriteriaCfg) -> list[dict[str, str]]:
    """§11.3: Ken French correlations and the volatility-regime multipliers."""
    rows: list[dict[str, str]] = []
    if french is not None:
        corr = french_correlations(fr, french)
        rules: dict[str, tuple[str, Callable[[float], bool]]] = {
            "COUNTRY~Mkt-RF": (f">= {c.french_country_corr_min}", lambda x: x >= c.french_country_corr_min),
            "SIZE~SMB": ("negative (SIZE is long large caps)", lambda x: x < 0),
            "BOOK_TO_PRICE~HML": ("positive", lambda x: x > 0), "MOMENTUM~Mom": ("positive", lambda x: x > 0)}
        for key, x in corr.items():
            if key in rules:
                text, rule = rules[key]
                rows.append(_row("Ken French", f"{key} {text}", f"{x:.3f}", rule(x)))
            else:
                rows.append({"area": "Ken French", "criterion": f"{key} (informational)", "value": f"{x:.3f}",
                             "status": INFO})
    for col, label in (("lambda_F", "factor VRA lambda_F"), ("lambda_S", "specific VRA lambda_S")):
        s = vra.drop_nulls(col)
        if s.height:
            hi, lo = s.sort(col, descending=True).row(0, named=True), s.sort(col).row(0, named=True)
            rows.append({"area": "Volatility regime", "criterion": f"{label}: peak and trough",
                         "value": f"{hi[col]:.2f} on {hi['date']}; {lo[col]:.2f} on {lo['date']}", "status": INFO})
    return rows


# ---- the battery --------------------------------------------------------------------------------

def compute_validation(inp: Inputs, cfg: ModelConfig, config_hash: str, start: date, end: date,
                       operations: dict[str, Any]) -> ValidationResult:
    v, h, P, w = cfg.validation, cfg.horizon_days, inp.P, cfg.validation.rolling_periods
    avail = set(inp.F["final"]) & set(inp.specific["date"].unique().to_list())
    ts_f = forecast_dates(inp.fdates, avail, h, start, end)
    ts_p = forecast_dates(P.dates, avail, h, start, end)
    if len(ts_p) < 3:
        raise ValueError(f"only {len(ts_p)} forecast periods between {start} and {end}")

    b_a = pure_factor_b(inp.fdates, inp.Phi, inp.observed, inp.F["final"], ts_f, h)
    factor = summarize(b_a, inp.names, w)
    eigen = eigen_bias_battery(inp.fdates, inp.Phi, {d: inp.F["pre"][d] for d in avail},
                               {d: inp.F["post"][d] for d in avail if d in inp.F["post"]}, h, start, end)
    alphas = _rng(cfg.model_id, "alpha").standard_normal((len(inp.names), v.random_alpha_portfolios))
    labels = [f"A{p:03d}" for p in range(v.random_alpha_portfolios)]
    random_alpha = {layer: summarize(min_risk_factor_b(inp.fdates, inp.Phi, inp.observed, inp.F[layer], ts_f, h,
                                                       alphas), labels, w) for layer in ("pre", "post", "final")}
    fam = asset_family_b(inp, ts_p, h, cfg)
    market, industry, random = (summarize(*fam[k], w) for k in ("market", "industry", "random"))

    n_groups = cfg.specific_risk.shrinkage.n_groups
    specific: dict[str, dict[str, Any]] = {}
    for model in ("full", "ts"):
        sig = inp.sigma[model]
        size = specific_bias(P.dates, inp.U, sig, P["capw"], P["in_estu"], inp.size_decile, h, start, end)
        vol = specific_bias(P.dates, inp.U, sig, P["capw"], P["in_estu"], vol_deciles(sig, P["in_estu"], n_groups),
                            h, start, end)
        specific[model] = {"overall": size["overall"], "size": size["by_decile"], "vol": vol["by_decile"],
                           "n": size["n"], "periods": size["dates"]}

    fdates_ts, pdates_ts = [inp.fdates[t] for t in ts_f], [P.dates[t] for t in ts_p]
    with warnings.catch_warnings():                          # the first window-1 periods have no rolling bias
        warnings.simplefilter("ignore", RuntimeWarning)
        mean_roll = np.nanmean(np.column_stack([rolling_bias(b_a[:, k], w) for k in range(b_a.shape[1])]), axis=1)
    rolling = {"COUNTRY": (fdates_ts, rolling_bias(b_a[:, 0], w)), "factors (mean)": (fdates_ts, mean_roll),
               "ESTU cap-weighted": (pdates_ts, rolling_bias(fam["market"][0][:, 0], w)),
               "ESTU equal-weighted": (pdates_ts, rolling_bias(fam["market"][0][:, 1], w))}

    cov_min, cov_date = coverage_complete(inp, start, end)
    mrad_by = {name: float(df["mrad"].drop_nulls().mean() or float("nan"))  # type: ignore[arg-type]
               for name, df in (("(c)", random_alpha["final"]), ("(d)", market), ("(e)", industry), ("(f)", random))}
    metrics: dict[str, Any] = {
        "periods": len(ts_p), "operations": operations, "coverage_min": cov_min, "coverage_min_date": cov_date,
        **regression_metrics(inp, start, end),
        "min_eigenvalue": min(float(np.linalg.eigvalsh(M).min()) for d, M in inp.F["final"].items()
                              if start <= d <= end),
        "factor_bias_mean": float(factor["bias"].mean()),  # type: ignore[arg-type]
        "eigen_dev_before": float(np.mean(np.abs(eigen["bias_before"].to_numpy() - 1))),
        "eigen_dev_after": float(np.mean(np.abs(eigen["bias_after"].to_numpy() - 1))),
        "specific_bias": float(specific["full"]["overall"]),
        "specific_size_deciles": [float(x) for x in specific["full"]["size"].values()],
        "mrad_by_family": mrad_by, "mrad_mean": float(np.nanmean(list(mrad_by.values()))),
    }
    fr_window = inp.factor_returns.filter(pl.col("date").is_between(start, end))
    return ValidationResult(
        model_id=cfg.model_id, config_hash=config_hash, start=start, end=end, horizon=h, periods=len(ts_p),
        scorecard=scorecard(metrics, v.criteria, w),
        external=external_checks(fr_window, inp.french, inp.vra.filter(pl.col("date").is_between(start, end)),
                                 v.criteria),
        factor=factor, eigen=eigen, random_alpha=random_alpha, market=market, industry=industry, random=random,
        specific=specific, rolling=rolling, vra=inp.vra.filter(pl.col("date").is_between(start, end)),
        metrics=metrics)
