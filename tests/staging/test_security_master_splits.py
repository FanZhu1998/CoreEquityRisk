"""Era splitting, override spans, superseded-code preference, current-name disambiguation, predecessors."""

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.sources.edgar import COMPANY_SCHEMA
from eqrisk.staging.security_master import issuer_ciks, read_cik_links, resolve

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config(ROOT / "configs" / "model_us_lc.yaml").security_master
CAL = get_calendar("XNYS")
S = CAL.sessions(date(2016, 1, 4), date(2016, 6, 30))
L = len(S) - 1


def _era(t, i, j):
    return {"ticker": t, "era": 1, "start": S[i], "end": S[j], "n_sessions": j - i + 1}


ERAS = pl.DataFrame([_era("IR", 0, L), _era("TT", 80, L), _era("FOXA", 0, L), _era("XL", 0, 99), _era("VIAB", 0, 99)])
C = [("IR", "IR", "Ingersoll Rand Inc", False), ("TT", "TT", "Trane Technologies plc", False),
     ("FOXA", "FOXA", "Fox Corp Class A", False), ("XL", "XL", "XL Fleet Corp", True),
     ("XL", "XL_old", "XL Group Ltd", True), ("VIAB", "VIAB", "Viacom Inc", True)]
CANDS = pl.DataFrame({"ticker": [c[0] for c in C], "code": [c[1] for c in C], "name": [c[2] for c in C],
                      "type": ["Common Stock"] * len(C), "exchange": ["NYSE"] * len(C), "delisted": [c[3] for c in C]})
PRICED = {"IR": (60, L), "TT": (0, L), "FOXA": (50, L), "TFCFA": (0, 55), "XL": (0, L), "XL_old": (0, 99), "VIAB": (0, 99)}
CODE_DATES = pl.DataFrame([{"code": c, "date": S[k]} for c, (i, j) in PRICED.items() for k in range(i, j + 1)])
CURRENT = pl.DataFrame({"ticker": ["IR", "TT", "FOXA"], "security": [""] * 3, "cik": [1699150, 1466258, 1754301],
                        "date_added": [None] * 3}, schema_overrides={"date_added": pl.String})
SEC = pl.DataFrame({"cik": [1699150, 1466258, 1754301], "ticker": ["IR", "TT", "FOXA"], "title": ["Ingersoll Rand Inc.", "Trane Technologies plc", "Fox Corp"]})
LOOKUP = pl.DataFrame({"name": ["XL GROUP LTD", "VIACOM INC", "VIACOM INC.", "XL FLEET CORP."],
                       "cik": [875159, 813828, 1339947, 1772720]})


def _company(cik, name, former=()):
    return {"cik": cik, "name": name, "entity_type": "operating", "sic": 1000, "sic_description": None, "tickers": "",
            "exchanges": "", "fiscal_year_end": "1231", "state_of_incorporation": None,
            "former_names": json.dumps([{"name": f} for f in former])}


COMPANIES = pl.DataFrame([_company(1699150, "Ingersoll Rand Inc."), _company(1466258, "Trane Technologies plc"),
                          _company(1754301, "Fox Corp"), _company(875159, "XL GROUP LTD"),
                          _company(813828, "Paramount Global", ["VIACOM INC", "CBS CORP"]),
                          _company(1339947, "Viacom Inc."), _company(1308161, "TWENTY-FIRST CENTURY FOX, INC.")],
                         schema=COMPANY_SCHEMA)
SPANS = pl.DataFrame({"cik": [1699150, 1466258, 1754301, 875159, 813828, 1339947, 1308161],
                      "first_filed": [date(2010, 1, 1)] * 7, "last_filed": [date(2026, 1, 1)] * 7, "n_filings": [40] * 7})
OVERRIDES = pl.DataFrame({"ticker": ["FOXA"], "start_date": [S[0]], "end_date": [S[49]], "eodhd_code": ["TFCFA"],
                          "cik": [1308161], "share_class": [None], "note": [""]},
                         schema_overrides={"share_class": pl.String})


