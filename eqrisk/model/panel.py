"""(T, N) numpy panels over the staged tables: rows are sessions (oldest first), columns are sids."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl


@dataclass
class Panel:
    dates: list[date]
    sids: np.ndarray                                   # int64 (N,)
    industries: list[str]                              # industry code order for `industry`
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    vectors: dict[str, np.ndarray] = field(default_factory=dict)   # (T,) series such as rf

    def __post_init__(self) -> None:
        self.row = {d: i for i, d in enumerate(self.dates)}
        self.col = {int(s): j for j, s in enumerate(self.sids)}

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]

    def __setitem__(self, name: str, value: np.ndarray) -> None:
        self.arrays[name] = value

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.dates), len(self.sids)


def pivot(df: pl.DataFrame, value: str, dates: list[date], sids: np.ndarray, fill: Any = np.nan,
          dtype: Any = np.float64) -> np.ndarray:
    """Long (date, sid, value) -> (T, N) aligned to `dates` x `sids`; absent cells get `fill`."""
    out = np.full((len(dates), len(sids)), fill, dtype=dtype)
    if df.height == 0:
        return out
    rows = pl.DataFrame({"date": dates, "_r": np.arange(len(dates))}, schema={"date": pl.Date, "_r": pl.Int64})
    cols = pl.DataFrame({"sid": sids, "_c": np.arange(len(sids))}, schema={"sid": pl.Int64, "_c": pl.Int64})
    j = df.select("date", "sid", value).join(rows, on="date").join(cols, on="sid").drop_nulls(value)
    out[j["_r"].to_numpy(), j["_c"].to_numpy()] = j[value].to_numpy()
    return out


def build_panel(prices: pl.DataFrame, mcap: pl.DataFrame, universe: pl.DataFrame, rf: pl.DataFrame,
                sessions: list[date]) -> Panel:
    """Everything the descriptor and regression layers read, as aligned panels."""
    sids = np.array(sorted(set(prices["sid"].unique().to_list()) | set(universe["sid"].unique().to_list())),
                    dtype=np.int64)
    industries = sorted(universe["industry"].drop_nulls().unique().to_list())
    P = Panel(sessions, sids, industries)
    for col in ("ret", "ret_excess", "close_unadj", "volume", "cum_split"):
        P[col] = pivot(prices, col, sessions, sids)
    regular = prices.with_columns(div=pl.when((pl.col("action") == "dividend") & ~pl.col("special_dividend"))
                                  .then(pl.col("dividend")).otherwise(0.0))
    P["dividend"] = pivot(regular, "div", sessions, sids, fill=0.0)
    adjusted = prices.with_columns(a=pl.col("action").is_in(["dividend", "split", "distribution"]))
    P["vendor_adjusts"] = pivot(adjusted, "a", sessions, sids, fill=False, dtype=bool)
    flagged = prices.with_columns(f=pl.col("price_flag").is_not_null())
    P["price_flag"] = pivot(flagged, "f", sessions, sids, fill=False, dtype=bool)
    P["mcap"] = pivot(mcap, "mcap_issuer", sessions, sids)
    P["shares"] = pivot(mcap, "shares_out", sessions, sids)
    P["in_cov"] = pivot(universe.with_columns(t=pl.lit(True)), "t", sessions, sids, fill=False, dtype=bool)
    P["in_estu"] = pivot(universe, "in_estu", sessions, sids, fill=False, dtype=bool)
    P["v_reg"] = pivot(universe, "v_reg", sessions, sids)
    P["capw"] = pivot(universe, "capw", sessions, sids)
    code = {ind: i for i, ind in enumerate(industries)}
    coded = universe.drop_nulls("industry").with_columns(
        k=pl.col("industry").replace_strict(code, return_dtype=pl.Int64))
    P["industry"] = pivot(coded, "k", sessions, sids, fill=-1, dtype=np.int64)
    rfv = pl.DataFrame({"date": sessions}, schema={"date": pl.Date}).join(rf.select("date", "rf"), on="date",
                                                                          how="left")
    P.vectors["rf"] = rfv["rf"].fill_null(0.0).to_numpy()
    return P
