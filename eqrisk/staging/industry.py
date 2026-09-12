"""Industry classification (blueprint §4.8, Appendix B): SIC -> 20 starter industries, first match
wins, plus issuer-level overrides keyed by (ticker, as-of date). Single membership only."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from eqrisk.ids import symbol_expr
from eqrisk.staging.security_master import EXCEPTION_SCHEMA, FAR_FUTURE


def read_industries(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, comment_prefix="#")


def read_sic_map(path: Path) -> list[tuple[int, int, str]]:
    df = pl.read_csv(path, comment_prefix="#", schema_overrides={"sic_lo": pl.Int64, "sic_hi": pl.Int64})
    return [(r["sic_lo"], r["sic_hi"], r["industry"]) for r in df.iter_rows(named=True)]


def read_overrides(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, comment_prefix="#", try_parse_dates=True)
    return df.with_columns(symbol_expr(pl.col("ticker")))


def map_sic(sic: int | None, rows: list[tuple[int, int, str]]) -> str | None:
    if sic is None:
        return None
    for lo, hi, ind in rows:
        if lo <= sic <= hi:
            return ind
    return None


def assign_industries(master: pl.DataFrame, ticker_history: pl.DataFrame, sic_rows: list[tuple[int, int, str]],
                      overrides: pl.DataFrame, industries: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(sid, industry, sic, source) for every security, plus the exception report."""
    valid = set(industries["industry"].to_list())
    bad = [r for _, _, r in sic_rows if r not in valid]
    if bad:
        raise ValueError(f"industry_map.csv names unknown industries: {sorted(set(bad))}")
    exc: list[dict[str, Any]] = []
    by_sid = {r["sid"]: {"sid": r["sid"], "issuer_id": r["issuer_id"], "sic": r["sic"],
                         "industry": map_sic(r["sic"], sic_rows), "source": "sic"}
              for r in master.iter_rows(named=True)}
    th = ticker_history.with_columns(end=pl.col("end_date").fill_null(FAR_FUTURE))
    for o in overrides.iter_rows(named=True):
        if o["industry"] not in valid:
            raise ValueError(f"industry_overrides.csv: unknown industry {o['industry']!r}")
        as_of: date = o["as_of"]
        hits = th.filter((pl.col("ticker") == o["ticker"]) & (pl.col("start_date") <= as_of) & (pl.col("end") >= as_of))
        if hits.height == 0:
            hits = th.filter(pl.col("ticker") == o["ticker"]).sort("start_date").tail(1)
        if hits.height == 0:
            exc.append({"ticker": o["ticker"], "start": as_of, "end": as_of, "issue": "override_unresolved",
                        "detail": f"{o['industry']} ({o['reason']})"})
            continue
        issuer = by_sid[hits["sid"][0]]["issuer_id"]
        for row in by_sid.values():
            if row["issuer_id"] != issuer:
                continue
            if row["industry"] == o["industry"]:
                exc.append({"ticker": o["ticker"], "start": as_of, "end": as_of, "issue": "override_noop",
                            "detail": f"SIC {row['sic']} already maps to {o['industry']}"})
            row.update(industry=o["industry"], source="override")
    for row in by_sid.values():
        if row["industry"] is None:
            tick = ticker_history.filter(pl.col("sid") == row["sid"])["ticker"].to_list()
            exc.append({"ticker": "|".join(tick), "start": None, "end": None, "issue": "unmapped_sic",
                        "detail": f"sid {row['sid']} SIC {row['sic']}"})
    out = pl.DataFrame(list(by_sid.values()), schema={"sid": pl.Int64, "issuer_id": pl.Int64, "sic": pl.Int64,
                                                      "industry": pl.String, "source": pl.String})
    return out.drop("issuer_id"), pl.DataFrame(exc, schema=EXCEPTION_SCHEMA).unique()
