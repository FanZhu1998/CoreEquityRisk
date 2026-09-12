"""Ken French daily factors (validation only; blueprint §11.3). Returns are converted from percent."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import polars as pl

from eqrisk.config import SourcesConfig
from eqrisk.sources.base import HttpClient, Source, SourceError

_RENAME = {"Mkt-RF": "mkt_rf", "SMB": "smb", "HML": "hml", "RF": "rf", "Mom": "mom"}
# The library writes missing values as -99.99 or -999.
_MISSING_AT_OR_BELOW = -99.0


def parse_french_csv(text: str) -> pl.DataFrame:
    """The daily section of a Ken French CSV: header row starts with ',' then YYYYMMDD rows."""
    lines = text.splitlines()
    try:
        h = next(i for i, line in enumerate(lines) if line.startswith(",") and len(line) > 1)
    except StopIteration:
        raise SourceError("no header row in Ken French file") from None
    cols = [_RENAME.get(c.strip(), c.strip().lower()) for c in lines[h].split(",")[1:]]
    dates, values = [], []
    for line in lines[h + 1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts[0]) != 8 or not parts[0].isdigit():
            break
        dates.append(date(int(parts[0][:4]), int(parts[0][4:6]), int(parts[0][6:])))
        values.append([float(p) for p in parts[1:1 + len(cols)]])
    data: dict[str, list[date] | list[float | None]] = {"date": dates}
    for j, c in enumerate(cols):
        data[c] = [None if v[j] <= _MISSING_AT_OR_BELOW else v[j] / 100.0 for v in values]
    return pl.DataFrame(data)


class FamaFrenchDaily(Source):
    name = "famafrench"
    dataset = "daily"

    def __init__(self, cfg: SourcesConfig, http: HttpClient | None = None) -> None:
        self.cfg = cfg.famafrench
        self.http = http or HttpClient(cfg.http, per_second=1.0)

    def _zip(self, fname: str) -> pl.DataFrame:
        blob = self.http.get(f"{self.cfg.base_url}/{fname}").content
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            text = z.read(z.namelist()[0]).decode("latin-1")
        return parse_french_csv(text)

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        f = self._zip(self.cfg.factors_file).join(self._zip(self.cfg.momentum_file), on="date", how="left")
        return f.filter(pl.col("date").is_between(start, end)).sort("date")
