from datetime import date
from pathlib import Path

import polars as pl
import pytest

from eqrisk.calendar import get_calendar
from eqrisk.config import load_config
from eqrisk.sources.eodhd_px import eod_frame
from eqrisk.staging.returns import build_prices, risk_free
from eqrisk.staging.security_master import FAR_FUTURE, FAR_PAST

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")
CAL = get_calendar("XNYS")


def test_risk_free_act360_and_carry_forward():
    sessions = CAL.sessions(date(2024, 1, 2), date(2024, 1, 9))
    dtb3 = pl.DataFrame({"date": [date(2024, 1, d) for d in (2, 3, 4, 5, 8)],
                         "value": [5.22, 5.24, 5.23, None, 5.20]})
    rf = risk_free(dtb3, sessions)
    r3 = rf.filter(pl.col("date") == date(2024, 1, 3)).row(0, named=True)
    assert r3["rf"] == pytest.approx(5.22 / 100 / 360) and not r3["filled"]
    r8 = rf.filter(pl.col("date") == date(2024, 1, 8)).row(0, named=True)      # Fri -> Mon, 5 Jan missing
    assert r8["days"] == 3 and r8["y_prev"] == 5.23 and r8["filled"]
    assert r8["rf"] == pytest.approx(5.23 / 100 * 3 / 360)
    assert rf["date"].min() == date(2024, 1, 3)                                  # first session has no prior


def _bars(code, closes, start=date(2024, 1, 2), adjusted=None, skip=()):
    sessions = CAL.sessions(start, date(2024, 3, 1))[: len(closes)]
    rows = [{"date": d.isoformat(), "open": c, "high": c, "low": c, "close": c,
             "adjusted_close": (adjusted[k] if adjusted else c), "volume": 1000}
            for k, (d, c) in enumerate(zip(sessions, closes, strict=True)) if k not in skip]
    return eod_frame(rows, code, sessions[0], sessions[-1]), sessions


def test_price_flags_and_quarantine():
    a, sessions = _bars("A", [10, 10.1, 10.2, 18.5, 18.6, 18.7, 18.8, 18.9])       # +81% with no action
    b, _ = _bars("B", [20, 20.5, 20.5, 20.5, 20.5, 20.5, 21, 22], skip=(6,))     # stale run, then a gap
    codes = pl.DataFrame({"sid": [1, 2], "code": ["A", "B"], "valid_from": [FAR_PAST] * 2, "valid_to": [FAR_FUTURE] * 2})
    rf = pl.DataFrame({"date": sessions, "rf": [0.0] * len(sessions)})
    px = build_prices(pl.concat([a, b]), codes, rf, sessions, CFG.qa)
    at = lambda sid, k: px.filter((pl.col("sid") == sid) & (pl.col("date") == sessions[k])).row(0, named=True)
    assert at(1, 3)["price_flag"] == "jump" and at(1, 3)["ret"] is None
    assert at(1, 2)["ret"] == pytest.approx(10.2 / 10.1 - 1) and at(1, 2)["price_flag"] is None
    assert {at(2, k)["price_flag"] for k in (1, 2, 3, 4, 5)} == {"stale"}
    assert at(2, 7)["price_flag"] == "gap" and at(2, 7)["ret"] is None          # prev row is two sessions back
    assert px.filter(pl.col("date") == sessions[0])["ret"].null_count() == 2    # no previous row


def test_split_adjustment_factor_and_cumulative_split():
    closes = [100.0, 102.0, 51.5, 52.0]                                      # 2:1 split on the third session
    adjusted = [50.0, 51.0, 51.5, 52.0]
    a, sessions = _bars("A", closes, adjusted=adjusted)
    codes = pl.DataFrame({"sid": [1], "code": ["A"], "valid_from": [FAR_PAST], "valid_to": [FAR_FUTURE]})
    px = build_prices(a, codes, pl.DataFrame({"date": sessions, "rf": [0.0] * 4}), sessions, CFG.qa).sort("date")
    assert px["action"].to_list()[2] == "split" and px["cum_split"].to_list() == [1.0, 1.0, 2.0, 2.0]
    assert px["adj_factor"].to_list()[-1] == 1.0
    back = (px["close_unadj"] * px["adj_factor"]).to_list()
    assert back == pytest.approx(adjusted)                                   # rebuilds the vendor's adjusted series
    assert px["ret"].to_list()[2] == pytest.approx(2 * 51.5 / 102 - 1)
