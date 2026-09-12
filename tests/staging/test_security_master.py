"""Identity resolution on a synthetic index that exercises every rule in DECISIONS D-008."""

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.sources.edgar import COMPANY_SCHEMA
from eqrisk.staging.security_master import FAR_FUTURE, FAR_PAST, read_ticker_map, resolve

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config(ROOT / "configs" / "model_us_lc.yaml").security_master
CAL = get_calendar("XNYS")
S = CAL.sessions(date(2016, 1, 4), date(2016, 6, 30))
LAST = S[-1]


def _era(ticker, era, i, j):
    return {"ticker": ticker, "era": era, "start": S[i], "end": S[j], "n_sessions": j - i + 1}


ERAS = pl.DataFrame([
    _era("AAA", 1, 0, len(S) - 1),
    _era("FB", 1, 0, 59), _era("META", 1, 60, len(S) - 1),        # rename, vendor moved history to META
    _era("DOW", 1, 0, 39), _era("DOW", 2, 80, len(S) - 1),         # reused ticker
    _era("GOOGL", 1, 0, len(S) - 1), _era("GOOG", 1, 0, len(S) - 1),
    _era("EQT", 1, 0, 29), _era("EQT", 2, 90, len(S) - 1),         # same company re-added
    _era("TWTR", 1, 0, 99),                                        # delisted, resolved by name only
    _era("XXX", 1, 0, len(S) - 1),                                 # vendor has nothing
])
C = [  # ticker, code, name, type, delisted
    ("AAA", "AAA", "Alpha Inc", "Common Stock", False),
    ("FB", "FB", "Some Buffer ETF", "ETF", False),
    ("META", "META", "Meta Platforms Inc", "Common Stock", False),
    ("DOW", "DOW", "Dow Inc", "Common Stock", False),
    ("DOW", "DOW_old", "Dow Chemical Company", "Common Stock", True),
    ("GOOGL", "GOOGL", "Alphabet Inc Class A", "Common Stock", False),
    ("GOOG", "GOOG", "Alphabet Inc Class C", "Common Stock", False),
    ("EQT", "EQT", "EQT Corporation", "Common Stock", False),
    ("TWTR", "TWTR", "Twitter Inc", "Common Stock", True),
    ("XXX", "XXX", None, None, None),
]
CANDS = pl.DataFrame({"ticker": [c[0] for c in C], "code": [c[1] for c in C], "name": [c[2] for c in C],
                      "type": [c[3] for c in C], "exchange": ["NYSE"] * len(C), "delisted": [c[4] for c in C]},
                     schema_overrides={"delisted": pl.Boolean})
PRICED = {"AAA": (0, len(S) - 1), "FB": (100, len(S) - 1), "META": (0, len(S) - 1), "DOW": (70, len(S) - 1),
          "DOW_old": (0, 39), "GOOGL": (0, len(S) - 1), "GOOG": (0, len(S) - 1), "EQT": (0, len(S) - 1),
          "TWTR": (0, 99)}
CODE_DATES = pl.DataFrame([{"code": c, "date": S[k]} for c, (i, j) in PRICED.items() for k in range(i, j + 1)])
CURRENT = pl.DataFrame({"ticker": ["AAA", "META", "DOW", "GOOGL", "GOOG", "EQT"], "security": [""] * 6,
                        "cik": [111, 1326801, 1751788, 1652044, 1652044, 33213], "date_added": [None] * 6},
                       schema_overrides={"date_added": pl.String})
SEC = pl.DataFrame({"cik": [111, 1326801, 1751788, 1652044, 1652044, 33213, 999],
                    "ticker": ["AAA", "META", "DOW", "GOOGL", "GOOG", "EQT", "FB"],
                    "title": ["Alpha Inc", "Meta Platforms, Inc.", "Dow Inc.", "Alphabet Inc.", "Alphabet Inc.",
                              "EQT Corp", "ProShares Trust II"]})
LOOKUP = pl.DataFrame({"name": ["TWITTER, INC.", "DOW CHEMICAL CO /DE/", "META PLATFORMS, INC.", "EQT CORP"],
                       "cik": [1418091, 29915, 1326801, 33213]})
