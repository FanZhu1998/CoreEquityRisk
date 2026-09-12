"""Sharadar tables via the Nasdaq Data Link datatables API (blueprint §4.1, Appendix C.2).

Only the as-reported SF1 dimensions (ARQ, ARY, ART) may be used: the MR* dimensions carry later
restatements into history. `datekey` is the SEC filing date and drives availability.
Wired for when a NASDAQ_DATA_LINK_API_KEY is added; tested against recorded pages only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
from pydantic import SecretStr

from eqrisk.config import SourcesConfig
from eqrisk.sources.base import HttpClient, Source, SourceError

AS_REPORTED_DIMENSIONS = ("ARQ", "ARY", "ART")
# Filter column that bounds each table in time.
_DATE_COLUMN = {"SF1": "datekey", "ACTIONS": "date", "SEP": "date", "DAILY": "date", "SP500": "date"}


class Sharadar(Source):
    name = "sharadar"

    def __init__(self, cfg: SourcesConfig, api_key: SecretStr | None, table: str,
                 filters: dict[str, Any] | None = None, http: HttpClient | None = None) -> None:
        if api_key is None:
            raise SourceError("NASDAQ_DATA_LINK_API_KEY is not set in .env")
        self.table = table.upper()
        self.dataset = table.lower()
        self.cfg = cfg.sharadar
        self._key = api_key
        self.filters = dict(filters or {})
        if self.table == "SF1":
            dims = str(self.filters.get("dimension", ",".join(AS_REPORTED_DIMENSIONS))).split(",")
            if not set(dims) <= set(AS_REPORTED_DIMENSIONS):
                raise SourceError(f"SF1 dimensions {dims} include restated MR* data (Appendix C.2)")
            self.filters["dimension"] = ",".join(dims)
        self.http = http or HttpClient(cfg.http, per_second=5.0)

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        params: dict[str, Any] = {**self.filters, "api_key": self._key.get_secret_value(),
                                  "qopts.per_page": self.cfg.page_size}
        col = _DATE_COLUMN.get(self.table)
        if col is not None:
            params[f"{col}.gte"] = start.isoformat()
            params[f"{col}.lte"] = end.isoformat()
        frames, columns = [], None
        while True:
            j = self.http.get_json(f"{self.cfg.base_url}/{self.table}.json", params)
            dt = j["datatable"]
            columns = [c["name"] for c in dt["columns"]]
            if dt["data"]:
                frames.append(pl.DataFrame(dt["data"], schema=columns, orient="row"))
            cursor = (j.get("meta") or {}).get("next_cursor_id")
            if not cursor:
                break
            params["qopts.cursor_id"] = cursor
        if not frames:
            return pl.DataFrame(schema=columns or [])
        return pl.concat(frames, how="diagonal_relaxed")
