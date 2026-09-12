"""Storage primitives (blueprint §14): atomic Parquet writes, immutable raw vintages, DuckDB catalog.

Raw layout: data/raw/<source>/<dataset>/<partition>/v###.parquet. A raw write whose bytes equal
the latest vintage is a no-op; a write that differs creates the next vintage and never touches
earlier files (rule 5), so vendor revisions stay auditable.
"""

from __future__ import annotations

import io
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import polars as pl


def parquet_bytes(df: pl.DataFrame) -> bytes:
    """Deterministic Parquet serialization (same frame -> same bytes)."""
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd", statistics=True)
    return buf.getvalue()


def atomic_write_bytes(data: bytes, path: Path) -> None:
    """Write to a temporary sibling, then rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_parquet(df: pl.DataFrame, path: Path) -> None:
    atomic_write_bytes(parquet_bytes(df), path)


def read_table(base_dir: Path, name: str) -> pl.DataFrame:
    """Read a whole derived table (one file, or year= parts) into memory."""
    root = base_dir / name
    if not root.exists():
        raise FileNotFoundError(f"table {name!r} is missing under {base_dir}; "
                                "run `eqrisk backfill` for the stage that writes it")
    return pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=False).collect()


def replace_table(df: pl.DataFrame, table_dir: Path, by_year: str | None = None) -> None:
    """Write a fresh copy beside the old one, then swap directories (derived data)."""
    tmp = table_dir.with_name(f".{table_dir.name}.{uuid.uuid4().hex[:8]}.new")
    if by_year:
        for part in df.with_columns(_y=pl.col(by_year).dt.year()).partition_by("_y", maintain_order=True):
            write_parquet(part.drop("_y"), tmp / f"year={part['_y'][0]}" / "data.parquet")
    else:
        write_parquet(df, tmp / f"{table_dir.name}.parquet")
    old = table_dir.with_name(f".{table_dir.name}.{uuid.uuid4().hex[:8]}.old")
    if table_dir.exists():
        table_dir.rename(old)
    tmp.rename(table_dir)
    shutil.rmtree(old, ignore_errors=True)


@dataclass(frozen=True)
class RawWrite:
    path: Path
    vintage: int
    created: bool          # False when identical bytes were already stored


def _vintages(partition: Path) -> list[Path]:
    return sorted(partition.glob("v[0-9][0-9][0-9].parquet"))


def write_raw(df: pl.DataFrame, partition: Path) -> RawWrite:
    data = parquet_bytes(df)
    existing = _vintages(partition)
    if existing:
        latest = existing[-1]
        n = int(latest.stem[1:])
        if latest.read_bytes() == data:
            return RawWrite(latest, n, created=False)
        n += 1
    else:
        n = 0
    path = partition / f"v{n:03d}.parquet"
    atomic_write_bytes(data, path)
    return RawWrite(path, n, created=True)


def latest_raw(partition: Path) -> Path | None:
    existing = _vintages(partition)
    return existing[-1] if existing else None


def read_latest_raw(partition: Path) -> pl.DataFrame | None:
    path = latest_raw(partition)
    return pl.read_parquet(path) if path is not None else None


def raw_partitions(dataset_dir: Path, key: str) -> list[tuple[str, Path]]:
    """(value, dir) for every `<key>=<value>` partition under `dataset_dir`, sorted by value."""
    if not dataset_dir.exists():
        return []
    out = []
    for d in dataset_dir.iterdir():
        if d.is_dir() and d.name.startswith(f"{key}="):
            out.append((d.name.split("=", 1)[1], d))
    return sorted(out)


def write_dated(df: pl.DataFrame, dataset_dir: Path, as_of: date) -> RawWrite | None:
    """Reference snapshot for `as_of`. Skipped (returns None) when its bytes equal the latest
    earlier snapshot, so unchanged reference data does not pile up one copy per day."""
    earlier = [d for v, d in raw_partitions(dataset_dir, "date") if v < as_of.isoformat()]
    if earlier:
        prev = latest_raw(earlier[-1])
        if prev is not None and prev.read_bytes() == parquet_bytes(df):
            return None
    return write_raw(df, dataset_dir / f"date={as_of.isoformat()}")


def latest_dated_dir(dataset_dir: Path, as_of: date | None = None) -> tuple[date, Path] | None:
    parts = [(v, d) for v, d in raw_partitions(dataset_dir, "date")
             if as_of is None or v <= as_of.isoformat()]
    if not parts:
        return None
    v, d = parts[-1]
    return date.fromisoformat(v), d


def read_dated(dataset_dir: Path, as_of: date | None = None) -> pl.DataFrame | None:
    """Latest reference snapshot dated on or before `as_of` (latest overall when None)."""
    hit = latest_dated_dir(dataset_dir, as_of)
    return read_latest_raw(hit[1]) if hit else None


def upsert_dates(df: pl.DataFrame, table_dir: Path, dates: list[date], by_year: bool,
                 date_col: str = "date") -> None:
    """Replace the rows of `dates` in a derived table with `df`, which holds only those dates (an
    empty frame, even one without columns, deletes them).

    Year-partitioned tables get one file per date, `year=YYYY/day=YYYY-MM-DD.parquet`, until
    `compact_month` merges them. A date already stored elsewhere (the backfill's `data.parquet`,
    a month file) is removed from that file first, so every date lives in exactly one file.
    Single-file tables are rewritten whole. Rows within a date keep the order of `df`.
    """
    days = sorted(set(dates))
    if not by_year:
        path = table_dir / f"{table_dir.name}.parquet"
        keep = pl.read_parquet(path).filter(~pl.col(date_col).is_in(days)) if path.exists() else None
        parts = [keep, df] if keep is not None else [df]
        write_parquet(pl.concat(parts, how="diagonal_relaxed").sort(date_col, maintain_order=True), path)
        return
    for d in days:
        ydir = table_dir / f"year={d.year}"
        target = ydir / f"day={d.isoformat()}.parquet"
        for f in sorted(ydir.glob("*.parquet")) if ydir.exists() else []:
            if f != target and (pl.read_parquet(f, columns=[date_col])[date_col] == d).any():
                rest = pl.read_parquet(f).filter(pl.col(date_col) != d)
                if rest.height:
                    write_parquet(rest, f)
                else:
                    f.unlink()
        part = df.filter(pl.col(date_col) == d) if date_col in df.columns else df   # no column: no rows
        if part.height:
            write_parquet(part, target)
        elif target.exists():
            target.unlink()


def compact_month(table_dir: Path, month: str, date_col: str = "date") -> int:
    """Merge a year-partitioned table's day files for `month` (YYYY-MM) into
    `year=YYYY/month=YYYY-MM.parquet`; returns the number of day files merged. Idempotent: rows of
    those days already in the month file (an interrupted earlier run) are replaced, not doubled."""
    ydir = table_dir / f"year={month[:4]}"
    day_files = sorted(ydir.glob(f"day={month}-*.parquet")) if ydir.exists() else []
    if not day_files:
        return 0
    target = ydir / f"month={month}.parquet"
    fresh = pl.concat([pl.read_parquet(f) for f in day_files], how="diagonal_relaxed")
    days = fresh[date_col].unique().to_list()
    parts = [pl.read_parquet(target).filter(~pl.col(date_col).is_in(days))] if target.exists() else []
    write_parquet(pl.concat([*parts, fresh], how="diagonal_relaxed").sort(date_col, maintain_order=True), target)
    for f in day_files:
        f.unlink()
    return len(day_files)


def init_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS eqrisk_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    finally:
        con.close()


def refresh_catalog(path: Path, tables: dict[str, Path]) -> list[str]:
    """(Re)create one DuckDB view per table over its Parquet tree. Returns the views created."""
    con = duckdb.connect(str(path))
    created = []
    try:
        for name, root in sorted(tables.items()):
            if not root.exists() or not any(root.rglob("*.parquet")):
                continue
            glob = (root / "**" / "*.parquet").as_posix()
            con.execute(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM "
                f"read_parquet('{glob}', hive_partitioning = true, union_by_name = true)"
            )
            created.append(name)
    finally:
        con.close()
    return created
