from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.staging.fundamentals_pit import PIT_SCHEMA
from eqrisk.staging.industry import assign_industries, map_sic, read_industries, read_overrides, read_sic_map
from eqrisk.staging.mcap import build_mcap
from eqrisk.staging.universe import build_universe

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config(ROOT / "configs" / "model_us_lc.yaml")
OVR = ROOT / "configs" / "overrides"
CAL = get_calendar("XNYS")
d = date


DEI, CSO, WAB = ("dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding",
                  "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic")
ORDER = ["override", DEI, CSO, WAB, "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"]
NO_FLOAT = pl.DataFrame(schema=PIT_SCHEMA)
MASTER1 = pl.DataFrame({"sid": [1], "cik": [1], "linked_sid": [None]}, schema_overrides={"linked_sid": pl.Int64})


def _count(period_end, value, concept=DEI, item="shares_out", cik=1):
    filed = date.fromordinal(period_end.toordinal() + 10)
    return {"cik": cik, "item": item, "period_end": period_end, "value": float(value), "filed": filed,
            "available_date": CAL.next_session(filed), "concept": concept, "accn": "x"}


def _flat_prices(start, end, close=100.0, splits=None):
    dates = CAL.sessions(start, end)
    cum, rows = 1.0, []
    for dt in dates:
        cum *= (splits or {}).get(dt, 1.0)
        rows.append({"date": dt, "sid": 1, "close_unadj": close / cum, "ret": 0.0, "cum_split": cum})
    return pl.DataFrame(rows)


def _mcap(prices, shares, floats=NO_FLOAT, master=MASTER1):
    return build_mcap(prices, master, pl.DataFrame(shares, schema=PIT_SCHEMA), floats, ORDER, 15, 27, 0.05, 3.0, 400)


def test_restated_balance_sheet_count_is_not_split_adjusted_twice():
    # 2:1 split on 1 Aug; the Q2 balance sheet (as of 30 Jun) is filed on 10 Aug, already restated
    # to the post-split basis (SAB Topic 4C), so its basis date is the filing date.
    split_day = d(2019, 8, 1)
    row = _count(d(2019, 6, 30), 200e6, CSO)
    row.update(filed=d(2019, 8, 10), available_date=CAL.next_session(d(2019, 8, 10)))
    mc, _ = _mcap(_flat_prices(d(2019, 8, 1), d(2019, 8, 30), splits={split_day: 2.0}), [row])
    after = mc.filter(pl.col("date") >= d(2019, 8, 13))
    assert after["shares_out"].to_list()[0] == pytest.approx(200e6)
    assert after["mcap_issuer"].to_list()[0] == pytest.approx(200e6 * 50.0)


def test_shares_split_adjusted_and_mcap_continuous_across_aapl_split():
    dates = CAL.sessions(d(2020, 8, 27), d(2020, 9, 2))
    closes = [500.04, 499.23, 129.04, 134.18, 131.4]
    rets = [None, 499.23 / 500.04 - 1, 4 * 129.04 / 499.23 - 1, 134.18 / 129.04 - 1, 131.4 / 134.18 - 1]
    prices = pl.DataFrame({"date": dates, "sid": [1] * 5, "close_unadj": closes, "ret": rets,
                           "cum_split": [1.0, 1.0, 4.0, 4.0, 4.0]})
    master = pl.DataFrame({"sid": [1], "cik": [320193], "linked_sid": [None]}, schema_overrides={"linked_sid": pl.Int64})
    shares = [{"cik": 320193, "item": "shares_out", "period_end": d(2020, 7, 17), "value": 4_275_634_000.0,
               "filed": d(2020, 7, 31), "available_date": d(2020, 8, 3), "concept": DEI, "accn": "x"}]
    mc, _ = _mcap(prices, shares, master=master)
    mc = mc.sort("date")
    assert mc["shares_out"].to_list()[1] == pytest.approx(4_275_634_000)
    assert mc["shares_out"].to_list()[2] == pytest.approx(4 * 4_275_634_000)
    assert mc["mcap_flag"].null_count() == 5                                  # no continuity break at the split
    late, _ = _mcap(prices.with_columns(pl.col("date") + pl.duration(days=500)), shares, master=master)
    assert late["mcap_issuer"].null_count() == 5                              # count older than 15 months


def test_xbrl_scale_error_is_dropped_not_used():
    ends = [d(2019, 3, 31), d(2019, 6, 30), d(2019, 9, 30), d(2019, 12, 31), d(2020, 2, 20), d(2020, 3, 31)]
    vals = [362e6, 362e6, 362e6, 362e6, 362_570_075e6, 362e6]                   # EIX-style 10^6 error
    mc, dropped = _mcap(_flat_prices(d(2020, 1, 2), d(2020, 4, 30)), [_count(e, v) for e, v in zip(ends, vals, strict=True)])
    assert dropped["value"].to_list() == [362_570_075e6]
    assert mc["mcap_issuer"].max() == pytest.approx(362e6 * 100.0)