COMPANIES = pl.DataFrame([
    {"cik": c, "name": n, "entity_type": "operating", "sic": sic, "sic_description": None, "tickers": t,
     "exchanges": "", "fiscal_year_end": "1231", "state_of_incorporation": None, "former_names": json.dumps(fn)}
    for c, n, sic, t, fn in [
        (111, "Alpha Inc", 2834, "AAA", []), (1326801, "Meta Platforms, Inc.", 7370, "META", [{"name": "FACEBOOK INC"}]),
        (1751788, "Dow Inc.", 2821, "DOW", []), (29915, "DOW CHEMICAL CO /DE/", 2821, "", []),
        (1652044, "Alphabet Inc.", 7370, "GOOGL|GOOG", []), (33213, "EQT Corp", 1311, "EQT", []),
        (1418091, "Twitter, Inc.", 7370, "", [])]], schema=COMPANY_SCHEMA)
SPANS = pl.DataFrame({"cik": [111, 1326801, 1751788, 29915, 1652044, 33213, 1418091],
                      "first_filed": [date(2010, 1, 1), date(2010, 1, 1), date(2019, 3, 1), date(2010, 1, 1),
                                      date(2010, 1, 1), date(2010, 1, 1), date(2010, 1, 1)],
                      "last_filed": [date(2026, 1, 1)] * 7, "n_filings": [40] * 7})
DV = CODE_DATES.with_columns(dv=pl.when(pl.col("code") == "GOOGL").then(2.0).otherwise(1.0))


def _resolve():
    return resolve(eras=ERAS, cands=CANDS, code_dates=CODE_DATES, cal=CAL, last_session=LAST, current=CURRENT,
                   sec_tickers=SEC, cik_lookup=LOOKUP, companies=COMPANIES, filing_spans=SPANS,
                   overrides=read_ticker_map(ROOT / "does-not-exist.csv"), dollar_volume=DV, cfg=CFG)


@pytest.fixture(scope="module")
def sm():
    return _resolve()


def _th(sm, ticker, era_start=None):
    rows = sm.ticker_history.filter(pl.col("ticker") == ticker)
    if era_start is not None:
        rows = rows.filter(pl.col("start_date") == era_start)
    return rows.row(0, named=True)


def test_rename_links_to_the_ticker_that_entered_next(sm):
    fb, meta = _th(sm, "FB"), _th(sm, "META")
    assert fb["sid"] == meta["sid"] and fb["eodhd_code"] == "META" and fb["code_method"] == "renamed_to:META"
    assert fb["cik"] == 1326801 and fb["cik_method"] == "rename"      # not the ETF that holds "FB" today
    codes = sm.codes.filter(pl.col("sid") == meta["sid"])
    assert codes.rows() == [(meta["sid"], "META", FAR_PAST, FAR_FUTURE)]


def test_reused_ticker_becomes_two_securities(sm):
    old, new = _th(sm, "DOW", S[0]), _th(sm, "DOW", S[80])
    assert old["sid"] != new["sid"]
    assert (old["eodhd_code"], old["cik"]) == ("DOW_old", 29915)
    assert (new["eodhd_code"], new["cik"], new["cik_method"]) == ("DOW", 1751788, "fja_current")


def test_share_classes_one_issuer_primary_by_liquidity(sm):
    a, c = _th(sm, "GOOGL"), _th(sm, "GOOG")
    m = sm.master.filter(pl.col("sid").is_in([a["sid"], c["sid"]])).sort("sid")
    assert m["issuer_id"].n_unique() == 1
    ma = m.filter(pl.col("sid") == a["sid"]).row(0, named=True)
    mc = m.filter(pl.col("sid") == c["sid"]).row(0, named=True)
    assert ma["primary_class"] and not mc["primary_class"] and mc["linked_sid"] == a["sid"]


def test_readded_company_is_one_security(sm):
    e1, e2 = _th(sm, "EQT", S[0]), _th(sm, "EQT", S[90])
    assert e1["sid"] == e2["sid"] and e1["cik"] == e2["cik"] == 33213
    assert sm.master.filter(pl.col("sid") == e1["sid"])["n_eras"].item() == 2


def test_delisted_company_resolved_by_name(sm):
    t = _th(sm, "TWTR")
    assert (t["cik"], t["cik_method"], t["end_date"]) == (1418091, "name_key", S[99])


def test_unpriced_unknown_ticker_is_reported_not_dropped(sm):
    x = _th(sm, "XXX")
    assert x["cik"] is None and x["sid"] is not None
    issues = set(sm.exceptions.filter(pl.col("ticker") == "XXX")["issue"].to_list())
    assert issues == {"unpriced_era", "no_cik"}
    assert sm.exceptions.filter(pl.col("ticker") != "XXX").height == 0


def test_resolution_is_deterministic(sm):
    again = _resolve()
    for name in ("master", "ticker_history", "codes", "exceptions"):
        assert getattr(again, name).equals(getattr(sm, name))
