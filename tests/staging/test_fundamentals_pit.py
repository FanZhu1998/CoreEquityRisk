"""Point-in-time fundamentals: TTM from year-to-date columns, restatements, chain priority."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import ConceptsConfig, _read_yaml, load_config, load_sources
from eqrisk.staging.fundamentals_pit import asof, company_pit

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config(ROOT / "configs" / "model_us_lc.yaml")
CONCEPTS = ConceptsConfig.model_validate(_read_yaml(ROOT / "configs" / "concepts.yaml"))
FORMS = load_sources(ROOT / "configs").edgar.forms
CAL = get_calendar("XNYS")


def F(concept, start, end, val, filed, form="10-Q", unit="USD", tax="us-gaap", cik=1):
    return {"cik": cik, "taxonomy": tax, "concept": concept, "unit": unit, "start": start, "end": end, "val": val,
            "accn": f"a-{filed}", "fy": None, "fp": None, "form": form, "filed": filed, "frame": None}


def _pit(rows):
    df = pl.DataFrame(rows, schema_overrides={"start": pl.Date, "end": pl.Date, "filed": pl.Date, "val": pl.Float64,
                                              "fy": pl.Int64, "fp": pl.String, "frame": pl.String})
    return company_pit(df, CONCEPTS, CFG.fundamentals, FORMS, CAL.next_session)


d = date
NI = [
    F("NetIncomeLoss", d(2022, 1, 1), d(2022, 12, 31), 100, d(2023, 2, 10), "10-K"),
    F("NetIncomeLoss", d(2023, 1, 1), d(2023, 3, 31), 30, d(2023, 5, 5)),
    F("NetIncomeLoss", d(2022, 1, 1), d(2022, 3, 31), 20, d(2023, 5, 5)),            # comparative
    F("NetIncomeLoss", d(2023, 4, 1), d(2023, 6, 30), 25, d(2023, 8, 4)),
    F("NetIncomeLoss", d(2023, 1, 1), d(2023, 6, 30), 55, d(2023, 8, 4)),            # YTD
    F("NetIncomeLoss", d(2022, 1, 1), d(2022, 6, 30), 45, d(2023, 8, 4)),            # prior-year YTD
    F("NetIncomeLoss", d(2022, 1, 1), d(2022, 12, 31), 110, d(2023, 9, 15), "10-K/A"),  # restatement
    F("NetIncomeLoss", d(2023, 1, 1), d(2023, 6, 30), 999, d(2023, 8, 1), "8-K"),     # not a periodic form
]
OTHER = [
    F("StockholdersEquity", None, d(2022, 12, 31), 480, d(2023, 2, 10), "10-K"),
    F("StockholdersEquity", None, d(2023, 3, 31), 500, d(2023, 5, 5)),
    F("Revenues", d(2021, 1, 1), d(2021, 12, 31), 1000, d(2022, 2, 10), "10-K"),
    F("RevenueFromContractWithCustomerExcludingAssessedTax", d(2021, 1, 1), d(2021, 12, 31), 990, d(2023, 2, 10), "10-K"),
    F("RevenueFromContractWithCustomerExcludingAssessedTax", d(2022, 1, 1), d(2022, 12, 31), 1100, d(2023, 2, 10), "10-K"),
    F("EntityCommonStockSharesOutstanding", None, d(2023, 4, 20), 1000, d(2023, 5, 5), unit="shares", tax="dei"),
]


@pytest.fixture(scope="module")
def pit():
    return _pit(NI + OTHER)


def _item(pit, item):
    return pit.filter(pl.col("item") == item).sort("period_end", "filed").select("period_end", "value", "filed").rows()


def test_ttm_from_ytd_columns_and_restatement_from_its_own_filing_date(pit):
    assert _item(pit, "ni_ttm") == [
        (d(2022, 12, 31), 100.0, d(2023, 2, 10)),
        (d(2022, 12, 31), 110.0, d(2023, 9, 15)),
        (d(2023, 3, 31), 110.0, d(2023, 5, 5)),            # 100 + 30 - 20
        (d(2023, 3, 31), 120.0, d(2023, 9, 15)),           # restated FY flows through
        (d(2023, 6, 30), 110.0, d(2023, 8, 4)),            # 100 + 55 - 45 (the 8-K 999 is ignored)
        (d(2023, 6, 30), 120.0, d(2023, 9, 15)),
    ]


def test_available_the_session_after_filing(pit):
    row = pit.filter((pl.col("item") == "ni_ttm") & (pl.col("period_end") == d(2023, 3, 31))).row(0, named=True)
    assert row["filed"] == d(2023, 5, 5) and row["available_date"] == d(2023, 5, 8)    # Friday -> Monday


def test_asof_never_uses_a_value_before_it_is_available(pit):
    dates = pl.DataFrame({"date": [d(2023, 5, 5), d(2023, 5, 8), d(2023, 8, 10), d(2023, 9, 20), d(2025, 1, 2)],
                          "cik": [1] * 5})
    got = asof(pit, dates, "ni_ttm", max_staleness_days=456).sort("date")["value"].to_list()
    assert got == [100.0, 110.0, 110.0, 120.0, None]            # filing day: old value; 2025: stale


def test_chain_priority_is_per_period(pit):
    assert _item(pit, "revenue_fy") == [(d(2021, 12, 31), 1000.0, d(2022, 2, 10)),     # Revenues outranks RFC
                                        (d(2022, 12, 31), 1100.0, d(2023, 2, 10))]
    concept = pit.filter((pl.col("item") == "revenue_fy") & (pl.col("period_end") == d(2022, 12, 31)))["concept"]
    assert concept.item() == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def test_instants_and_dei_cover_page_shares(pit):
    assert _item(pit, "book_equity")[-1] == (d(2023, 3, 31), 500.0, d(2023, 5, 5))
    assert _item(pit, "shares_out") == [(d(2023, 4, 20), 1000.0, d(2023, 5, 5))]


def test_zero_share_counts_are_not_counts():
    rows = [F("CommonStockSharesOutstanding", None, d(2019, 12, 31), 0, d(2020, 2, 20), "10-K", unit="shares"),
            F("CommonStockSharesOutstanding", None, d(2020, 3, 31), 295e6, d(2020, 5, 11), unit="shares")]
    s = _pit(rows).filter(pl.col("item") == "shares_out")
    assert s.select("period_end", "value", "concept").rows() == [
        (d(2020, 3, 31), 295e6, "us-gaap:CommonStockSharesOutstanding")]


def test_bank_revenue_fallback_sum():
    rows = [F("InterestAndDividendIncomeOperating", d(2022, 1, 1), d(2022, 12, 31), 70, d(2023, 2, 20), "10-K", cik=2),
            F("NoninterestIncome", d(2022, 1, 1), d(2022, 12, 31), 30, d(2023, 2, 20), "10-K", cik=2)]
    rev = _pit(rows).filter(pl.col("item") == "revenue_fy").row(-1, named=True)
    assert rev["value"] == 100.0 and "+" in rev["concept"]
