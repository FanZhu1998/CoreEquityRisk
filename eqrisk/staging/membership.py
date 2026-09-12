"""Point-in-time S&P 500 membership (blueprint §4.7).

fja05680 stores one snapshot per change date. Membership on a session is the latest snapshot
dated on or before it. A ticker's membership splits into eras wherever it leaves and later
returns, which is how a reused ticker (DOW: Dow Chemical until 2017, Dow Inc from 2019) is
told apart before identity resolution.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from eqrisk.ids import symbol_expr


def parse_components(raw: pl.DataFrame) -> pl.DataFrame:
    """Snapshots (date, 'A,AAL,...') -> long (snap_date, ticker) with Nasdaq-style tickers."""
    return (raw.select(pl.col("date").alias("snap_date"), pl.col("tickers").str.split(",").alias("ticker"))
            .explode("ticker", empty_as_null=True)
            .with_columns(symbol_expr(pl.col("ticker")))
            .filter(pl.col("ticker").str.len_chars() > 0)
            .unique(["snap_date", "ticker"])
            .sort("snap_date", "ticker"))


def daily_membership(components: pl.DataFrame, sessions: list[date]) -> pl.DataFrame:
    """(date, ticker) for every session, from the latest snapshot on or before the session."""
    snaps = components.select("snap_date").unique().sort("snap_date")
    s = pl.DataFrame({"date": sessions}, schema={"date": pl.Date}).sort("date")
    s = s.join_asof(snaps, left_on="date", right_on="snap_date", strategy="backward").drop_nulls("snap_date")
    return s.join(components, on="snap_date").select("date", "ticker").sort("date", "ticker")


def membership_eras(daily: pl.DataFrame, sessions: list[date]) -> pl.DataFrame:
    """Contiguous runs per ticker: (ticker, era, start, end, n_sessions)."""
    idx = pl.DataFrame({"date": sessions, "i": list(range(len(sessions)))},
                       schema={"date": pl.Date, "i": pl.Int64})
    d = daily.join(idx, on="date").sort("ticker", "i")
    d = d.with_columns(brk=(pl.col("i").diff().over("ticker") != 1).fill_null(True))
    d = d.with_columns(era=pl.col("brk").cast(pl.Int32).cum_sum().over("ticker"))
    return (d.group_by("ticker", "era")
            .agg(start=pl.col("date").min(), end=pl.col("date").max(), n_sessions=pl.len())
            .sort("ticker", "start"))


def daily_counts(daily: pl.DataFrame) -> pl.DataFrame:
    return daily.group_by("date").agg(n=pl.len()).sort("date")