def test_genuine_split_is_not_an_outlier():
    split_day = d(2019, 8, 1)
    counts = [_count(d(2019, 3, 31), 100e6), _count(d(2019, 6, 30), 100e6),
              _count(d(2019, 9, 30), 400e6), _count(d(2019, 12, 31), 400e6)]
    _, dropped = _mcap(_flat_prices(d(2019, 1, 2), d(2020, 1, 31), splits={split_day: 4.0}), counts)
    assert dropped.height == 0


def test_cover_page_count_outranks_a_later_weighted_average():
    counts = [_count(d(2020, 4, 20), 1000e6, DEI), _count(d(2020, 6, 30), 900e6, WAB)]
    mc, _ = _mcap(_flat_prices(d(2020, 8, 3), d(2020, 8, 7)), counts)
    assert set(mc["shares_source"].to_list()) == {DEI} and mc["shares_out"][0] == pytest.approx(1000e6)


def test_public_float_is_the_last_resort():
    floats = pl.DataFrame([_count(d(2020, 6, 30), 1e11, "dei:EntityPublicFloat", item="public_float")], schema=PIT_SCHEMA)
    mc, _ = _mcap(_flat_prices(d(2020, 6, 1), d(2020, 8, 31)), [], floats=floats)
    after = mc.filter(pl.col("date") >= d(2020, 7, 13))
    assert set(after["shares_source"].to_list()) == {"public_float"}
    assert after["shares_out"][0] == pytest.approx(1e9)                        # $100bn float / $100 price


def test_sic_first_match_wins():
    rows = read_sic_map(OVR / "industry_map.csv")
    assert map_sic(3674, rows) == "SEMICONDUCTORS" and map_sic(3672, rows) == "TECH_HARDWARE"
    assert map_sic(6798, rows) == "REAL_ESTATE" and map_sic(9999, rows) is None


def test_overrides_are_issuer_level_and_noops_are_reported():
    master = pl.DataFrame({"sid": [1, 2, 3, 4], "issuer_id": [1652044, 1652044, 1045810, -4], "sic": [7370, 7370, 3674, None]},
                          schema_overrides={"sic": pl.Int64})
    th = pl.DataFrame({"sid": [1, 2, 3, 4], "ticker": ["GOOGL", "GOOG", "NVDA", "XYZ"],
                       "start_date": [d(2015, 1, 1)] * 4, "end_date": [None] * 4}, schema_overrides={"end_date": pl.Date})
    ind, exc = assign_industries(master, th, read_sic_map(OVR / "industry_map.csv"),
                                 read_overrides(OVR / "industry_overrides.csv"), read_industries(OVR / "industries.csv"))
    got = dict(ind.select("sid", "industry").iter_rows())
    assert got[1] == got[2] == "COMMUNICATION_SERVICES" and got[3] == "SEMICONDUCTORS" and got[4] is None
    issues = dict(exc.select("ticker", "issue").iter_rows())
    assert issues.get("XYZ") == "unmapped_sic"


def _universe(n_days=30, lonely=1):
    sessions = CAL.sessions(d(2024, 1, 2), d(2024, 3, 29))[:n_days]
    sids = list(range(1, 13))
    ind = pl.DataFrame({"sid": sids, "industry": ["BANKS"] * 10 + ["INSURANCE"] * lonely + [None] * (2 - lonely)})
    master = pl.DataFrame({"sid": sids, "primary_class": [True] * 11 + [False]})
    rows = [(s, i) for s in sessions for i in sids]
    members = pl.DataFrame({"date": [r[0] for r in rows], "sid": [r[1] for r in rows], "ticker": [f"T{r[1]}" for r in rows]})
    prices = members.select("date", "sid", ret=pl.lit(0.01), price_flag=pl.lit(None, pl.String))
    mcap = members.select("date", "sid", mcap_issuer=(pl.col("sid") * 1e9).cast(pl.Float64))
    parents = dict(read_industries(OVR / "industries.csv").select("industry", "parent").iter_rows())
    cfg = CFG.model_copy(update={"universe": CFG.universe.model_copy(update={
        "estu": CFG.universe.estu.model_copy(update={"min_history_days": 3})})})
    return build_universe(members, prices, mcap, ind, master, parents, sessions, cfg), sessions


def test_exclusion_reasons_and_weights():
    (u, _), sessions = _universe()
    day = u.filter(pl.col("date") == sessions[5])
    reasons = dict(day.select("sid", "exclusion_reason").iter_rows())
    assert reasons[12] == "secondary_class" and reasons[1] is None
    assert u.filter(pl.col("date") == sessions[0])["exclusion_reason"].to_list()[0] == "short_history"
    est = day.filter(pl.col("in_estu"))
    assert est["v_reg"].sum() == pytest.approx(1.0) and est["capw"].sum() == pytest.approx(1.0)


def test_thin_industry_merges_into_parent_after_enough_breaches():
    (u, thin), sessions = _universe(n_days=30)
    ins = u.filter(pl.col("sid") == 11).sort("date")
    # INSURANCE has one name: it breaches daily and merges once breaches exceed max_breach_days (20).
    assert ins["industry"].to_list()[19] == "INSURANCE" and ins["industry"].to_list()[21] == "FINANCIAL_SERVICES"
    assert "thin_merged" in thin["issue"].to_list()
