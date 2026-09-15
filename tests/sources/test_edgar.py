from datetime import date

import polars as pl
import pytest

from eqrisk.sources.base import EntitlementError, SourceError
from eqrisk.sources.edgar import Edgar, parse_cik_lookup, parse_daily_index

UA = "EQRisk test test@example.com"


def test_user_agent_is_required(sources):
    with pytest.raises(SourceError, match="SEC_USER_AGENT"):
        Edgar(sources, None)


def test_company_tickers_sends_user_agent_and_normalizes(mock_http, sources, fixture_bytes):
    calls = []
    http = mock_http([("/files/company_tickers.json", fixture_bytes("sec_company_tickers.json"))],
                     calls=calls, headers={"User-Agent": UA})
    t = Edgar(sources, UA, http=http).company_tickers()
    assert calls[0].headers["user-agent"] == UA
    assert "BRK.B" in t["ticker"].to_list()
    assert t.filter(pl.col("ticker") == "AAPL")["cik"].item() == 320193


def test_companyfacts_keeps_dei_and_us_gaap_with_types(mock_http, sources, fixture_bytes):
    http = mock_http([("/companyfacts/CIK0000320193.json", fixture_bytes("sec_companyfacts_0000320193.json"))])
    df = Edgar(sources, UA, http=http).companyfacts(320193)
    assert set(df["taxonomy"].unique()) == {"dei", "us-gaap"}                 # ffd dropped
    shares = df.filter(pl.col("concept") == "EntityCommonStockSharesOutstanding")
    assert shares.height == 5 and shares["start"].null_count() == 5           # instants
    assert df.schema["filed"] == pl.Date and df.schema["val"] == pl.Float64
    ni = df.filter(pl.col("concept") == "NetIncomeLoss")
    assert ni["start"].null_count() == 0 and (ni["cik"] == 320193).all()


def test_companyfacts_404_is_empty_frame(mock_http, sources):
    df = Edgar(sources, UA, http=mock_http([])).companyfacts(1)
    assert df.height == 0 and "concept" in df.columns


def test_submissions_company_row_and_filings(mock_http, sources, fixture_bytes):
    http = mock_http([("/submissions/CIK0000320193.json", fixture_bytes("sec_submissions_0000320193.json"))])
    company, filings = Edgar(sources, UA, http=http).submissions(320193)
    row = company.row(0, named=True)
    assert row["sic"] == 3571 and row["tickers"] == "AAPL" and "APPLE COMPUTER INC" in row["former_names"]
    assert filings.height == 6 and filings.schema["filing_date"] == pl.Date


def test_daily_index_parse(fixture_bytes):
    idx = parse_daily_index(fixture_bytes("sec_master_20240205.idx").decode("latin-1"))
    assert idx.height == 8 and {"10-K", "10-Q"} <= set(idx["form"].to_list())
    assert (idx["filed"] == date(2024, 2, 5)).all() and idx.schema["cik"] == pl.Int64


def test_daily_index_missing_day_is_empty(mock_http, sources):
    assert Edgar(sources, UA, http=mock_http([])).daily_index(date(2024, 2, 3)).height == 0


def test_daily_index_for_a_weekend_is_empty_although_sec_answers_403(mock_http, sources):
    """The 2026-09-15 failure: SEC's storage answers 403 AccessDenied for Saturday's missing index."""
    body = b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'
    http = mock_http([("/master.20260912.idx", (403, body))])
    assert Edgar(sources, UA, http=http).daily_index(date(2026, 9, 12)).height == 0


def test_daily_index_refused_by_sec_still_fails(mock_http, sources):
    """A rate limit or a missing User-Agent is an HTML page, never mistaken for a day without filings."""
    http = mock_http([("/master.", (403, b"<html><body>Request Rate Threshold Exceeded</body></html>"))])
    with pytest.raises(EntitlementError):
        Edgar(sources, UA, http=http).daily_index(date(2026, 9, 14))


def test_cik_lookup_parse(fixture_bytes):
    df = parse_cik_lookup(fixture_bytes("sec_cik_lookup.txt").decode("latin-1"))
    assert df.filter(pl.col("name") == "TWITTER, INC.")["cik"].item() == 1418091
    assert (df["name"].str.len_chars() > 0).all()                             # blank names dropped
