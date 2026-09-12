"""Storage primitives (blueprint §14): atomic Parquet writes, immutable raw vintages, DuckDB catalog.

Raw layout: data/raw/<source>/<dataset>/<partition>/v###.parquet. A raw write whose bytes equal
the latest vintage is a no-op; a write that differs creates the next vintage and never touches
earlier files (rule 5), so vendor revisions stay auditable.
"""

from __future__ import annotations

import io
import os
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
