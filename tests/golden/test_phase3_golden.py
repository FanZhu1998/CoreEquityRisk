"""Phase 3 acceptance (blueprint §17, Phase 3 table) against the staged development slice."""

import math
from datetime import date

import polars as pl
import pytest

pytestmark = pytest.mark.golden

SPLITS = [("AAPL", date(2020, 8, 31), 4.0), ("TSLA", date(2020, 8, 31), 5.0), ("TSLA", date(2022, 8, 25), 3.0),
          ("AMZN", date(2022, 6, 6), 20.0), ("GOOGL", date(2022, 7, 18), 20.0), ("NVDA", date(2024, 6, 10), 10.0)]


@pytest.mark.parametrize(("ticker", "day", "ratio"), SPLITS)
def test_splits_have_sane_returns_and_continuous_market_cap(staged, sid_of, ticker, day, ratio):
    sid = sid_of(ticker)                                     # TSLA split in 2020 before joining the index
    px = staged("prices").filter((pl.col("sid") == sid) & (pl.col("date") == day)).row(0, named=True)
    assert px["action"] == "split" and px["split_ratio"] == pytest.approx(ratio)
    assert abs(px["ret"]) <= 0.20
    mc = staged("mcap").filter((pl.col("sid") == sid) & pl.col("date").is_between(date(day.year, 1, 1), day)).sort("date")
    before, on = mc.row(-2, named=True), mc.row(-1, named=True)
    assert abs(math.log(on["mcap_issuer"] / before["mcap_issuer"]) - math.log(1 + px["ret"])) <= 0.05


def test_fb_to_meta_is_one_security_and_one_issuer(staged, sid_of):
    fb, meta = sid_of("FB", date(2022, 6, 8)), sid_of("META", date(2022, 6, 9))
    assert fb == meta
    assert staged("security_master").filter(pl.col("sid") == fb)["issuer_id"].n_unique() == 1


def test_googl_goog_share_classes(staged, sid_of):
    d = date(2024, 6, 3)
    a, c = sid_of("GOOGL", d), sid_of("GOOG", d)
    m = staged("security_master").filter(pl.col("sid").is_in([a, c]))
    assert m["issuer_id"].n_unique() == 1
    assert m.filter(pl.col("sid") == c)["linked_sid"].item() == a
    u = staged("universe").filter((pl.col("date") == d) & pl.col("sid").is_in([a, c]))
    assert u.filter(pl.col("sid") == a)["in_estu"].item()
    assert u.filter(pl.col("sid") == c)["exclusion_reason"].item() == "secondary_class"


def test_daily_coverage_count_and_tsla_entry(staged, sid_of):
    counts = staged("universe").group_by("date").len()
    assert counts["len"].min() >= 495 and counts["len"].max() <= 510
    tsla = sid_of("TSLA", date(2021, 1, 4))
    assert staged("universe").filter(pl.col("sid") == tsla)["date"].min() == date(2020, 12, 21)


@pytest.mark.parametrize("day", [date(2025, 1, 9), date(2022, 6, 20), date(2023, 6, 19), date(2024, 6, 19)])
@pytest.mark.parametrize("table", ["prices", "mcap", "universe", "membership"])
def test_no_rows_on_closed_days(staged, table, day):
    assert staged(table).filter(pl.col("date") == day).height == 0


def test_fundamentals_are_available_only_after_filing(staged):
    pit = staged("fundamentals_pit")
    assert pit.filter(pl.col("available_date") <= pl.col("filed")).height == 0


def test_every_coverage_name_has_an_industry(staged):
    u = staged("universe")
    assert u.filter(pl.col("industry").is_null()).height == 0, (
        u.filter(pl.col("industry").is_null()).group_by("ticker").len())
    industry_issues = staged("exceptions").filter(
        (pl.col("area") == "industry") & (pl.col("issue") != "override_noop"))
    assert industry_issues.height == 0, industry_issues


def test_no_thin_industries_in_the_model_window(staged):
    est = staged("universe").filter(pl.col("in_estu") & (pl.col("date") >= date(2018, 1, 2)))
    neff = est.group_by("date", "industry").agg(
        n=pl.len(), neff=pl.col("v_reg").sum() ** 2 / (pl.col("v_reg") ** 2).sum())
    thin = neff.filter(pl.col("neff") < 5)
    assert thin.height == 0, thin.group_by("industry").agg(days=pl.len(), worst=pl.col("neff").min())