@pytest.fixture(scope="module")
def sm():
    return resolve(eras=ERAS, cands=CANDS, code_dates=CODE_DATES, cal=CAL, last_session=S[-1], current=CURRENT,
                   sec_tickers=SEC, cik_lookup=LOOKUP, companies=COMPANIES, filing_spans=SPANS, overrides=OVERRIDES,
                   dollar_volume=CODE_DATES.with_columns(dv=pl.lit(1.0)), cfg=CFG,
                   code_names={"TFCFA": "Twenty-First Century Fox Inc"})


def _rows(sm, t):
    return sm.ticker_history.filter(pl.col("ticker") == t).sort("start_date").to_dicts()


def test_hidden_rename_splits_ir_where_tt_entered(sm):
    old, new = _rows(sm, "IR")
    tt = _rows(sm, "TT")[0]
    assert (old["start_date"], old["end_date"]) == (S[0], S[79]) and new["start_date"] == S[80]
    assert old["eodhd_code"] == "TT" and old["sid"] == tt["sid"] and old["cik"] == 1466258
    assert new["eodhd_code"] == "IR" and new["cik"] == 1699150 and new["sid"] != old["sid"]


def test_override_span_splits_foxa_into_two_companies(sm):
    old, new = _rows(sm, "FOXA")
    assert (old["end_date"], old["eodhd_code"], old["cik"], old["code_method"]) == (S[49], "TFCFA", 1308161, "override")
    assert (new["start_date"], new["eodhd_code"], new["cik"]) == (S[50], "FOXA", 1754301)
    assert old["sid"] != new["sid"]


def test_ended_era_prefers_the_superseded_code(sm):
    xl = _rows(sm, "XL")[0]
    assert xl["eodhd_code"] == "XL_old" and xl["cik"] == 875159       # not XL Fleet, today's holder of "XL"


def test_current_names_outrank_former_names(sm):
    v = _rows(sm, "VIAB")[0]
    assert v["cik"] == 1339947 and v["cik_method"].startswith("name_key+current_name")


def test_no_exceptions_left(sm):
    assert sm.exceptions.height == 0, sm.exceptions


def test_predecessor_found_by_name_and_manual_links_kept():
    master = pl.DataFrame({"sid": [1, 2], "cik": [2012383, 1841666], "first_date": [date(2016, 1, 4)] * 2})
    th = pl.DataFrame({"cik": [2012383, 1841666], "vendor_name": ["BlackRock Inc", "APA Corporation"]})
    companies = pl.DataFrame([_company(2012383, "BlackRock, Inc."), _company(1364742, "BlackRock Inc."),
                              _company(1841666, "APA Corp"), _company(6769, "APACHE CORP")], schema=COMPANY_SCHEMA)
    spans = pl.DataFrame({"cik": [2012383, 1364742, 1841666, 6769],
                          "first_filed": [date(2024, 11, 6), date(2009, 8, 7), date(2021, 3, 1), date(2009, 5, 1)],
                          "last_filed": [date(2026, 8, 6), date(2024, 8, 6), date(2026, 8, 1), date(2021, 2, 20)],
                          "n_filings": [8, 60, 20, 50]})
    lookup = pl.DataFrame({"name": ["BLACKROCK INC.", "APACHE CORP"], "cik": [1364742, 6769]})
    links, exc = issuer_ciks(master, th, companies, lookup, spans,
                             read_cik_links(ROOT / "configs" / "overrides" / "cik_links.csv"), CFG)
    got = {(r["issuer_id"], r["cik"]): r["role"] for r in links.iter_rows(named=True)}
    assert got[(2012383, 1364742)] == "predecessor"                # found by name
    assert got[(1841666, 6769)] == "manual"                       # Apache -> APA only by the override file
    assert exc.height == 0
