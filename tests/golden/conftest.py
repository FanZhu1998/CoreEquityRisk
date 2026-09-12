"""Golden tests run against staged development-slice data under data/staged; skipped when absent."""

from pathlib import Path

import polars as pl
import pytest

STAGED = Path(__file__).resolve().parents[2] / "data" / "staged"


def _table(name: str) -> pl.DataFrame:
    root = STAGED / name
    if not root.exists():
        pytest.skip(f"no staged {name}; run `eqrisk backfill --stage stage`")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


@pytest.fixture(scope="session")
def staged():
    cache: dict[str, pl.DataFrame] = {}

    def get(name: str) -> pl.DataFrame:
        if name not in cache:
            cache[name] = _table(name)
        return cache[name]

    return get


@pytest.fixture(scope="session")
def sid_of(staged):
    th = staged("ticker_history")

    def lookup(ticker: str, on=None) -> int:
        rows = th.filter(pl.col("ticker") == ticker)
        if on is not None:
            rows = rows.filter((pl.col("start_date") <= on) & (pl.col("end_date").is_null() | (pl.col("end_date") >= on)))
        sids = rows["sid"].unique().to_list()
        assert len(sids) == 1, f"{ticker} on {on}: sids {sids}"
        return int(sids[0])

    return lookup
