from datetime import date

import numpy as np
import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.model.descriptors import _CumSplit, growth_events, market_excess_return
from eqrisk.model.panel import Panel
from eqrisk.staging.fundamentals_pit import PIT_SCHEMA

CAL = get_calendar("XNYS")


def _panel(T=5, N=3):
    P = Panel(CAL.sessions(date(2024, 1, 2), date(2024, 1, 31))[:T], np.arange(1, N + 1), ["A"])
    return P


def test_market_return_uses_prior_day_estu_caps():
    P = _panel()
    P["in_estu"] = np.ones((5, 3), bool)
    P["in_estu"][1, 2] = False                             # name 3 left the ESTU at the close of day 1
    P["capw"] = np.tile([0.5, 0.3, 0.2], (5, 1))
    P["ret_excess"] = np.tile([0.01, 0.02, 0.10], (5, 1))
    m = market_excess_return(P)
    assert np.isnan(m[0])
    assert m[1] == pytest.approx(0.5 * 0.01 + 0.3 * 0.02 + 0.2 * 0.10)
    assert m[2] == pytest.approx((0.5 * 0.01 + 0.3 * 0.02) / 0.8)      # day-1 ESTU, renormalized


def test_growth_events_put_eps_on_one_split_basis():
    # EPS grows 10% a year; a 2:1 split on 2022-06-01 halves every EPS filed afterwards.
    P = Panel(CAL.sessions(date(2021, 1, 4), date(2023, 12, 29)), np.array([1]), ["A"])
    cum = np.where(np.array([d >= date(2022, 6, 1) for d in P.dates]), 2.0, 1.0)
    P["cum_split"] = cum[:, None]
    splits = _CumSplit(P, {7: 1})
    rows = []
    for k, year in enumerate(range(2017, 2023)):
        filed = date(year + 1, 2, 15)
        eps = 1.0 * 1.1 ** k / (2.0 if filed >= date(2022, 6, 1) else 1.0)
        rows.append({"cik": 7, "item": "eps_fy", "period_end": date(year, 12, 31), "value": eps, "filed": filed,
                     "available_date": CAL.next_session(filed), "concept": "c", "accn": "a"})
    ev = growth_events(pl.DataFrame(rows, schema=PIT_SCHEMA), "eps_fy", splits, years=5, min_years=3)
    last = ev.sort("available_date").row(-1, named=True)
    raw = np.array([1.1 ** k for k in range(1, 6)])
    t = np.arange(1, 6)
    expected = np.polyfit(t, raw, 1)[0] / raw.mean()
    assert last["value"] == pytest.approx(expected)          # no break at the split
    assert last["latest_fy"] == date(2022, 12, 31)
