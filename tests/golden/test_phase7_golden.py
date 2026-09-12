"""Phase 7 acceptance (blueprint §17) on specific risk built from the development data."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.config import load_project
from eqrisk.model.panel import pivot
from eqrisk.model.tables import load_panel
from eqrisk.validation.bias import specific_bias

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "model" / "us_lc_v1"
WINDOW = (date(2019, 1, 2), date(2021, 12, 31))


def _read(name):
    root = MODEL / name
    if not root.exists():
        pytest.skip(f"no model table {name}")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


@pytest.fixture(scope="module")
def data():
    sr = _read("specific_risk")
    P, _ = load_panel(load_project(ROOT))
    U = pivot(_read("specific_returns"), "u", P.dates, P.sids)
    final = pivot(sr, "sigma_final", P.dates, P.sids)
    ts = pivot(sr, "sigma_ts", P.dates, P.sids)
    dec = pivot(sr, "size_decile", P.dates, P.sids, fill=-1, dtype=np.int64)
    return sr, P, U, final, ts, dec


def test_every_coverage_name_has_a_positive_forecast(data):
    sr, P, _, final, _, _ = data
    for d in sr["date"].unique().to_list():
        r = P.row[d]
        cov = P["in_cov"][r]
        assert (np.isfinite(final[r][cov]) & (final[r][cov] > 0)).all(), d


def test_estu_median_annualized_in_range(data):
    sr, P, _, final, _, _ = data
    for d in sr["date"].unique().to_list():
        r = P.row[d]
        med = np.median(final[r][P["in_estu"][r]]) * np.sqrt(12)
        assert 0.10 <= med <= 0.45, (d, med)


def test_cap_weighted_bias_overall_and_by_size_decile(data):
    _, P, U, final, ts, dec = data
    full = specific_bias(P.dates, U, final, P["capw"], P["in_estu"], dec, 21, *WINDOW)
    tso = specific_bias(P.dates, U, ts, P["capw"], P["in_estu"], dec, 21, *WINDOW)
    print("\nfull:", full, "\nts-only:", tso)
    assert 0.9 <= full["overall"] <= 1.1
    assert all(0.8 <= b <= 1.2 for b in full["by_decile"].values())
    spread = lambda by: max(by.values()) - min(by.values())  # noqa: E731
    assert spread(full["by_decile"]) <= spread(tso["by_decile"])


def test_lambda_s_rises_in_march_2020():
    vra = _read("vra_specific")
    before = vra.filter(pl.col("date").is_between(date(2020, 1, 2), date(2020, 2, 14)))["lambda_S"].mean()
    peak = vra.filter(pl.col("date").is_between(date(2020, 3, 1), date(2020, 4, 30)))["lambda_S"].max()
    assert peak > 1.3 and peak > 1.3 * before


def test_new_listings_lean_on_the_structural_model():
    sr = _read("specific_risk")
    first = sr.group_by("sid").agg(pl.col("date").min().alias("first"))
    late = first.filter(pl.col("first") > date(2019, 6, 1))
    rows = sr.join(late, on="sid").filter(pl.col("date") == pl.col("first"))
    assert rows.height > 5 and (rows["gamma"] < 1).all()
