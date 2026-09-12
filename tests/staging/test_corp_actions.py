from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.config import load_config
from eqrisk.staging.corp_actions import (
    BAD_PRICE,
    DISTRIBUTION,
    DIVIDEND,
    NONE,
    SPLIT,
    flag_special_dividends,
    implied_actions,
    unconfirmed_splits,
)

QA = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml").qa.corp_actions


def _one(close, prev_close, adj, prev_adj):
    df = pl.DataFrame({"close": [close], "prev_close": [prev_close], "adjusted_close": [adj],
                       "prev_adjusted_close": [prev_adj]}, schema={k: pl.Float64 for k in
                       ("close", "prev_close", "adjusted_close", "prev_adjusted_close")})
    return implied_actions(df, QA).row(0, named=True)


def test_pure_split_aapl_2020_08_31():
    r = _one(129.04, 499.23, 125.0575, 120.9557)            # real EODHD rows
    assert r["action"] == SPLIT and r["split_ratio"] == 4.0 and r["dividend"] == 0.0
    assert r["ret_total"] == pytest.approx(4 * 129.04 / 499.23 - 1)


def test_dividend_aapl_2023_11_10_recovers_the_declared_amount():
    r = _one(186.4, 182.41, 184.1549, 179.9758)
    assert r["action"] == DIVIDEND and r["dividend"] == pytest.approx(0.24, abs=5e-4)
    assert r["ret_total"] == pytest.approx((186.4 + r["dividend"]) / 182.41 - 1)


def test_vendor_rounding_is_not_a_dividend():
    r = _one(182.89, 181.82, 180.4494, 179.3937)            # AAPL 2023-11-08, no event
    assert r["action"] == NONE and r["dividend"] == 0.0


def test_reverse_split_one_for_ten():
    r = _one(50.0, 5.1, 50.0, 51.0)
    assert r["action"] == SPLIT and r["split_ratio"] == pytest.approx(0.1)
    assert r["ret_total"] == pytest.approx(0.1 * 50.0 / 5.1 - 1)


def test_spin_off_is_a_distribution_not_a_five_for_four_split():
    # Price falls 20% on the ex-date while the adjusted series falls 1%: g = 1.2375, near 5/4 but not
    # within the 0.5% split tolerance (D-007).
    r = _one(80.0, 100.0, 99.0, 100.0)
    assert r["action"] == DISTRIBUTION and r["split_ratio"] == 1.0
    assert r["dividend"] == pytest.approx(100.0 * (1 - 0.8 / 0.99))
    assert r["ret_total"] == pytest.approx((80.0 + r["dividend"]) / 100.0 - 1)


def test_odd_ratio_spin_off_is_not_a_28_for_3_split():
    r = _one(5.04, 46.84, 1.0, 1.0)                           # Aimco 2020: g = 9.2937
    assert r["action"] == DISTRIBUTION and r["split_ratio"] == 1.0


def test_share_counts_decide_between_split_and_spin_off():
    d0 = date(2021, 11, 8)
    prices = pl.DataFrame({"sid": [1, 2, 3, 4, 5], "code": ["ADS", "BFB", "TSLA", "RST", "COL"], "date": [d0] * 5,
                           "action": [SPLIT] * 5, "split_ratio": [1.25, 1.25, 5.0, 2.0, 0.1]})
    issuer_of = pl.DataFrame({"sid": [1, 2, 3, 4, 5], "cik": [11, 22, 33, 44, 55]})
    q3, q4 = date(2021, 9, 30), date(2021, 12, 31)
    shares = pl.DataFrame({
        "cik": [11, 11, 22, 22, 33, 33, 44, 44, 55, 55],
        "period_end": [q3, q4, q3, q4, q3, q4, date(2021, 10, 20), q3, q3, q4],
        "filed": ([date(2021, 10, 28), date(2022, 2, 25)] * 3 + [date(2021, 10, 28), date(2021, 11, 15)]
                  + [date(2021, 10, 28), date(2022, 2, 25)]),
        # RST: the cover count (as of 20 Oct) is pre-split; the Q3 balance sheet filed after the
        # split is restated 2:1 although it is dated 30 Sep. Ordering by filing date sees the split.
        # COL: a vendor close quoted at a tenth of its value reads as 1:10; the count did not move.
        "value": [49.8e6, 49.9e6, 384e6, 480e6, 1e9, 5e9, 100e6, 200e6, 130e6, 131e6]})
    out = unconfirmed_splits(prices, issuer_of, shares, QA)
    assert out.rows() == [("ADS", d0), ("COL", d0)]           # BFB, TSLA and RST moved by their ratios


def test_forced_distribution_overrides_a_clean_ratio():
    eod = pl.DataFrame({"code": ["ADS"], "date": [date(2021, 11, 8)], "close": [80.0], "prev_close": [100.0],
                        "adjusted_close": [100.0], "prev_adjusted_close": [100.0]})
    assert implied_actions(eod, QA).row(0, named=True)["action"] == SPLIT
    forced = implied_actions(eod, QA, pl.DataFrame({"code": ["ADS"], "date": [date(2021, 11, 8)]})).row(0, named=True)
    assert forced["action"] == DISTRIBUTION and forced["split_ratio"] == 1.0
    assert forced["dividend"] == pytest.approx(20.0)


def test_missing_adjusted_close_falls_back_to_price_return():
    r = _one(10.5, 10.0, None, None)
    assert r["action"] == NONE and r["ret_total"] == pytest.approx(0.05)


def test_bad_prices_and_first_rows_have_no_return():
    assert _one(0.0, 10.0, 0.0, 10.0)["action"] == BAD_PRICE
    assert _one(0.0, 10.0, 0.0, 10.0)["ret_total"] is None
    assert _one(10.0, None, 10.0, None)["ret_total"] is None


def test_special_dividends():
    df = pl.DataFrame({"sid": [1] * 6, "i": [0, 63, 126, 189, 200, 252],
                       "action": [DIVIDEND] * 5 + [DISTRIBUTION], "dividend": [0.5, 0.5, 0.5, 0.5, 3.0, 5.0]})
    assert flag_special_dividends(df, QA).to_list() == [False, False, False, False, True, True]
