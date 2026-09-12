from datetime import date

import polars as pl
import pytest
from pydantic import SecretStr

from eqrisk.sources.famafrench import FamaFrenchDaily
from eqrisk.sources.fred import FredSeries
from eqrisk.sources.sp500_membership import Fja05680

UPDATED = "S&P 500 Historical Components & Changes (Updated).csv"


def test_fred_missing_marker_becomes_null(mock_http, sources, fixture_bytes):
    http = mock_http([("/series/observations", fixture_bytes("fred_DTB3.json"))])
    df = FredSeries(sources, SecretStr("k"), "DTB3", http=http).fetch(date(2024, 1, 1), date(2024, 1, 12))
    assert df["date"][0] == date(2024, 1, 1) and df["value"][0] is None
    assert df["value"][1] == pytest.approx(5.22)


def _fja(mock_http, fixture_bytes):
    return mock_http([
        ("/repos/fja05680/sp500/contents", fixture_bytes("fja_contents.json")),
        ("Historical Components", fixture_bytes("fja_components.csv")),
        ("/sp500.csv", fixture_bytes("fja_sp500.csv")),
        ("sp500_ticker_start_end", fixture_bytes("fja_spells.csv")),
    ])


def test_fja_prefers_the_updated_components_file(mock_http, sources, fixture_bytes):
    fja = Fja05680(sources, http=_fja(mock_http, fixture_bytes))
    assert fja.components_file() == UPDATED
    comps = fja.fetch(date(1900, 1, 1), date(2030, 1, 1))
    assert comps.height == 4 and comps["date"].dtype == pl.Date
    assert fja.fetch(date(1900, 1, 1), date(2018, 1, 1)).height == 2     # snapshots after `end` dropped


def test_fja_current_list_keeps_cik_not_gics(mock_http, sources, fixture_bytes):
    cur = Fja05680(sources, http=_fja(mock_http, fixture_bytes)).fetch_current()
    assert cur.filter(pl.col("ticker") == "AAPL")["cik"].item() == 320193
    assert not any("GICS" in c for c in cur.columns)


def test_fja_spells_show_reused_tickers(mock_http, sources, fixture_bytes):
    spells = Fja05680(sources, http=_fja(mock_http, fixture_bytes)).fetch_spells()
    dow = spells.filter(pl.col("ticker") == "DOW").sort("start_date")
    assert dow.height == 2 and dow["end_date"][0] == date(2017, 9, 1) and dow["end_date"][1] is None


def test_french_factors_are_decimal_returns(mock_http, sources, fixture_bytes):
    http = mock_http([("F-F_Research_Data_Factors_daily", fixture_bytes("ff_factors_daily.zip")),
                      ("F-F_Momentum_Factor_daily", fixture_bytes("ff_momentum_daily.zip"))])
    df = FamaFrenchDaily(sources, http=http).fetch(date(2026, 1, 1), date(2026, 12, 31))
    assert {"date", "mkt_rf", "smb", "hml", "rf", "mom"} <= set(df.columns)
    last = df.row(-1, named=True)
    assert last["date"] == date(2026, 7, 31)
    assert last["mkt_rf"] == pytest.approx(0.0068) and last["mom"] == pytest.approx(-0.0086)
