"""Model-stage orchestration (blueprint §13.2 step 5, §13.4): staged tables -> model tables.

Phase 4: descriptors and exposures for every session from the one before `history.model_start`.
Later phases append the regression, the factor covariance and specific risk.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.calendar import get_calendar
from eqrisk.config import Project
from eqrisk.frames import as_float, as_int
from eqrisk.log import get_logger
from eqrisk.model.descriptors import DESCRIPTORS, compute_descriptors
from eqrisk.model.exposures import FUNDAMENTAL_STYLES, STYLES, build_exposures
from eqrisk.model.factor_cov import FactorCovResult, factor_panel, run_factor_cov
from eqrisk.model.panel import Panel, pivot
from eqrisk.model.regression import RegressionResult, french_correlations, run_regressions
from eqrisk.model.specific_risk import SpecificRiskResult, run_specific_risk
from eqrisk.model.tables import factor_order, load_panel
from eqrisk.staging.rawio import read_reference
from eqrisk.store import read_table, refresh_catalog, replace_table, upsert_dates
from eqrisk.validation.bias import eigen_bias_battery, factor_bias, specific_bias

log = get_logger(__name__)


def descriptor_frame(P: Panel, desc: dict[str, np.ndarray], t0: int) -> pl.DataFrame:
    rr, cc = np.nonzero(P["in_cov"][t0:])
    rr = rr + t0
    cols: dict[str, Any] = {"date": [P.dates[i] for i in rr], "sid": P.sids[cc]}
    for name in DESCRIPTORS:
        cols[name] = desc[name][rr, cc]
    return pl.DataFrame(cols).sort("date", "sid")


def run_exposures(project: Project, end: date | None = None) -> dict[str, Any]:
    cfg = project.config
    P, t = load_panel(project, end)
    first = get_calendar(cfg.calendar).prev_session(cfg.history.model_start)
    log.info("descriptors", sessions=len(P.dates), sids=len(P.sids))
    desc = compute_descriptors(P, t["fundamentals_pit"], t["security_master"], cfg)
    log.info("exposures", first=str(first))
    exposures, qa, dqa = build_exposures(P, desc, cfg, first, first)
    out = project.model_dir
    for name, df, part in (("descriptors", descriptor_frame(P, desc, P.row[first]), "date"),
                           ("exposures", exposures, "date"), ("exposure_qa", qa, None),
                           ("descriptor_qa", dqa, None)):
        replace_table(df, out / name, part)
    refresh_catalog(project.catalog_path, {n: out / n for n in ("descriptors", "exposures", "exposure_qa",
                                                                 "descriptor_qa")})
    return exposure_report(exposures, qa)


def run_regression_stage(project: Project, end: date | None = None) -> dict[str, Any]:
    """Phase 5: one constrained WLS per session from history.model_start (returns t, exposures t-1)."""
    cfg = project.config
    P, _ = load_panel(project, end)
    exposures = read_table(project.model_dir, "exposures")
    res = run_regressions(P, exposures, cfg, cfg.history.model_start)
    out = project.model_dir
    for name, df, part in (("factor_returns", res.factor_returns, "date"), ("regression_stats", res.stats, None),
                           ("specific_returns", res.specific, "date"), ("omega_summary", res.omega, "date")):
        replace_table(df, out / name, part)
    refresh_catalog(project.catalog_path, {n: out / n for n in ("factor_returns", "regression_stats",
                                                                 "specific_returns", "omega_summary")})
    french = read_reference(project.raw_dir, "famafrench", "daily")
    return regression_report(res, french)


def regression_report(res: RegressionResult, french: pl.DataFrame) -> dict[str, Any]:
    st = res.stats.filter(pl.col("status") == "ok")
    fc = res.factor_returns.filter(pl.col("factor") == "COUNTRY").select("date", "f")
    mk = (st.select("date", "country_minus_mkt").join(fc, on="date")
          .with_columns(mkt=pl.col("f") - pl.col("country_minus_mkt")))
    shocks = res.factor_returns.filter(pl.col("f_over_sigma").abs() > 6)
    return {
        "days_ok": st.height, "days_skipped": res.stats.height - st.height,
        "constraint_resid_max": as_float(st["constraint_resid"].max(), 0.0),
        "cond_max": as_float(st["cond"].max(), 0.0), "r2_mean": as_float(st["r2_w"].mean(), 0.0),
        "n_min": as_int(st["n"].min(), 0),
        "corr_country_vs_capweighted_estu": float(np.corrcoef(mk["f"], mk["mkt"])[0, 1]),
        "country_minus_mkt_abs_max_bp": as_float(mk["country_minus_mkt"].abs().max(), 0.0) * 1e4,
        "factor_shocks_over_6sd": shocks.height,
        "french": {k: round(v, 3) for k, v in french_correlations(res.factor_returns, french).items()},
    }


def run_factor_cov_stage(project: Project, end: date | None = None, refresh: str | None = None) -> dict[str, Any]:
    """Phase 6: factor covariance for every date with enough history."""
    import time

    cfg = project.config
    fr = read_table(project.model_dir, "factor_returns")
    if end is not None:
        fr = fr.filter(pl.col("date") <= end)
    names = factor_order(fr)
    dates, Phi, observed = factor_panel(fr, names)
    t0 = time.perf_counter()
    res = run_factor_cov(dates, Phi, observed, names, cfg, cfg.model_id,
                         refresh or cfg.factor_cov.eigen.backfill_refresh)
    elapsed = time.perf_counter() - t0
    out = project.model_dir
    for name, df, part in (("factor_cov", res.cov, "date"), ("factor_risk_diag", res.risk, "date"),
                           ("eigen_diag", res.eigen, None), ("vra_factor", res.vra, None)):
        replace_table(df, out / name, part)
    refresh_catalog(project.catalog_path, {n: out / n for n in ("factor_cov", "factor_risk_diag", "eigen_diag",
                                                                 "vra_factor")})
    return factor_cov_report(dates, Phi, observed, names, res, cfg.horizon_days, elapsed)


def factor_cov_report(dates: list[date], Phi: np.ndarray, observed: np.ndarray, names: list[str],
                      res: FactorCovResult, horizon: int, elapsed: float) -> dict[str, Any]:
    min_eig = min(float(np.linalg.eigvalsh(F).min()) for F in res.final.values())
    vra = res.vra
    covid = vra.filter(pl.col("date").is_between(date(2020, 3, 1), date(2020, 4, 30)))["lambda_F"].max()
    calm = vra.filter(pl.col("date").is_between(date(2021, 1, 1), date(2021, 12, 31)))["lambda_F"].min()
    fb = factor_bias(dates, Phi, observed, res.final, names, horizon, date(2020, 1, 2), date(2021, 12, 31))
    eb = eigen_bias_battery(dates, Phi, res.pre, res.post_eigen, horizon, date(2019, 1, 2), dates[-1])
    inside = fb.filter(pl.col("bias").is_between(0.85, 1.15))
    return {
        "forecasts": len(res.final), "first": str(min(res.final)), "last": str(max(res.final)),
        "seconds_per_forecast": round(elapsed / max(len(res.final), 1), 3), "min_eigenvalue": min_eig,
        "lambda_max_mar_apr_2020": covid, "lambda_min_2021": calm,
        "factor_bias_2020_2021_inside_0.85_1.15": f"{inside.height}/{fb.height}",
        "factor_bias_outside": {r["factor"]: round(r["bias"], 3) for r in fb.filter(
            ~pl.col("bias").is_between(0.85, 1.15)).iter_rows(named=True)},
        "eigen_smallest10_bias_before_after": [round(as_float(eb["bias_before"][:10].mean()), 3),
                                               round(as_float(eb["bias_after"][:10].mean()), 3)],
    }


def run_specific_risk_stage(project: Project, end: date | None = None) -> dict[str, Any]:
    """Phase 7: five-layer specific risk for every session with 252 sessions of specific returns."""
    cfg = project.config
    P, _ = load_panel(project, end)
    U = pivot(read_table(project.model_dir, "specific_returns"), "u", P.dates, P.sids)
    ex = read_table(project.model_dir, "exposures")
    res = run_specific_risk(P, U, {d[0]: g for d, g in ex.group_by("date")}, cfg, cfg.history.model_start)
    out = project.model_dir
    replace_table(res.table, out / "specific_risk", "date")
    replace_table(res.vra, out / "vra_specific", None)
    refresh_catalog(project.catalog_path, {n: out / n for n in ("specific_risk", "vra_specific")})
    return specific_report(P, U, res, cfg.horizon_days)


def specific_report(P: Panel, U: np.ndarray, res: SpecificRiskResult, horizon: int) -> dict[str, Any]:
    t = res.table
    ann = t.join(pl.DataFrame({"date": P.dates, "_r": np.arange(len(P.dates))}), on="date")
    estu_med = []
    for d, g in t.group_by("date"):
        r = P.row[d[0]]
        cols = np.array([P.col[int(s)] for s in g["sid"].to_list()])
        m = P["in_estu"][r, cols]
        estu_med.append(float(np.median(g["sigma_final"].to_numpy()[m])) * np.sqrt(12))
    cov_days = [(P["in_cov"][r] & np.isfinite(res.final[r])).sum() / max(P["in_cov"][r].sum(), 1)
                for r in range(len(P.dates)) if np.isfinite(res.final[r]).any()]
    window = (date(2019, 1, 2), date(2021, 12, 31))
    full = specific_bias(P.dates, U, res.final, P["capw"], P["in_estu"], res.decile, horizon, *window)
    ts = specific_bias(P.dates, U, res.ts_only, P["capw"], P["in_estu"], res.decile, horizon, *window)
    vra = res.vra
    def spread(by: dict[int, float]) -> float:
        return max(by.values()) - min(by.values()) if by else float("nan")

    return {
        "forecast_rows": ann.height, "first": str(t["date"].min()), "last": str(t["date"].max()),
        "coverage_finite_min": round(float(min(cov_days)), 4),
        "estu_median_annualized": [round(min(estu_med), 4), round(max(estu_med), 4)],
        "lambda_S_max_mar_apr_2020": vra.filter(pl.col("date").is_between(date(2020, 3, 1), date(2020, 4, 30)))[
            "lambda_S"].max(),
        "lambda_S_min_2021": vra.filter(pl.col("date").dt.year() == 2021)["lambda_S"].min(),
        "bias_2019_2021_full": round(float(full["overall"]), 3),
        "bias_2019_2021_ts_only": round(float(ts["overall"]), 3),
        "decile_bias_full": {k: round(v, 3) for k, v in full["by_decile"].items()},
        "decile_bias_ts_only": {k: round(v, 3) for k, v in ts["by_decile"].items()},
        "decile_spread_full_vs_ts": [round(spread(full["by_decile"]), 3), round(spread(ts["by_decile"]), 3)],
        "gamma_below_1_share": round(as_float((t["gamma"] < 1).mean()), 4),
    }


def exposure_report(exposures: pl.DataFrame, qa: pl.DataFrame) -> dict[str, Any]:
    by = qa.group_by("style").agg(
        coverage_min=pl.col("coverage").min(), coverage_median=pl.col("coverage").median(),
        stability_median=pl.col("stability").median(), vif_max=pl.col("vif").max(),
        mean_abs_max=pl.col("mean_cw").abs().max(), std_dev_max=(pl.col("std_ew") - 1).abs().max())
    return {
        "range": [str(exposures["date"].min()), str(exposures["date"].max())],
        "rows": exposures.height,
        "styles": {r["style"]: {k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items() if k != "style"}
                   for r in by.sort("style").iter_rows(named=True)},
        "price_styles": [s for s in STYLES if s not in FUNDAMENTAL_STYLES],
    }


# model table -> partitioned by year (True) or one file (False); the backfill writes the same layout
MODEL_TABLES: dict[str, bool] = {
    "descriptors": True, "exposures": True, "exposure_qa": False, "descriptor_qa": False,
    "factor_returns": True, "regression_stats": False, "specific_returns": True, "omega_summary": True,
    "factor_cov": True, "factor_risk_diag": True, "eigen_diag": False, "vra_factor": False,
    "specific_risk": True, "vra_specific": False,
}


def _upsert(project: Project, frames: dict[str, pl.DataFrame], days: list[date]) -> None:
    for name, df in frames.items():
        part = df.filter(pl.col("date").is_in(days)) if "date" in df.columns else pl.DataFrame()
        upsert_dates(part, project.model_dir / name, days, by_year=MODEL_TABLES[name])


def run_model_dates(project: Project, dates: list[date]) -> dict[str, float]:
    """The daily model step (§13.2 step 5) for `dates`, written into the model tables.

    Exposures need the sessions a stability lag earlier. The regression, specific risk and both VRA
    multipliers carry state, so they are recomputed from `history.model_start`, and only `dates` is
    written. The factor covariance simulates its eigen adjustment on each date ('daily' refresh).
    Returns seconds per step.
    """
    import time

    cfg = project.config
    cal = get_calendar(cfg.calendar)
    days = sorted(dates)
    out = project.model_dir
    clock: dict[str, float] = {}
    t0 = time.perf_counter()
    P, t = load_panel(project, days[-1])
    anchor = cal.prev_session(cfg.history.model_start)
    first = max(anchor, cal.offset(days[0], -cfg.gates.stability_lag_days))
    desc = compute_descriptors(P, t["fundamentals_pit"], t["security_master"], cfg)
    exposures, qa, dqa = build_exposures(P, desc, cfg, first, anchor)
    _upsert(project, {"descriptors": descriptor_frame(P, desc, P.row[days[0]]), "exposures": exposures,
                      "exposure_qa": qa, "descriptor_qa": dqa}, days)
    clock["exposures_s"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    ex = read_table(out, "exposures")
    res = run_regressions(P, ex, cfg, cfg.history.model_start)
    _upsert(project, {"factor_returns": res.factor_returns, "regression_stats": res.stats,
                      "specific_returns": res.specific, "omega_summary": res.omega}, days)
    clock["regression_s"] = time.perf_counter() - t1

    t1 = time.perf_counter()
    fr = read_table(out, "factor_returns").filter(pl.col("date") <= days[-1])
    names = factor_order(fr)
    fdates, Phi, observed = factor_panel(fr, names)
    fc = run_factor_cov(fdates, Phi, observed, names, cfg, cfg.model_id, "daily", only=set(days))
    _upsert(project, {"factor_cov": fc.cov, "factor_risk_diag": fc.risk, "eigen_diag": fc.eigen,
                      "vra_factor": fc.vra}, days)
    clock["factor_cov_s"] = time.perf_counter() - t1

    t1 = time.perf_counter()
    U = pivot(read_table(out, "specific_returns"), "u", P.dates, P.sids)
    sr = run_specific_risk(P, U, {d[0]: g for d, g in ex.group_by("date")}, cfg, cfg.history.model_start)
    _upsert(project, {"specific_risk": sr.table, "vra_specific": sr.vra}, days)
    clock["specific_s"] = time.perf_counter() - t1
    refresh_catalog(project.catalog_path, {n: out / n for n in MODEL_TABLES})
    return clock
