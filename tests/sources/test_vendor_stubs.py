"""Sharadar and Databento are wired but not entitled here; these tests use recorded shapes."""

from datetime import date

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from eqrisk.sources.base import CostGuardError, SourceError
from eqrisk.sources.databento_px import DatabentoPrices
from eqrisk.sources.sharadar import Sharadar

COLUMNS = [{"name": "ticker", "type": "String"}, {"name": "dimension", "type": "String"},
           {"name": "datekey", "type": "Date"}, {"name": "netinccmn", "type": "BigDecimal(34,4)"}]
PAGES = [
    {"datatable": {"data": [["AAPL", "ARQ", "2024-02-02", 33916000000.0]], "columns": COLUMNS},
     "meta": {"next_cursor_id": "c1"}},
    {"datatable": {"data": [["MSFT", "ARQ", "2024-01-30", 21870000000.0]], "columns": COLUMNS},
     "meta": {"next_cursor_id": None}},
]


def test_sharadar_follows_the_cursor(mock_http, sources):
    seen = []

    def route(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=PAGES[len(seen) - 1])

    sf1 = Sharadar(sources, SecretStr("k"), "SF1", {"ticker": "AAPL,MSFT"}, http=mock_http([("/SF1.json", route)]))
    df = sf1.fetch(date(2024, 1, 1), date(2024, 3, 1))
    assert df.height == 2 and df["ticker"].to_list() == ["AAPL", "MSFT"]
    assert seen[0]["dimension"] == "ARQ,ARY,ART" and seen[0]["datekey.gte"] == "2024-01-01"
    assert seen[1]["qopts.cursor_id"] == "c1"


def test_sharadar_refuses_restated_dimensions(sources):
    with pytest.raises(SourceError, match="MR"):
        Sharadar(sources, SecretStr("k"), "SF1", {"dimension": "MRQ"})


class _FakeDatabento:
    def __init__(self, cost: float, frame: pd.DataFrame | None = None) -> None:
        self.requests: list[dict] = []
        outer = self

        class _Meta:
            def get_cost(self, **kw):
                outer.requests.append(kw)
                return cost

        class _Store:
            def to_df(self):
                return frame

        class _Ts:
            def get_range(self, **kw):
                outer.requests.append({"get_range": kw})
                return _Store()

        self.metadata, self.timeseries = _Meta(), _Ts()


def test_databento_refuses_requests_above_the_cap(sources):
    fake = _FakeDatabento(cost=sources.databento.max_cost_usd * 10)
    with pytest.raises(CostGuardError, match="cap"):
        DatabentoPrices(sources, SecretStr("k"), ["AAPL"], client=fake).fetch(date(2024, 7, 1), date(2024, 7, 31))
    assert not any("get_range" in r for r in fake.requests)


def test_databento_fetch_under_the_cap(sources):
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-07-01", "2024-07-02"], utc=True), name="ts_event")
    frame = pd.DataFrame({"symbol": ["BRK.B", "BRK.B"], "open": [1.0, 2.0], "high": [1.0, 2.0],
                          "low": [1.0, 2.0], "close": [1.5, 2.5], "volume": [10, 20]}, index=idx)
    fake = _FakeDatabento(cost=0.001, frame=frame)
    df = DatabentoPrices(sources, SecretStr("k"), ["BRK-B"], client=fake).fetch(date(2024, 7, 1), date(2024, 7, 2))
    assert df["date"].to_list() == [date(2024, 7, 1), date(2024, 7, 2)] and df["code"][0] == "BRK.B"
    assert fake.requests[0]["end"] == "2024-07-03" and fake.requests[0]["symbols"] == ["BRK.B"]
