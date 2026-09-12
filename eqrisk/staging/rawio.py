"""Readers over the immutable raw store (latest vintage of every partition)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import polars as pl

from eqrisk.sources.eodhd_px import EOD_SCHEMA
from eqrisk.store import latest_raw, raw_partitions, read_dated


def read_eod(raw_dir: Path, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    base = raw_dir / "eodhd" / "eod"
    lo = start.isoformat() if start else ""
    hi = end.isoformat() if end else "9999"
    files = [latest_raw(d) for v, d in raw_partitions(base, "date") if lo <= v <= hi]
    paths = [str(f) for f in files if f is not None]
    if not paths:
        return pl.DataFrame(schema=EOD_SCHEMA)
    return pl.read_parquet(paths).select(list(EOD_SCHEMA))


def cik_partitions(raw_dir: Path, dataset: str) -> dict[int, Path]:
    """cik -> latest vintage file for an EDGAR dataset partitioned by cik."""
    out = {}
    for v, d in raw_partitions(raw_dir / "edgar" / dataset, "cik"):
        f = latest_raw(d)
        if f is not None:
            out[int(v)] = f
    return out


def read_edgar(raw_dir: Path, dataset: str, ciks: Iterable[int] | None = None,
               concepts: set[tuple[str, str]] | None = None) -> pl.DataFrame:
    """Concatenate an EDGAR per-cik dataset, optionally keeping only some (taxonomy, concept)."""
    parts = cik_partitions(raw_dir, dataset)
    keep = set(ciks) if ciks is not None else None
    paths = [str(p) for c, p in sorted(parts.items()) if keep is None or c in keep]
    if not paths:
        return pl.DataFrame()
    lf = pl.scan_parquet(paths)
    if concepts is not None:
        taxa = pl.DataFrame({"taxonomy": [t for t, _ in concepts], "concept": [c for _, c in concepts]})
        lf = lf.join(taxa.lazy(), on=["taxonomy", "concept"], how="semi")
    return lf.collect()


def read_reference(raw_dir: Path, source: str, dataset: str, as_of: date | None = None) -> pl.DataFrame:
    df = read_dated(raw_dir / source / dataset, as_of)
    if df is None:
        raise FileNotFoundError(f"no raw {source}/{dataset}; run `eqrisk backfill --stage ingest` first")
    return df
