"""Daily QA gates (blueprint §11.4, §6.4). A failed FAIL gate quarantines the session; WARN gates
are reported in the manifest and the notification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import GatesCfg, Project

FAIL, WARN = "FAIL", "WARN"
OK, QUARANTINED = "OK", "QUARANTINED"
_MONTHS_PER_YEAR = 12
_BP = 1e4


@dataclass
class DayData:
    """What the gates read for one session."""
    n_cov: int                          # coverage names (universe rows)
    n_priced: int                       # coverage names with a positive close that session
    qa: pl.DataFrame                    # exposure_qa rows: style, mean_cw, std_ew, imputed, stability, vif
    stats: dict[str, Any] | None        # regression_stats row
    factors: pl.DataFrame               # factor_returns rows: factor, f, f_over_sigma
    F: np.ndarray | None                # final factor covariance
    lambda_f: float | None
    lambda_s: float | None
    sigma_cov: np.ndarray               # monthly specific volatility of every coverage name (NaN if missing)
    sigma_estu: np.ndarray              # the same for ESTU names


def _gate(name: str, level: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"gate": name, "level": level, "ok": bool(ok), "detail": detail}


def gate_results(day: DayData, g: GatesCfg, fundamental: set[str]) -> list[dict[str, Any]]:
    out = []
    share = day.n_priced / day.n_cov if day.n_cov else 0.0
    out.append(_gate("data_freshness", FAIL, share >= g.price_coverage_min,
                     f"{day.n_priced}/{day.n_cov} coverage names priced ({share:.2%})"))
    out.append(_gate("universe", WARN, g.universe_min <= day.n_cov <= g.universe_max, f"coverage {day.n_cov}"))
    qa = day.qa
    if qa.height == 0:
        out.append(_gate("exposure_moments", FAIL, False, "no exposures"))
    else:
        mean_dev = float(qa["mean_cw"].abs().max())  # type: ignore[arg-type]
        std_dev = float((qa["std_ew"] - 1.0).abs().max())  # type: ignore[arg-type]
        out.append(_gate("exposure_moments", FAIL, mean_dev < g.exposure_mean_tol and std_dev < g.exposure_std_tol,
                         f"max |cap-weighted mean| {mean_dev:.1e}, max |EW std - 1| {std_dev:.1e}"))
        imp = qa.with_columns(share=pl.col("imputed") / max(day.n_cov, 1)).sort("share", descending=True).row(
            0, named=True)
        out.append(_gate("imputation", WARN, imp["share"] <= g.imputed_max,
                         f"max imputed {imp['share']:.1%} ({imp['style']})"))
        vif = qa.drop_nulls("vif")
        if vif.height:
            top = vif.sort("vif", descending=True).row(0, named=True)
            text = f"max VIF {top['vif']:.2f} ({top['style']})"
            out.append(_gate("vif", FAIL, top["vif"] <= g.vif_fail, text))
            out.append(_gate("vif_warn", WARN, top["vif"] <= g.vif_warn, text))
        floor = pl.when(pl.col("style").is_in(sorted(fundamental))).then(g.stability_fundamental_min).otherwise(
            g.stability_min)
        low = qa.drop_nulls("stability").filter(pl.col("stability").is_not_nan() & (pl.col("stability") < floor))
        out.append(_gate("stability", WARN, low.height == 0, ", ".join(
            f"{r['style']} {r['stability']:.2f}" for r in low.iter_rows(named=True)) or "all styles stable"))
    s = day.stats
    if s is None or s.get("status") != "ok":
        out.append(_gate("regression", FAIL, False, f"regression status {None if s is None else s.get('status')}"))
    else:
        ok = s["constraint_resid"] < g.constraint_resid_max and s["cond"] < g.cond_max and s["n"] >= g.regression_n_min
        out.append(_gate("regression", FAIL, ok,
                         f"n {s['n']}, cond {s['cond']:.1f}, constraint residual {s['constraint_resid']:.1e}"))
        lo, hi = g.r2_bounds
        out.append(_gate("fit", WARN, lo <= s["r2_w"] <= hi, f"R2 {s['r2_w']:.3f}"))
        bp = abs(s["country_minus_mkt"]) * _BP
        out.append(_gate("country_tracking", WARN, bp < g.country_track_bp, f"|country - ESTU| {bp:.1f} bp"))
    shocks = day.factors.filter(pl.col("f_over_sigma").abs() > g.factor_shock_z) if day.factors.height else day.factors
    out.append(_gate("factor_shock", WARN, shocks.height == 0, ", ".join(
        f"{r['factor']} {r['f_over_sigma']:+.1f} sd" for r in shocks.iter_rows(named=True)) or "none"))
    if day.F is None:
        out.append(_gate("factor_covariance", FAIL, False, "no factor covariance"))
    else:
        sym, mn = bool(np.array_equal(day.F, day.F.T)), float(np.linalg.eigvalsh(day.F).min())
        out.append(_gate("factor_covariance", FAIL, sym and mn > 0, f"symmetric {sym}, min eigenvalue {mn:.2e}"))
    lo, hi = g.lambda_bounds
    lams = {"lambda_F": day.lambda_f, "lambda_S": day.lambda_s}
    out.append(_gate("vra", WARN, all(v is not None and lo <= v <= hi for v in lams.values()), ", ".join(
        f"{k} {v:.2f}" if v is not None else f"{k} missing" for k, v in lams.items())))
    good = np.isfinite(day.sigma_cov) & (day.sigma_cov > 0)
    out.append(_gate("specific_coverage", FAIL, day.sigma_cov.size > 0 and bool(good.all()),
                     f"{int(good.sum())}/{day.sigma_cov.size} coverage names with a finite positive forecast"))
    est = day.sigma_estu[np.isfinite(day.sigma_estu)]
    med = float(np.median(est)) * float(np.sqrt(_MONTHS_PER_YEAR)) if est.size else float("nan")
    lo, hi = g.spec_median_ann
    out.append(_gate("specific_level", WARN, lo <= med <= hi, f"ESTU median {med:.1%} annualized"))
    return out


def status_of(results: list[dict[str, Any]]) -> str:
    return QUARANTINED if any(r["level"] == FAIL and not r["ok"] for r in results) else OK


def _day(root: Path, name: str, d: date) -> pl.DataFrame:
    path = root / name
    if not path.exists():
        return pl.DataFrame()
    return pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False).filter(
        pl.col("date") == d).collect()


def load_day(project: Project, d: date) -> DayData:
    m, s = project.model_dir, project.staged_dir
    uni = _day(s, "universe", d)
    px = _day(s, "prices", d)
    n_priced = px.filter(pl.col("close_unadj") > 0).select("sid").join(uni.select("sid"), on="sid").height \
        if px.height and uni.height else 0
    cov = _day(m, "factor_cov", d)
    F = None
    if cov.height:
        pos = {n: i for i, n in enumerate(sorted(set(cov["factor_i"].to_list()) | set(cov["factor_j"].to_list())))}
        F = np.zeros((len(pos), len(pos)))
        i, j = cov["factor_i"].replace_strict(pos).to_numpy(), cov["factor_j"].replace_strict(pos).to_numpy()
        F[i, j] = cov["cov_final"].to_numpy()
        F[j, i] = cov["cov_final"].to_numpy()
    st, vf, vs = _day(m, "regression_stats", d), _day(m, "vra_factor", d), _day(m, "vra_specific", d)
    sr = _day(m, "specific_risk", d)
    sr = sr.select("sid", "sigma_final") if sr.height else pl.DataFrame(schema={"sid": pl.Int64,
                                                                                "sigma_final": pl.Float64})
    sig = (uni.select("sid", "in_estu").join(sr, on="sid", how="left") if uni.height
           else pl.DataFrame(schema={"sid": pl.Int64, "in_estu": pl.Boolean, "sigma_final": pl.Float64}))
    return DayData(
        n_cov=uni.height, n_priced=n_priced, qa=_day(m, "exposure_qa", d),
        stats=st.row(0, named=True) if st.height else None, factors=_day(m, "factor_returns", d), F=F,
        lambda_f=float(vf["lambda_F"][0]) if vf.height and vf["lambda_F"][0] is not None else None,
        lambda_s=float(vs["lambda_S"][0]) if vs.height and vs["lambda_S"][0] is not None else None,
        sigma_cov=sig["sigma_final"].fill_null(np.nan).to_numpy(),
        sigma_estu=sig.filter(pl.col("in_estu"))["sigma_final"].fill_null(np.nan).to_numpy())
