"""EODHD end-of-day prices and symbol lists (DECISIONS D-001).

`close` is unadjusted; `adjusted_close` is split- and dividend-adjusted and is back-adjusted by
the vendor whenever a new dividend or split occurs. A return must therefore never divide two
adjusted closes from different pulls. Each stored row carries `prev_date`, `prev_close` and
`prev_adjusted_close` taken from the *same* response, so staging can compute every return, and
the implied corporate action behind it, from one row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import SecretStr

from eqrisk.config import SourcesConfig
from eqrisk.sources.base import HttpClient, IngestReport, NotFoundError, Source, SourceError
from eqrisk.store import read_latest_raw, write_raw

EOD_SCHEMA: dict[str, Any] = {
    "code": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "adjusted_close": pl.Float64, "volume": pl.Float64,
    "prev_date": pl.Date, "prev_close": pl.Float64, "prev_adjusted_close": pl.Float64,
}
LISTING_SCHEMA: dict[str, Any] = {
    "code": pl.String, "name": pl.String, "country": pl.String, "exchange": pl.String,
    "currency": pl.String, "type": pl.String, "isin": pl.String, "delisted": pl.Boolean,
}
_VENDOR_FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume")


def empty_eod() -> pl.DataFrame:
    return pl.DataFrame(schema=EOD_SCHEMA)


def eod_frame(rows: list[dict[str, Any]], code: str, start: date, end: date) -> pl.DataFrame:
    """Vendor rows for one code -> EOD_SCHEMA, with same-response previous values, clipped to [start, end]."""
    data: dict[str, list[Any]] = {"date": [r.get("date") for r in rows]}
    for k in _VENDOR_FIELDS:
        data[k] = [r.get(k) for r in rows]
    df = pl.DataFrame(data, schema={"date": pl.String, **{k: pl.Float64 for k in _VENDOR_FIELDS}},
                      strict=False)
    df = df.with_columns(pl.col("date").str.to_date()).unique("date", keep="last").sort("date")
    df = df.with_columns(
        prev_date=pl.col("date").shift(1),
        prev_close=pl.col("close").shift(1),
        prev_adjusted_close=pl.col("adjusted_close").shift(1),
        code=pl.lit(code),
    )
    return df.filter(pl.col("date").is_between(start, end)).select(list(EOD_SCHEMA))


class EodhdPrices(Source):
    name = "eodhd"
    dataset = "eod"

    def __init__(self, cfg: SourcesConfig, api_key: SecretStr | None, codes: Sequence[str],
                 http: HttpClient | None = None) -> None:
        if api_key is None:
            raise SourceError("EODHD_API_KEY is not set in .env")
        self.cfg = cfg.eodhd
        self._key = api_key
        self.codes = sorted(set(codes))
        self.http = http or HttpClient(cfg.http, cfg.eodhd.requests_per_second)
        self.missing: list[str] = []

    def fetch_code(self, code: str, start: date, end: date) -> pl.DataFrame:
        params = {"api_token": self._key.get_secret_value(), "fmt": "json", "period": "d",
                  "order": "a", "to": end.isoformat(),
                  "from": (start - timedelta(days=self.cfg.prev_buffer_days)).isoformat()}
        try:
            rows = self.http.get_json(f"{self.cfg.base_url}/eod/{code}.{self.cfg.exchange}", params)
        except NotFoundError:
            rows = []
        if not isinstance(rows, list) or not rows:
            self.missing.append(code)
            return empty_eod()
        return eod_frame(rows, code, start, end)

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        frames = [self.fetch_code(c, start, end) for c in self.codes]
        frames = [f for f in frames if f.height]
        return pl.concat(frames) if frames else empty_eod()


class EodhdListings(Source):
    """Current and delisted US symbol lists; `delisted` tells them apart."""

    name = "eodhd"
    dataset = "listings"

    def __init__(self, cfg: SourcesConfig, api_key: SecretStr | None, http: HttpClient | None = None) -> None:
        if api_key is None:
            raise SourceError("EODHD_API_KEY is not set in .env")
        self.cfg = cfg.eodhd
        self._key = api_key
        self.http = http or HttpClient(cfg.http, cfg.eodhd.requests_per_second)

    def _list(self, delisted: bool) -> pl.DataFrame:
        params: dict[str, Any] = {"api_token": self._key.get_secret_value(), "fmt": "json"}
        if delisted:
            params["delisted"] = 1
        rows = self.http.get_json(f"{self.cfg.base_url}/exchange-symbol-list/{self.cfg.exchange}", params)
        data = {k: [r.get(v) for r in rows] for k, v in (
            ("code", "Code"), ("name", "Name"), ("country", "Country"), ("exchange", "Exchange"),
            ("currency", "Currency"), ("type", "Type"), ("isin", "Isin"))}
        return pl.DataFrame(data, schema={k: pl.String for k in data}).with_columns(delisted=pl.lit(delisted))

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        out = pl.concat([self._list(False), self._list(True)])
        return out.unique(["code", "delisted"], keep="first").sort("code", "delisted").select(list(LISTING_SCHEMA))


def write_eod(df: pl.DataFrame, raw_root: Path, report: IngestReport,
              only_dates: set[date] | None = None) -> None:
    """Upsert rows into data/raw/eodhd/eod/date=YYYY-MM-DD, one vintage file per change.

    Rows for codes already stored under a date are replaced; other codes are kept, so a later
    pull that adds a code produces a new vintage holding the union. Identical content is a no-op.
    """
    base = raw_root / "eodhd" / "eod"
    for part in df.partition_by("date", maintain_order=True):
        d = part["date"][0]
        if only_dates is not None and d not in only_dates:
            continue
        pdir = base / f"date={d.isoformat()}"
        report.rows += part.height                       # rows this pull supplied, not the merged total
        old = read_latest_raw(pdir)
        if old is not None:
            part = pl.concat([old.filter(~pl.col("code").is_in(part["code"].implode())), part])
        w = write_raw(part.sort("code").select(list(EOD_SCHEMA)), pdir)
        report.partitions_written += int(w.created)
        report.partitions_unchanged += int(not w.created)


def probe(cfg: SourcesConfig, api_key: SecretStr, http: HttpClient | None = None) -> dict[str, Any]:
    """Entitlement check for `eqrisk doctor`: HTTP status per endpoint, plus plan limits."""
    client = http or HttpClient(cfg.http, cfg.eodhd.requests_per_second)
    token = {"api_token": api_key.get_secret_value(), "fmt": "json"}
    checks = {
        "eod": ("eod/AAPL.US", {"from": "2024-01-02", "to": "2024-01-05"}),
        "listings": ("exchange-symbol-list/US", {}),
        "bulk_eod": ("eod-bulk-last-day/US", {"date": "2024-01-05", "symbols": "AAPL.US"}),
        "fundamentals": ("fundamentals/AAPL.US", {"filter": "General::Code"}),
        "dividends": ("div/AAPL.US", {"from": "2024-01-01"}),
        "splits": ("splits/AAPL.US", {"from": "2020-01-01"}),
    }
    out: dict[str, Any] = {}
    for label, (path, params) in checks.items():
        try:
            client.get(f"{cfg.eodhd.base_url}/{path}", {**token, **params})
            out[label] = "ok"
        except SourceError as exc:
            out[label] = str(exc).rsplit("->", 1)[-1].strip()
    try:
        user = client.get_json(f"{cfg.eodhd.base_url}/user", token)
        out["plan"] = {k: user.get(k) for k in ("subscriptionType", "dailyRateLimit", "apiRequests")}
    except SourceError as exc:
        out["plan"] = str(exc)
    return out
