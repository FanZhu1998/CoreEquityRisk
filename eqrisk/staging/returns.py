"""Total returns, price QA and the risk-free rate (blueprint §4.3, §4.6).

A missing or quarantined price gives a missing return, never zero (§18). Quarantined:
non-positive prices, and moves beyond `qa.prices.max_abs_return_no_action` with no corporate
action on file. A return spanning more than one session (the vendor skipped days) is also set
missing (DECISIONS D-007), since it is not a one-day return. Volume is the shares traded that
day: the vendor's split adjustment up to its pull date is taken back out (DECISIONS D-017).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from eqrisk.config import QaCfg
from eqrisk.staging.corp_actions import (
    DIVIDEND,
    NONE,
    UNEXPLAINED,
    flag_special_dividends,
    implied_actions,
    vendor_split_basis,
)

# act/360 money-market convention for DTB3 (§4.6); the config's daycount key selects it.
_ACT360_DAYS = 360.0

PRICE_FLAG_ORDER = ("nonpositive", "jump", "gap", "stale", "action_unexplained")


def risk_free(dtb3: pl.DataFrame, sessions: list[date]) -> pl.DataFrame:
    """r_f,t = y_{t-1}/100 * (calendar days from the previous session) / 360.

    y_{t-1} is the latest DTB3 observation dated on or before the previous session; `filled`
    marks sessions whose yield was carried forward from an earlier observation.
    """
    obs = dtb3.drop_nulls("value").sort("date").rename({"date": "obs_date", "value": "y"})
    s = (pl.DataFrame({"date": sessions}, schema={"date": pl.Date})
         .with_columns(prev=pl.col("date").shift(1)).drop_nulls("prev").sort("prev"))
    s = s.join_asof(obs, left_on="prev", right_on="obs_date", strategy="backward")
    return s.select(
        "date",
        y_prev=pl.col("y"),
        days=(pl.col("date") - pl.col("prev")).dt.total_days(),
        filled=pl.col("obs_date") != pl.col("prev"),
    ).with_columns(rf=pl.col("y_prev") / 100.0 * pl.col("days") / _ACT360_DAYS).sort("date")


def build_prices(eod: pl.DataFrame, codes: pl.DataFrame, rf: pl.DataFrame, sessions: list[date],
                 qa: QaCfg, force_distribution: pl.DataFrame | None = None) -> pl.DataFrame:
    """The §14 `prices` table: one row per (date, sid) the vendor priced."""
    sess = (pl.DataFrame({"date": sessions}, schema={"date": pl.Date})
            .with_row_index("i").with_columns(pl.col("i").cast(pl.Int64), prev_session=pl.col("date").shift(1)))
    ea = implied_actions(eod, qa.corp_actions, force_distribution)
    basis = vendor_split_basis(ea, qa.corp_actions.split_volume_window, qa.corp_actions.split_volume_margin)
    ea = ea.hstack(basis).with_columns(volume=pl.col("volume") / pl.col("volume_split"))
    df = (ea.join(codes, on="code")
          .filter(pl.col("date").is_between(pl.col("valid_from"), pl.col("valid_to")))
          .join(sess, on="date", how="inner")
          .unique(["sid", "date"], keep="first")
          .sort("sid", "date"))

    run_id = (pl.col("close") != pl.col("close").shift(1)).fill_null(True).cum_sum().over("sid")
    df = df.with_columns(run=run_id).with_columns(run_len=pl.len().over("sid", "run"))
    flags: dict[str, pl.Expr] = {
        "nonpositive": pl.col("close").is_null() | (pl.col("close") <= 0),
        "jump": (pl.col("ret_total").abs() > qa.prices.max_abs_return_no_action)
                & pl.col("action").is_in([NONE, DIVIDEND, UNEXPLAINED]),
        "gap": pl.col("prev_date").is_not_null() & (pl.col("prev_date") != pl.col("prev_session")),
        "stale": pl.col("run_len") >= qa.prices.stale_run_days,
        "action_unexplained": pl.col("action") == UNEXPLAINED,
    }
    flag_expr: pl.Expr = pl.lit(None, pl.String)
    for name in reversed(PRICE_FLAG_ORDER):
        flag_expr = pl.when(flags[name]).then(pl.lit(name)).otherwise(flag_expr)
    df = df.with_columns(price_flag=flag_expr)
    df = df.with_columns(ret=pl.when(pl.col("price_flag").is_in(["nonpositive", "jump", "gap"]))
                         .then(None).otherwise(pl.col("ret_total")))
    df = df.with_columns(flag_special_dividends(df, qa.corp_actions))

    # Back-adjustment factor: AP_t = P_t * adj_factor_t, with adj_factor = 1 on the last row.
    step = (1.0 / pl.col("split_ratio")) * (1.0 - pl.col("dividend") * pl.col("split_ratio") / pl.col("prev_close"))
    df = df.with_columns(step=pl.when(pl.col("prev_close") > 0).then(step).otherwise(1.0))
    df = df.with_columns(
        adj_factor=pl.col("step").reverse().cum_prod().reverse().shift(-1, fill_value=1.0).over("sid"),
        cum_split=pl.col("split_ratio").cum_prod().over("sid"),
    )
    df = df.join(rf.select("date", "rf"), on="date", how="left").with_columns(
        ret_excess=pl.col("ret") - pl.col("rf"))
    cols: list[Any] = ["date", "sid", "code", pl.col("close").alias("close_unadj"), "volume", "adj_factor",
                       "ret", "ret_excess", "rf", "price_flag", "split_ratio", "dividend", "special_dividend",
                       "action", "cum_split", "volume_basis_err"]
    return df.select(cols).sort("date", "sid")
