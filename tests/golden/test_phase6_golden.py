"""Phase 6 acceptance (blueprint §17) on the factor covariance built from the development data."""

import time
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.config import load_project
from eqrisk.model.factor_cov import factor_panel, run_factor_cov
from eqrisk.model.tables import factor_order
from eqrisk.validation.bias import eigen_bias_battery, factor_bias

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "model" / "us_lc_v1"


def _read(name):
    root = MODEL / name
    if not root.exists():
        pytest.skip(f"no model table {name}")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


@pytest.fixture(scope="module")
def inputs():
    fr = _read("factor_returns")
    names = factor_order(fr)
    dates, Phi, obs = factor_panel(fr, names)
    return names, dates, Phi, obs


@pytest.fixture(scope="module")
def matrices(inputs):
    names, *_ = inputs
    pos = {n: i for i, n in enumerate(names)}
    cov = _read("factor_cov")
    out: dict = {"final": {}, "pre": {}}
    for (d,), g in cov.group_by("date"):
        i, j = g["factor_i"].replace_strict(pos).to_numpy(), g["factor_j"].replace_strict(pos).to_numpy()
        for key, col in (("final", "cov_final"), ("pre", "cov_pre_eigen")):
            M = np.zeros((len(names), len(names)))
            M[i, j] = g[col].to_numpy()
            M[j, i] = g[col].to_numpy()
            out[key][d] = M
    lam = dict(_read("vra_factor").select("date", "lambda_F").iter_rows())
    out["post"] = {d: F / lam[d] ** 2 for d, F in out["final"].items()}
    return out


def test_every_forecast_symmetric_and_positive_definite(matrices):
    assert min(float(np.linalg.eigvalsh(F).min()) for F in matrices["final"].values()) > 0


def test_stored_forecast_is_reproduced_bit_for_bit(inputs, matrices):
    names, dates, Phi, obs = inputs
    project = load_project(ROOT)
    refresh_day = max(_read("eigen_diag")["date"].to_list())          # a date that simulated its own v(k)
    again = run_factor_cov(dates, Phi, obs, names, project.config, project.config.model_id, "weekly",
                           only={refresh_day})
    assert np.allclose(again.final[refresh_day], matrices["final"][refresh_day], rtol=0, atol=1e-15)


def test_vra_rises_in_march_2020_and_relaxes_after():
    vra = _read("vra_factor")
    assert vra.filter(pl.col("date").is_between(date(2020, 3, 1), date(2020, 4, 30)))["lambda_F"].max() > 1.5
    assert vra.filter(pl.col("date").dt.year() == 2021)["lambda_F"].min() < 1.0


def test_eigen_adjustment_moves_small_eigenfactors_toward_one(inputs, matrices):
    names, dates, Phi, _ = inputs
    eb = eigen_bias_battery(dates, Phi, matrices["pre"], matrices["post"], 21, date(2019, 1, 2), dates[-30])
    before, after = eb["bias_before"][:10].to_numpy(), eb["bias_after"][:10].to_numpy()
    assert np.abs(after - 1).mean() < np.abs(before - 1).mean(), (before, after)


def test_factor_bias_over_the_backtest_meets_the_success_criterion(inputs, matrices):
    # §1.3: mean per-factor bias in [0.85, 1.15], and most factors inside that range (D-021).
    names, dates, Phi, obs = inputs
    fb = factor_bias(dates, Phi, obs, matrices["final"], names, 21, date(2019, 1, 2), dates[-30])
    inside = fb.filter(pl.col("bias").is_between(0.85, 1.15))
    print(f"\n2019-{dates[-30]}: mean {fb['bias'].mean():.3f}, {inside.height}/{fb.height} inside [0.85, 1.15]")
    assert 0.85 <= fb["bias"].mean() <= 1.15 and inside.height >= 2 * fb.height / 3


def test_factor_bias_2020_2021_exceptions_listed_within_the_sampling_band(inputs, matrices):
    # The window's 25 periods include the COVID crash; D-021 lists the exceptions to [0.85, 1.15]
    # and holds the window to the statistic's own 95% band, 1 +/- sqrt(2/T).
    names, dates, Phi, obs = inputs
    fb = factor_bias(dates, Phi, obs, matrices["final"], names, 21, date(2020, 1, 2), date(2021, 12, 31))
    outside = fb.filter(~pl.col("bias").is_between(0.85, 1.15))
    print("\n2020-2021 outside [0.85, 1.15]:", [(r["factor"], round(r["bias"], 2)) for r in outside.iter_rows(named=True)])
    assert fb.filter((pl.col("bias") - 1).abs() <= pl.col("band")).height >= 2 * fb.height / 3


def test_incremental_daily_update_under_60s(inputs):
    names, dates, Phi, obs = inputs
    project = load_project(ROOT)
    t0 = time.perf_counter()
    run_factor_cov(dates, Phi, obs, names, project.config, project.config.model_id, "daily", only={dates[-1]})
    assert time.perf_counter() - t0 < 60
