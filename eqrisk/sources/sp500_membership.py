"""S&P 500 point-in-time membership from fja05680/sp500 (MIT; blueprint §4.7).

Three files are used:
* "S&P 500 Historical Components & Changes (Updated).csv": one row per change date, with the
  full constituent list as a comma-separated string.
* "sp500.csv": the current list, used only for its CIK column. Its GICS columns are dropped: GICS
  is licensed and the model classifies industries from SIC instead (D4).
* "sp500_ticker_start_end.csv": membership spells, used as a cross-check.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from urllib.parse import quote

import polars as pl

from eqrisk.config import SourcesConfig
from eqrisk.sources.base import HttpClient, Source, SourceError

_UPDATED_SUFFIX = " (Updated).csv"
_GITHUB_HEADERS = {"User-Agent": "eqrisk", "Accept": "application/vnd.github+json"}


class Fja05680(Source):
    name = "fja05680"
    dataset = "components"

    def __init__(self, cfg: SourcesConfig, http: HttpClient | None = None) -> None:
        self.cfg = cfg.fja05680
        self.http = http or HttpClient(cfg.http, per_second=2.0, headers=_GITHUB_HEADERS)

    def list_files(self) -> list[str]:
        return sorted(item["name"] for item in self.http.get_json(self.cfg.contents_api))

    def components_file(self, names: list[str] | None = None) -> str:
        names = names if names is not None else self.list_files()
        cands = [n for n in names if n.startswith(self.cfg.file_prefix) and n.endswith(".csv")]
        preferred = self.cfg.file_prefix + _UPDATED_SUFFIX
        if preferred in cands:
            return preferred
        if not cands:
            raise SourceError("no S&P 500 components file in fja05680/sp500")
        return cands[-1]

    def _csv(self, name: str) -> list[dict[str, str]]:
        text = self.http.get(f"{self.cfg.raw_base}/{quote(name)}").text
        return list(csv.DictReader(io.StringIO(text)))

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        """Every snapshot dated on or before `end` (earlier ones are needed to know membership at `start`)."""
        rows = self._csv(self.components_file())
        df = pl.DataFrame({"date": [r["date"] for r in rows], "tickers": [r["tickers"] for r in rows]})
        return df.with_columns(pl.col("date").str.to_date()).filter(pl.col("date") <= end).sort("date")

    def fetch_current(self) -> pl.DataFrame:
        rows = self._csv("sp500.csv")
        return pl.DataFrame({
            "ticker": [r["Symbol"] for r in rows],
            "security": [r["Security"] for r in rows],
            "cik": [int(r["CIK"]) if r.get("CIK", "").strip().isdigit() else None for r in rows],
            "date_added": [r.get("Date added") or None for r in rows],
        }, schema={"ticker": pl.String, "security": pl.String, "cik": pl.Int64, "date_added": pl.String})

    def fetch_spells(self) -> pl.DataFrame:
        rows = self._csv("sp500_ticker_start_end.csv")
        return pl.DataFrame({
            "ticker": [r["ticker"] for r in rows],
            "start_date": [r["start_date"] or None for r in rows],
            "end_date": [r["end_date"] or None for r in rows],
        }, schema={"ticker": pl.String, "start_date": pl.String, "end_date": pl.String}).with_columns(
            pl.col("start_date").str.to_date(), pl.col("end_date").str.to_date())
