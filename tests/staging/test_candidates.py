from pathlib import Path

import polars as pl

from eqrisk.sources.edgar import parse_cik_lookup
from eqrisk.staging.security_master import candidate_ciks, candidate_codes

FIX = Path(__file__).resolve().parents[1] / "fixtures"

LISTINGS = pl.DataFrame({
    "code": ["AAPL", "DOW", "META", "FB", "MSFT", "BRK-B", "DOW_old", "TWTR"],
    "name": ["Apple Inc", "Dow Inc", "Meta Platforms Inc", "Some Buffer ETF", "Microsoft Corporation",
             "Berkshire Hathaway Inc", "Dow Chemical Company", "Twitter Inc"],
    "type": ["Common Stock"] * 3 + ["ETF"] + ["Common Stock"] * 4,
    "exchange": ["NASDAQ"] * 8,
    "delisted": [False] * 6 + [True, True],
})


def test_candidate_codes_include_superseded_holders_and_fallbacks():
    c = candidate_codes(["AAPL", "DOW", "FB", "META", "DWDP", "TWTR", "BRK.B"], LISTINGS)
    codes = lambda t: sorted(c.filter(pl.col("ticker") == t)["code"].to_list())
    assert codes("DOW") == ["DOW", "DOW_old"]
    assert codes("BRK.B") == ["BRK-B"]
    assert codes("DWDP") == ["DWDP"] and c.filter(pl.col("ticker") == "DWDP")["name"].item() is None
    assert codes("FB") == ["FB"]            # an ETF today; coverage decides in staging


def test_candidate_ciks_from_lists_and_names():
    lookup = parse_cik_lookup((FIX / "sec_cik_lookup.txt").read_text(encoding="latin-1"))
    current = pl.DataFrame({"ticker": ["AAPL"], "security": ["Apple Inc."], "cik": [320193], "date_added": [None]},
                           schema_overrides={"date_added": pl.String})
    sec = pl.DataFrame({"cik": [1326801], "ticker": ["META"], "title": ["Meta Platforms, Inc."]})
    out = candidate_ciks(["AAPL", "META", "TWTR"], current, sec, lookup,
                         ["Twitter Inc", "Celgene Corporation", "Dow Chemical Company"])
    got = dict(zip(out["cik"].to_list(), out["via"].to_list(), strict=True))
    assert got[320193] == "fja_current" and got[1326801] == "sec_ticker"
    assert {1418091, 816284, 29915} <= set(got)            # delisted names matched to SEC names
    assert 1772696 not in got                               # "17 ICE BOX/SWEETER TWITTER, LLC"
