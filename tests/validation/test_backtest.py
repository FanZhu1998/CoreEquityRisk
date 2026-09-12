"""Phase 9 building blocks on synthetic data where the truth is known."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.config import load_config
from eqrisk.validation.backtest import (
    FAIL,
    NOT_RUN,
    PASS,
    ValidationResult,
    min_risk_factor_b,
    portfolio_sigma,
    rolling_bias,
    scorecard,
    summarize,
    vol_deciles,
)
from eqrisk.validation.report import write_report

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")


def test_summary_under_truth_is_near_one():
    b = np.random.default_rng(0).standard_normal((600, 20))
    s = summarize(b, [f"P{k}" for k in range(20)], 12)
    assert s["bias"].mean() == pytest.approx(1.0, abs=0.03)
    assert s["inside"].mean() >= 0.8 and s["band"][0] == pytest.approx(np.sqrt(2 / 600))
    assert s["mrad"].mean() == pytest.approx(0.17, abs=0.03)                  # USE4 App. A under normality


def test_rolling_bias_matches_direct_std():
    x = np.random.default_rng(1).standard_normal(30)
    r = rolling_bias(x, 12)
    assert np.isnan(r[:11]).all() and r[-1] == pytest.approx(np.std(x[-12:], ddof=1))


def _factor_world(scale, T=21 * 300, K=6, seed=2):
    rng = np.random.default_rng(seed)
    B = rng.standard_normal((K, K))
    F = B @ B.T / K + 0.2 * np.eye(K)
    Phi = rng.multivariate_normal(np.zeros(K), F / 21, size=T)           # 21-day sums have covariance F
    dates = [date.fromordinal(730000 + t) for t in range(T)]
    forecasts = {d: F * scale for d in dates}
    ts = list(range(0, T - 22, 21))
    return dates, Phi, np.ones((T, K), bool), forecasts, ts


def test_min_risk_portfolios_unbiased_with_the_true_matrix_and_not_with_a_shrunk_one():
    alphas = np.random.default_rng(3).standard_normal((6, 40))
    dates, Phi, obs, F, ts = _factor_world(1.0)
    b = min_risk_factor_b(dates, Phi, obs, F, ts, 21, alphas)
    assert np.std(b, ddof=1) == pytest.approx(1.0, abs=0.06)
    dates, Phi, obs, F, ts = _factor_world(0.5)                           # variance understated by half
    assert np.std(min_risk_factor_b(dates, Phi, obs, F, ts, 21, alphas), ddof=1) == pytest.approx(np.sqrt(2), abs=0.08)


def test_portfolio_sigma_equals_the_dense_form():
    rng = np.random.default_rng(4)
    X, H = rng.standard_normal((30, 5)), rng.dirichlet(np.ones(30), size=3).T
    B = rng.standard_normal((5, 5))
    F, spec = B @ B.T, rng.uniform(0.01, 0.05, 30)
    dense = np.sqrt(np.diag(H.T @ (X @ F @ X.T + np.diag(spec)) @ H))
    assert np.allclose(portfolio_sigma(X, F, spec, H), dense, rtol=1e-12)


def test_vol_deciles_split_the_estu_evenly():
    sigma = np.tile(np.arange(1.0, 23.0), (2, 1))
    estu = np.ones_like(sigma, bool)
    estu[:, -2:] = False
    g = vol_deciles(sigma, estu, 10)
    assert (g[:, -2:] == -1).all() and np.bincount(g[0, :-2]).tolist() == [2] * 10


def _metrics(**over):
    m = {"operations": {"fast": None, "fast_text": "-", "unattended": None, "streak_text": "-", "rerun": None,
                        "rerun_text": "-"},
         "coverage_min": 0.995, "coverage_min_date": date(2020, 3, 2), "constraint_resid_max": 1e-17,
         "country_corr": 0.999, "min_eigenvalue": 1e-6, "factor_bias_mean": 1.02, "eigen_dev_before": 0.2,
         "eigen_dev_after": 0.1, "specific_bias": 1.01, "specific_size_deciles": [0.95, 1.05],
         "mrad_by_family": {"(d)": 0.2}, "mrad_mean": 0.2}
    m.update(over)
    return m


def test_scorecard_marks_each_criterion():
    rows = scorecard(_metrics(), CFG.validation.criteria, 12)
    assert [r["status"] for r in rows if r["area"] == "Operations"] == [NOT_RUN] * 3
    assert {r["status"] for r in rows if r["area"] != "Operations"} == {PASS}
    bad = scorecard(_metrics(coverage_min=0.98, specific_size_deciles=[0.7, 1.0], mrad_mean=0.3),
                    CFG.validation.criteria, 12)
    failed = {r["criterion"].split(" ")[0] for r in bad if r["status"] == FAIL}
    assert failed == {">=", "each", "12-period"}


def test_report_has_every_section(tmp_path):
    rng = np.random.default_rng(5)
    table = lambda n, p: summarize(rng.standard_normal((n, p)), [f"P{k}" for k in range(p)], 12)  # noqa: E731
    dates = [date(2020, 1, 1 + k) for k in range(20)]
    res = ValidationResult(
        model_id="t", config_hash="0" * 64, start=dates[0], end=dates[-1], horizon=21, periods=40,
        scorecard=scorecard(_metrics(), CFG.validation.criteria, 12), external=[],
        factor=table(40, 5), eigen=pl.DataFrame({"k": np.arange(5), "n": [40] * 5, "bias_before": rng.uniform(1, 2, 5),
                                                 "bias_after": rng.uniform(0.9, 1.1, 5)}),
        random_alpha={k: table(40, 10) for k in ("pre", "post", "final")}, market=table(40, 2), industry=table(40, 4),
        random=table(40, 10), specific={m: {"overall": 1.0, "size": {0: 1.0, 1: 1.1}, "vol": {0: 0.9, 1: 1.2}}
                                        for m in ("full", "ts")},
        rolling={"COUNTRY": (dates, rng.uniform(0.8, 1.2, 20))},
        vra=pl.DataFrame({"date": dates, "lambda_F": rng.uniform(0.8, 1.5, 20), "lambda_S": rng.uniform(0.8, 1.5, 20)}),
        metrics=_metrics())
    report = write_report(res, tmp_path / "rep")
    text = report.read_text(encoding="utf-8")
    for section in ("## Summary", "## External checks", "## (a)", "## (b)", "## (c)", "## (d)", "## (e)", "## (f)",
                    "## (g)", "## Volatility regime"):
        assert section in text
    for fig in ("factor_bias", "rolling_bias", "eigen_smile", "random_alpha", "specific_deciles", "lambda"):
        assert (tmp_path / "rep" / f"{fig}.png").stat().st_size > 1000
    assert (tmp_path / "rep" / "summary.json").exists()
