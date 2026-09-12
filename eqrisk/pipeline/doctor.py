"""`eqrisk doctor` (blueprint §13.1): keys, connectivity, entitlements, dataset ranges, cost estimates."""

from __future__ import annotations

import json
from datetime import date, timedelta

import polars as pl

from eqrisk.config import Project
from eqrisk.sources.base import SourceError
from eqrisk.sources.databento_px import DatabentoPrices
from eqrisk.sources.edgar import Edgar
from eqrisk.sources.eodhd_px import probe as eodhd_probe
from eqrisk.sources.famafrench import FamaFrenchDaily
from eqrisk.sources.fred import FredSeries
from eqrisk.sources.sp500_membership import Fja05680

# How far back the connectivity probes look for a recent observation.
_PROBE_DAYS = 45


def run_doctor(project: Project, today: date | None = None) -> list[dict[str, str]]:
    today = today or date.today()
    src, s, cfg = project.sources, project.settings, project.config
    out: list[dict[str, str]] = []

    def add(check: str, status: str, detail: str = "") -> None:
        out.append({"check": check, "status": status, "detail": detail})

    add("data tier", "info", f"prices={cfg.sources.prices} corp_actions={cfg.sources.corp_actions} "
                             f"fundamentals={cfg.sources.fundamentals} membership={cfg.sources.membership}")
    for key, present in s.presence().items():
        add(f"env {key}", "ok" if present else "missing")

    current: pl.DataFrame | None = None
    try:
        fja = Fja05680(src)
        fname = fja.components_file()
        comps = fja.fetch(date(1900, 1, 1), today)
        last = comps.row(-1, named=True)
        current = fja.fetch_current()
        add("fja05680 membership", "ok", f"{fname!r}: latest snapshot {last['date']} "
                                         f"({len(last['tickers'].split(','))} names); current list {current.height}")
    except SourceError as exc:
        add("fja05680 membership", "fail", str(exc))

    if s.eodhd_api_key is not None:
        try:
            add("eodhd entitlements", "ok", json.dumps(eodhd_probe(src, s.eodhd_api_key)))
        except SourceError as exc:
            add("eodhd entitlements", "fail", str(exc))

    series = cfg.sources.risk_free.series
    try:
        fr = FredSeries(src, s.fred_api_key, series).fetch(today - timedelta(days=_PROBE_DAYS), today)
        last_obs = fr.drop_nulls("value").row(-1, named=True)
        add(f"fred {series}", "ok", f"latest {last_obs['date']} = {last_obs['value']}%")
    except (SourceError, IndexError) as exc:
        add(f"fred {series}", "fail", str(exc))

    try:
        tick = Edgar(src, s.sec_user_agent).company_tickers()
        add("sec edgar", "ok", f"company_tickers: {tick.height} entries; User-Agent set")
    except SourceError as exc:
        add("sec edgar", "fail", str(exc))

    try:
        ff = FamaFrenchDaily(src).fetch(today - timedelta(days=4 * _PROBE_DAYS), today)
        add("ken french", "ok", f"latest {ff['date'].max()}")
    except SourceError as exc:
        add("ken french", "fail", str(exc))

    if s.databento_api_key is not None:
        try:
            symbols = current["ticker"].to_list() if current is not None else ["AAPL"]
            dp = DatabentoPrices(src, s.databento_api_key, symbols)
            rng = dp.dataset_range()
            rs, re_ = str(rng.get("start", ""))[:10], str(rng.get("end", ""))[:10]
            plan_start = max(cfg.history.price_start, date.fromisoformat(rs)) if rs else cfg.history.price_start
            cost = dp.cost(plan_start, today - timedelta(days=1))
            status = "ok" if cost <= src.databento.max_cost_usd else "warn"
            add(f"databento {src.databento.dataset}", status,
                f"range {rs}..{re_}; {len(dp.symbols)} symbols {plan_start}..{today - timedelta(days=1)} "
                f"would cost ${cost:.2f} (cap ${src.databento.max_cost_usd:.2f})")
        except Exception as exc:  # the vendor SDK raises its own exception types
            add(f"databento {src.databento.dataset}", "fail", f"{type(exc).__name__}: {str(exc)[:160]}")
    return out
