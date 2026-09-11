from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from eqrisk.manifest import finish, new_manifest, read_manifests, write_manifest
from eqrisk.store import latest_raw, refresh_catalog, write_parquet, write_raw


def test_raw_writes_are_idempotent_and_vintaged(tmp_path: Path):
    part = tmp_path / "raw" / "src" / "ds" / "date=2024-01-02"
    df = pl.DataFrame({"sym": ["A", "B"], "px": [1.0, 2.0]})
    w0 = write_raw(df, part)
    first = w0.path.read_bytes()
    w1 = write_raw(df, part)
    assert w0.created and not w1.created and w1.path == w0.path and w1.path.read_bytes() == first
    w2 = write_raw(df.with_columns(pl.col("px") * 2), part)
    assert w2.created and w2.vintage == 1 and latest_raw(part) == w2.path
    assert w0.path.read_bytes() == first                          # earlier vintage untouched
    assert not [p for p in part.iterdir() if p.name.endswith(".tmp")]


def test_manifest_roundtrip(tmp_path: Path):
    m = new_manifest(root=tmp_path, command="test", model_id="m1", config_hash="abc",
                     industry_scheme_version="sic20_v1", as_of=date(2024, 1, 2))
    m = finish(m.model_copy(update={"gates": {"regression": "PASS"}}), "OK")
    write_manifest(m, tmp_path / "model")
    [back] = read_manifests(tmp_path / "model")
    assert back == m and back.status == "OK"
    row = pl.read_parquet(tmp_path / "model" / "run_manifest" / f"{m.run_id}.parquet")
    assert row["gates"][0] == '{"regression": "PASS"}'


def test_catalog_views_over_parquet(tmp_path: Path):
    write_parquet(pl.DataFrame({"x": [1, 2, 3]}), tmp_path / "t" / "year=2024" / "a.parquet")
    cat = tmp_path / "catalog.duckdb"
    assert refresh_catalog(cat, {"t": tmp_path / "t", "empty": tmp_path / "none"}) == ["t"]
    con = duckdb.connect(str(cat))
    assert con.execute("select sum(x), max(year) from t").fetchone() == (6, 2024)
    con.close()
