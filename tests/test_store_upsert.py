"""Daily upserts and monthly compaction of derived tables (blueprint §14)."""

from datetime import date

import polars as pl

from eqrisk.store import compact_month, upsert_dates, write_parquet


def _frame(days, v=0.0, n=2):
    return pl.DataFrame({"date": [d for d in days for _ in range(n)], "sid": [i for _ in days for i in range(n)],
                         "x": [v] * (n * len(days))})


def _read(t):
    return pl.scan_parquet(str(t / "**" / "*.parquet"), hive_partitioning=False).collect().sort("date", "sid")


def test_upsert_replaces_a_backfilled_date_and_stores_each_date_once(tmp_path):
    t = tmp_path / "exposures"
    days = [date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)]
    write_parquet(_frame(days), t / "year=2026" / "data.parquet")               # backfill layout
    upsert_dates(_frame([days[1]], 1.0), t, [days[1]], by_year=True)            # rerun of a backfilled date
    new = date(2026, 9, 11)
    upsert_dates(_frame([new], 2.0), t, [new], by_year=True)                     # a new session
    got = _read(t)
    assert got.group_by("date").len()["len"].to_list() == [2, 2, 2, 2]
    assert got.filter(pl.col("date") == days[1])["x"].to_list() == [1.0, 1.0]
    assert days[1] not in pl.read_parquet(t / "year=2026" / "data.parquet")["date"].to_list()
    before = (t / "year=2026" / f"day={new}.parquet").read_bytes()
    upsert_dates(_frame([new], 2.0), t, [new], by_year=True)
    assert (t / "year=2026" / f"day={new}.parquet").read_bytes() == before     # identical rerun, identical bytes


def test_compaction_merges_day_files_and_stays_idempotent(tmp_path):
    t = tmp_path / "factor_returns"
    aug = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    for d in aug:
        upsert_dates(_frame([d]), t, [d], by_year=True)
    assert compact_month(t, "2026-08") == 3
    assert [p.name for p in (t / "year=2026").iterdir()] == ["month=2026-08.parquet"]
    upsert_dates(_frame([aug[0]], 5.0), t, [aug[0]], by_year=True)               # a later rerun lands in a day file
    assert compact_month(t, "2026-08") == 1
    got = _read(t)
    assert got.height == 6 and got.filter(pl.col("date") == aug[0])["x"].to_list() == [5.0, 5.0]
    assert compact_month(t, "2026-08") == 0


def test_an_empty_frame_deletes_the_dates(tmp_path):
    days = [date(2026, 9, 9), date(2026, 9, 10)]
    for name, by_year in (("factor_cov", True), ("vra_factor", False)):
        t = tmp_path / name
        upsert_dates(_frame(days), t, days, by_year=by_year)
        upsert_dates(pl.DataFrame(), t, [days[1]], by_year=by_year)             # e.g. an empty descriptor_qa
        assert _read(t)["date"].unique().to_list() == [days[0]], name


def test_single_file_tables_are_rewritten(tmp_path):
    t = tmp_path / "regression_stats"
    days = [date(2026, 9, 9), date(2026, 9, 10)]
    upsert_dates(_frame(days, n=1), t, days, by_year=False)
    upsert_dates(_frame([days[1]], 3.0, n=1), t, [days[1]], by_year=False)
    got = pl.read_parquet(t / "regression_stats.parquet")
    assert got["date"].to_list() == days and got["x"].to_list() == [0.0, 3.0]
