"""FRED series observations (blueprint §4.6). DTB3 is percent per annum, discount basis."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
from pydantic import SecretStr

from eqrisk.config import SourcesConfig
from eqrisk.sources.base import HttpClient, Source, SourceError

FRED_SCHEMA: dict[str, Any] = {"date": pl.Date, "value": pl.Float64}
# FRED writes a missing observation (e.g. a bond-market holiday) as "."
_MISSING = "."


class FredSeries(Source):
    name = "fred"

    def __init__(self, cfg: SourcesConfig, api_key: SecretStr | None, series: str,
                 http: HttpClient | None = None) -> None:
        if api_key is None:
            raise SourceError("FRED_API_KEY is not set in .env")
        self.dataset = series
        self.cfg = cfg.fred
        self._key = api_key
        self.http = http or HttpClient(cfg.http, per_second=2.0)

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        j = self.http.get_json(f"{self.cfg.base_url}/series/observations", {
            "series_id": self.dataset, "api_key": self._key.get_secret_value(), "file_type": "json",
            "observation_start": start.isoformat(), "observation_end": end.isoformat()})
        obs = j.get("observations", [])
        return pl.DataFrame({
            "date": [o["date"] for o in obs],
            "value": [None if o["value"] == _MISSING else float(o["value"]) for o in obs],
        }, schema={"date": pl.String, "value": pl.Float64}).with_columns(
            pl.col("date").str.to_date()).sort("date")
