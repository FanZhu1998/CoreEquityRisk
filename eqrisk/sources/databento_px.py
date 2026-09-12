"""Databento EQUS.SUMMARY daily bars (blueprint §4.1). Metered: every request is priced first.

On this machine EQUS.SUMMARY history starts 2024-07-01, so it is a secondary source for recent
consolidated volume and a close-to-close cross-check, not the backfill source (D-001).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import polars as pl
from pydantic import SecretStr

from eqrisk.config import SourcesConfig
from eqrisk.ids import normalize_symbol
from eqrisk.sources.base import CostGuardError, Source, SourceError

BAR_SCHEMA: dict[str, Any] = {"code": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
                              "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64}


class DatabentoPrices(Source):
    name = "databento"
    dataset = "ohlcv_1d"

    def __init__(self, cfg: SourcesConfig, api_key: SecretStr | None, symbols: Sequence[str],
                 client: Any = None) -> None:
        self.cfg = cfg.databento
        self._key = api_key
        self.symbols = sorted({normalize_symbol(s) for s in symbols})
        self._client = client

    def client(self) -> Any:
        if self._client is None:
            if self._key is None:
                raise SourceError("DATABENTO_API_KEY is not set in .env")
            import databento as db

            self._client = db.Historical(self._key.get_secret_value())
        return self._client

    def _request(self, start: date, end: date) -> dict[str, Any]:
        # Databento `end` is exclusive; our intervals are closed.
        return {"dataset": self.cfg.dataset, "symbols": self.symbols, "schema": self.cfg.schema_,
                "stype_in": self.cfg.stype_in, "start": start.isoformat(),
                "end": (end + timedelta(days=1)).isoformat()}

    def cost(self, start: date, end: date) -> float:
        return float(self.client().metadata.get_cost(**self._request(start, end)))

    def dataset_range(self) -> dict[str, Any]:
        rng = self.client().metadata.get_dataset_range(dataset=self.cfg.dataset)
        return dict(rng) if isinstance(rng, dict) else {"range": str(rng)}

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        cost = self.cost(start, end)
        if cost > self.cfg.max_cost_usd:
            raise CostGuardError(f"Databento would charge ${cost:.4f} for {len(self.symbols)} symbols "
                                 f"{start}..{end}, above the ${self.cfg.max_cost_usd:.2f} cap "
                                 "(sources.yaml databento.max_cost_usd)")
        store = self.client().timeseries.get_range(**self._request(start, end))
        pdf = store.to_df()
        if len(pdf) == 0:
            return pl.DataFrame(schema=BAR_SCHEMA)
        df = pl.from_pandas(pdf.reset_index())
        return df.select(
            code=pl.col("symbol").cast(pl.String),
            date=pl.col("ts_event").dt.date(),
            open=pl.col("open").cast(pl.Float64), high=pl.col("high").cast(pl.Float64),
            low=pl.col("low").cast(pl.Float64), close=pl.col("close").cast(pl.Float64),
            volume=pl.col("volume").cast(pl.Float64),
        ).sort("date", "code")
