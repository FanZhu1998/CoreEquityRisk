from datetime import date
from pathlib import Path

import polars as pl

from eqrisk.calendar import get_calendar
from eqrisk.staging.membership import daily_counts, daily_membership, membership_eras, parse_components

FIX = Path(__file__).resolve().parents[1] / "fixtures"
SESSIONS = get_calendar("XNYS").sessions(date(2016, 1, 4), date(2022, 6, 15))


def _daily():
    comps = parse_components(pl.read_csv(FIX / "fja_components.csv", try_parse_dates=True))
    return daily_membership(comps, SESSIONS)


def test_parse_strips_end_of_life_suffix_and_normalizes_classes():
    raw = pl.DataFrame({"date": [date(2000, 1, 3)], "tickers": ["AAL-199702,BRK.B,BF-B, MSFT"]})
    assert parse_components(raw)["ticker"].to_list() == ["AAL", "BF.B", "BRK.B", "MSFT"]


def test_membership_uses_latest_snapshot_on_or_before_each_session():
    daily = _daily()
    on = lambda d: sorted(daily.filter(pl.col("date") == d)["ticker"].to_list())
    assert on(date(2017, 8, 31)) == ["AAPL", "DOW", "FB", "MSFT"]
    assert on(date(2017, 9, 1)) == ["AAPL", "DWDP", "FB", "MSFT"]
    assert on(date(2022, 6, 9)) == ["AAPL", "DOW", "DWDP", "META", "MSFT"]
    assert set(daily_counts(daily)["n"].to_list()) == {4, 5}


def test_reused_ticker_splits_into_eras():
    eras = membership_eras(_daily(), SESSIONS)
    dow = eras.filter(pl.col("ticker") == "DOW").sort("start")
    assert dow.select("start", "end").rows() == [(date(2016, 1, 4), date(2017, 8, 31)),
                                                  (date(2019, 4, 2), date(2022, 6, 15))]
    fb = eras.filter(pl.col("ticker") == "FB").row(0, named=True)
    meta = eras.filter(pl.col("ticker") == "META").row(0, named=True)
    assert fb["end"] == date(2022, 6, 8) and meta["start"] == date(2022, 6, 9)
