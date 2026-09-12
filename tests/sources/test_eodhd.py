from datetime import date

import httpx
import polars as pl
from pydantic import SecretStr

from eqrisk.sources.base import IngestReport
from eqrisk.sources.eodhd_px import EodhdListings, EodhdPrices, write_eod
from eqrisk.store import latest_raw

KEY = SecretStr("test-key")
SPLIT_WINDOW = (date(2020, 8, 27), date(2020, 9, 2))


def test_rows_carry_previous_values_from_the_same_response(mock_http, sources, fixture_bytes):
    http = mock_http([("/eod/AAPL.US", fixture_bytes("eodhd_eod_AAPL_split.json"))])
    df = EodhdPrices(sources, KEY, ["AAPL"], http=http).fetch(*SPLIT_WINDOW)
    assert df["date"].to_list() == [date(2020, 8, d) for d in (27, 28, 31)] + [date(2020, 9, 1), date(2020, 9, 2)]
    assert df["prev_date"][0] == date(2020, 8, 26)            # from the buffer before `start`
    row = df.filter(pl.col("date") == date(2020, 8, 31)).row(0, named=True)
    k = (row["adjusted_close"] / row["prev_adjusted_close"]) / (row["close"] / row["prev_close"])
    assert abs(k - 4.0) < 1e-4                                  # AAPL 4:1 split is visible in one row


def test_missing_code_is_recorded_not_raised(mock_http, sources):
    px = EodhdPrices(sources, KEY, ["NOPE"], http=mock_http([("/eod/", (404, b""))]))
    assert px.fetch(*SPLIT_WINDOW).height == 0 and px.missing == ["NOPE"]


def test_reingesting_a_date_is_byte_identical(tmp_path, mock_http, sources, fixture_bytes):
    http = mock_http([("/eod/AAPL.US", fixture_bytes("eodhd_eod_AAPL_split.json"))])
    df = EodhdPrices(sources, KEY, ["AAPL"], http=http).fetch(*SPLIT_WINDOW)
    first = IngestReport("eodhd", "eod")
    write_eod(df, tmp_path, first)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")}
    again = IngestReport("eodhd", "eod")
    write_eod(df, tmp_path, again)
    assert (first.partitions_written, again.partitions_written, again.partitions_unchanged) == (5, 0, 5)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")} == snapshot


def test_new_code_for_a_stored_date_becomes_a_new_vintage(tmp_path, mock_http, sources, fixture_bytes):
    http = mock_http([("/eod/AAPL.US", fixture_bytes("eodhd_eod_AAPL_split.json"))])
    df = EodhdPrices(sources, KEY, ["AAPL"], http=http).fetch(*SPLIT_WINDOW)
    write_eod(df, tmp_path, IngestReport("eodhd", "eod"))
    part = tmp_path / "eodhd" / "eod" / "date=2020-08-31"
    v0 = latest_raw(part).read_bytes()
    extra = df.filter(pl.col("date") == date(2020, 8, 31)).with_columns(code=pl.lit("XYZ"))
    write_eod(extra, tmp_path, IngestReport("eodhd", "eod"))
    assert latest_raw(part).name == "v001.parquet" and (part / "v000.parquet").read_bytes() == v0
    assert sorted(pl.read_parquet(latest_raw(part))["code"].to_list()) == ["AAPL", "XYZ"]


def test_listings_mark_delisted_and_superseded_codes(mock_http, sources, fixture_bytes):
    def route(req: httpx.Request) -> httpx.Response:
        name = "eodhd_listings_delisted.json" if req.url.params.get("delisted") == "1" else "eodhd_listings_current.json"
        return httpx.Response(200, content=fixture_bytes(name))

    df = EodhdListings(sources, KEY, http=mock_http([("/exchange-symbol-list/US", route)])).fetch(date(2024, 1, 1), date(2024, 1, 1))
    assert df.filter(pl.col("code") == "DOW_old")["delisted"].item() is True
    assert df.filter(pl.col("code") == "AAPL")["delisted"].item() is False
    assert {"code", "name", "type", "isin", "delisted"} <= set(df.columns)
