"""Shares outstanding and market capitalization, point in time (blueprint §4.4; DECISIONS D-008, D-011).

The share count on date t is the first fresh series in priority order: manual override, cover-page
count (dei), balance-sheet count, weighted-average basic, then weighted-average diluted. Each is
taken at its latest as-of date available by t.

Split basis: a cover-page count is a dated snapshot, so it is on the basis of its own as-of date.
Financial-statement counts (balance sheet, EPS denominators) are restated for any split effected
before the statements are issued (SAB Topic 4C), so they are on the basis of their filing date.
Each count is split-adjusted from its basis date to t with the primary class's splits.

Counts more than `share_outlier_factor` away from the issuer's neighbouring counts (on a
split-free basis) are dropped as XBRL scale errors; EIX once reported 362,570,075,000,000 shares.
Issuers with no count fall back to public float / price on the float's date, flagged
`public_float`; floats are dated at the second quarter-end but filed with the next 10-K, so they
get their own maximum age. Issuer market cap = count x primary-class close.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import polars as pl

from eqrisk.staging.fundamentals_pit import PIT_SCHEMA, asof, asof_priority

_DAYS_PER_MONTH = 30.4375        # 365.25 / 12, to turn the staleness rule's months into days
OVERRIDE = "override"
PUBLIC_FLOAT = "public_float"
COVER_PAGE = "dei:EntityCommonStockSharesOutstanding"
_DATED_SNAPSHOTS = (OVERRIDE, COVER_PAGE)


def staleness_days(months: int) -> int:
    return int(round(months * _DAYS_PER_MONTH))


def override_rows(overrides: pl.DataFrame, available: Callable[[date], date]) -> pl.DataFrame:
    """shares_overrides.csv rows as `shares_out` PIT rows with concept 'override'."""
    if overrides.height == 0:
        return pl.DataFrame(schema=PIT_SCHEMA)
    return overrides.select(
        pl.col("cik").cast(pl.Int64), item=pl.lit("shares_out"), period_end=pl.col("as_of"),
        value=pl.col("shares").cast(pl.Float64), filed=pl.col("filed"),
        available_date=pl.col("filed").map_elements(available, return_dtype=pl.Date),
        concept=pl.lit(OVERRIDE), accn=pl.col("source").cast(pl.String)).select(list(PIT_SCHEMA))


def basis_date(concept: pl.Expr, period_end: pl.Expr, filed: pl.Expr) -> pl.Expr:
    """The date whose split basis a reported count is on (see module docstring)."""
    return pl.when(concept.is_in(list(_DATED_SNAPSHOTS))).then(period_end).otherwise(filed)


def _reference(prices: pl.DataFrame, master: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(sid -> cik, ref_sid) and the primary-class price series keyed by ref_sid."""
    ref = master.select("sid", "cik", ref_sid=pl.coalesce("linked_sid", "sid"))
    refpx = prices.select(ref_sid=pl.col("sid"), date=pl.col("date"), ref_close=pl.col("close_unadj"),
                          ref_cum=pl.col("cum_split")).sort("date")
    return ref, refpx


def _at(frame: pl.DataFrame, refpx: pl.DataFrame, when: str, cols: dict[str, str]) -> pl.DataFrame:
    """Attach primary-class values as of the session on or before `when` (by ref_sid)."""
    right = refpx.rename({"date": "_d", **dict(cols.items())}).select("ref_sid", "_d", *cols.values())
    keyed = frame.filter(pl.col(when).is_not_null()).sort(when)
    hit = keyed.join_asof(right, left_on=when, right_on="_d", by="ref_sid", strategy="backward",
                          check_sortedness=False).drop("_d")
    return pl.concat([hit, frame.filter(pl.col(when).is_null())], how="diagonal_relaxed")


def normalized_counts(prices: pl.DataFrame, master: pl.DataFrame, shares: pl.DataFrame,
                      staleness_months: int) -> pl.DataFrame:
    """Share-count rows usable in the price window, with `norm` = count / cumulative splits at its
    basis date (a split-free basis). Counts too old to be fresh on the first price date are dropped:
    splits before the price history are unknown, so they cannot be normalized."""
    ref, refpx = _reference(prices, master)
    first = prices["date"].min()
    assert isinstance(first, date)
    oldest = date.fromordinal(first.toordinal() - staleness_days(staleness_months))
    issuer_ref = ref.filter(pl.col("cik").is_not_null() & (pl.col("sid") == pl.col("ref_sid"))).select(
        "cik", "ref_sid").unique("cik", keep="first")
    rows = (shares.filter(pl.col("period_end") >= oldest).join(issuer_ref, on="cik", how="left")
            .with_columns(basis=basis_date(pl.col("concept"), pl.col("period_end"), pl.col("filed"))))
    rows = _at(rows, refpx, "basis", {"ref_cum": "cum_basis"})
    return rows.with_columns(norm=pl.col("value") / pl.col("cum_basis").fill_null(1.0)).drop("ref_sid")


