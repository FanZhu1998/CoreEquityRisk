"""Phase 4 acceptance (blueprint §17) against the model tables built from the development data."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.kernels.descriptors import weighted_corr
from eqrisk.model.exposures import FUNDAMENTAL_STYLES, STYLES

pytestmark = pytest.mark.golden
MODEL = Path(__file__).resolve().parents[2] / "data" / "model" / "us_lc_v1"
WINDOW = (date(2018, 1, 2), date(2021, 12, 31))          # §17 development slice


def _read(name):
    root = MODEL / name
    if not root.exists():
        pytest.skip(f"no model table {name}; run `eqrisk backfill --stage model`")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


@pytest.fixture(scope="module")
def qa():
    return _read("exposure_qa").filter(pl.col("date").is_between(*WINDOW))


@pytest.fixture(scope="module")
def xu(staged):
    x = _read("exposures").filter(pl.col("date").is_between(*WINDOW))
    u = staged("universe").select("date", "sid", "in_estu", "mcap", "capw")
    return x.join(u, on=["date", "sid"], how="left")


def test_moments_every_style_every_date(qa):
    assert qa["mean_cw"].abs().max() < 1e-8
    assert (qa["std_ew"] - 1).abs().max() < 1e-6


# MOMENTUM sits at the edge of two §17 guidelines by construction (DECISIONS D-014): its 252-session
# minimum leaves recent listings imputed on some days, and its 126-day half-life turns over ~11% of
# the weight each month. It is held to the looser bounds below; every other style to §17.
MOMENTUM_COVERAGE_FLOOR, MOMENTUM_STABILITY_FLOOR = 0.98, 0.88


def test_coverage_without_imputation(qa):
    worst = qa.group_by("style").agg(pl.col("coverage").min())
    for style, cov in worst.iter_rows():
        floor = 0.95 if style in FUNDAMENTAL_STYLES else MOMENTUM_COVERAGE_FLOOR if style == "MOMENTUM" else 0.99
        assert cov >= floor, f"{style}: {cov:.3f} < {floor}"


def test_stability_and_vif(qa):
    by = qa.group_by("style").agg(stab=pl.col("stability").median(), vif=pl.col("vif").max())
    for style, stab, vmax in by.iter_rows():
        floor = 0.95 if style in FUNDAMENTAL_STYLES else MOMENTUM_STABILITY_FLOOR if style == "MOMENTUM" else 0.90
        assert stab >= floor, f"{style} median stability {stab:.3f}"
        assert vmax < 10, f"{style} VIF {vmax:.1f}"
    # The §11.4 stability gate (0.80) is WARN-level. March-April 2020 reshuffled the beta tail
    # (NLBETA fell to 0.04, RESVOL to 0.59): the gate should flag those days, not fail them (D-014).
    warn = qa.filter(pl.col("stability") < 0.80).group_by("style").agg(days=pl.len(), worst=pl.col("stability").min())
    print("\nstability WARN days (< 0.80):", warn.sort("style").rows())


def test_spot_checks(xu, staged):
    day = xu.filter(pl.col("date") == pl.col("date").max())
    est = day.filter(pl.col("in_estu"))
    assert np.corrcoef(est["SIZE"], np.log(est["mcap"]))[0, 1] > 0.98
    by_ind = est.group_by("industry").agg(pl.col("BETA").mean(), pl.col("BOOK_TO_PRICE").mean())
    ind = {r["industry"]: r for r in by_ind.iter_rows(named=True)}
    assert ind["UTILITIES"]["BETA"] < 0 and ind["BANKS"]["BOOK_TO_PRICE"] > 0
    top = est.sort("SIZE", descending=True).head(len(est) // 10)
    assert top["mcap"].min() >= est.sort("mcap", descending=True)["mcap"][len(est) // 10] * 0.999


def test_orthogonal_styles(xu):
    for d, g in xu.filter(pl.col("in_estu")).group_by("date"):
        w = np.sqrt(g["mcap"].to_numpy())
        m = np.ones(g.height, bool)
        for style, target in (("RESIDUAL_VOLATILITY", "BETA"), ("NONLINEAR_BETA", "BETA")):
            rho = weighted_corr(g[style].to_numpy(), g[target].to_numpy(), w, m)
            assert abs(rho) < 1e-8, (d, style, rho)
        if d[0].day > 7:                                   # sampling a few dates is enough
            break


def test_every_style_present():
    x = _read("exposures")
    assert set(STYLES) <= set(x.columns)
