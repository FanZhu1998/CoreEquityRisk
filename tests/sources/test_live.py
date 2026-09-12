"""Phase 2 acceptance against the real vendors. Run with: uv run pytest -m live tests/sources/test_live.py

A one-month pull for 20 symbols, including a delisted ticker (TWTR) and a renamed one (FB is
now META), lands in data/raw with row counts; a second ingest of the same window writes nothing.
"""

import shutil
from datetime import date
from pathlib import Path

import pytest

from eqrisk.config import load_project
from eqrisk.pipeline.ingest import dataset_dir, ingest_reference
from eqrisk.sources.base import IngestReport
from eqrisk.sources.eodhd_px import EodhdPrices, write_eod
from eqrisk.store import read_dated

ROOT = Path(__file__).resolve().parents[2]
CODES = ["AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "JPM", "XOM", "JNJ",
         "PG", "KO", "PEP", "WMT", "HD", "NVDA", "TSLA", "V", "TWTR", "DOW_old"]
WINDOW = (date(2017, 3, 1), date(2017, 3, 31))

pytestmark = pytest.mark.live


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    shutil.copy(ROOT / ".env", tmp_path / ".env")
    return load_project(tmp_path)


def test_one_month_twenty_symbols(project):
    px = EodhdPrices(project.sources, project.settings.eodhd_api_key, CODES)
    df = px.fetch(*WINDOW)
    first, again = IngestReport("eodhd", "eod"), IngestReport("eodhd", "eod")
    write_eod(df, project.raw_dir, first)
    write_eod(px.fetch(*WINDOW), project.raw_dir, again)
    per_code = df.group_by("code").len()
    print("\nrows per code:", dict(zip(per_code["code"], per_code["len"], strict=True)))
    assert first.partitions_written == 23 and again.partitions_written == 0      # 23 sessions in March 2017
    assert set(df["code"]) == set(CODES) and not px.missing
    assert df.filter(df["code"] == "META").height == 23                          # history moved from FB
    assert df["prev_close"].null_count() == 0


def test_reference_pulls_are_incremental(project):
    today = date.today()
    first = ingest_reference(project, today, today)
    again = ingest_reference(project, today, today)
    assert all(r.partitions_written == 0 for r in again)
    assert read_dated(dataset_dir(project, "fja05680", "components")).height > 2000
    assert {r.dataset for r in first} >= {"components", "DTB3", "daily", "listings", "company_tickers"}