def clean_share_counts(rows: pl.DataFrame, factor: float, window_days: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Drop counts whose split-normalized value is beyond `factor` x the median of the issuer's
    other counts dated within `window_days` (needs at least two neighbours). Returns (kept, dropped)."""
    r = rows.with_row_index("_i")
    bad: list[int] = []
    for (_cik,), g in r.group_by("cik"):
        pe = g["period_end"].cast(pl.Int32).to_numpy()
        v = g["norm"].to_numpy()
        ids = g["_i"].to_numpy()
        for j in range(len(v)):
            near = np.abs(pe - pe[j]) <= window_days
            near[j] = False
            if near.sum() >= 2:
                ref = float(np.median(v[near]))
                if ref > 0 and not (1.0 / factor <= v[j] / ref <= factor):
                    bad.append(int(ids[j]))
    drop = pl.Series("_i", bad, dtype=pl.UInt32)
    return (r.filter(~pl.col("_i").is_in(drop.implode())).drop("_i"),
            r.filter(pl.col("_i").is_in(drop.implode())).drop("_i"))


def build_mcap(prices: pl.DataFrame, master: pl.DataFrame, shares_pit: pl.DataFrame, float_pit: pl.DataFrame,
               order: list[str], staleness_months: int, float_max_age_months: int, continuity_tol: float,
               outlier_factor: float, outlier_window_days: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The §14 `mcap` table for every priced (date, sid), plus the dropped share-count outliers."""
    stale = staleness_days(staleness_months)
    ref, refpx = _reference(prices, master)
    kept, dropped = clean_share_counts(normalized_counts(prices, master, shares_pit, staleness_months),
                                       outlier_factor, outlier_window_days)
    rows = prices.select("date", "sid", "ret").join(ref, on="sid", how="left")
    keys = rows.filter(pl.col("cik").is_not_null()).select("date", "cik").unique()

    so = asof_priority(kept.select(list(PIT_SCHEMA)), keys, "shares_out", order, stale)
    pf = asof(float_pit, keys, "public_float", staleness_days(float_max_age_months)).rename(
        {"value": "float_usd", "period_end": "float_end"}).drop("filed")
    rows = (rows.join(so.rename({"value": "count", "period_end": "count_asof", "filed": "count_filed",
                                 "concept": "source"}), on=["date", "cik"], how="left")
            .join(pf, on=["date", "cik"], how="left"))
    rows = _at(rows, refpx, "float_end", {"ref_close": "close_at_float"})
    use_float = pl.col("count").is_null() & pl.col("float_usd").is_not_null() & (pl.col("close_at_float") > 0)
    rows = rows.with_columns(
        shares_raw=pl.when(use_float).then(pl.col("float_usd") / pl.col("close_at_float")).otherwise(pl.col("count")),
        shares_asof=pl.when(use_float).then(pl.col("float_end")).otherwise(pl.col("count_asof")),
        basis=pl.when(use_float).then(pl.col("float_end")).otherwise(
            basis_date(pl.col("source"), pl.col("count_asof"), pl.col("count_filed"))),
        shares_source=pl.when(use_float).then(pl.lit(PUBLIC_FLOAT)).otherwise(pl.col("source")))
    rows = _at(rows, refpx, "basis", {"ref_cum": "cum_basis"})
    rows = rows.join(refpx, on=["ref_sid", "date"], how="left")
    rows = rows.with_columns(
        shares_out=pl.col("shares_raw") * pl.col("ref_cum") / pl.col("cum_basis").fill_null(1.0))
    rows = rows.with_columns(mcap_issuer=pl.col("shares_out") * pl.col("ref_close"))
    rows = rows.sort("sid", "date").with_columns(
        jump=((pl.col("mcap_issuer").log() - pl.col("mcap_issuer").log().shift(1)).over("sid")
              - (1.0 + pl.col("ret")).log()).abs())
    out = rows.select(
        "date", "sid", "shares_out", "shares_asof",
        shares_source=pl.when(pl.col("shares_out").is_null()).then(None).otherwise(pl.col("shares_source")),
        mcap_sid=pl.col("mcap_issuer"), mcap_issuer=pl.col("mcap_issuer"),
        mcap_flag=pl.when(pl.col("jump") > continuity_tol).then(pl.lit("continuity")).otherwise(None),
    ).sort("date", "sid")
    return out, dropped
