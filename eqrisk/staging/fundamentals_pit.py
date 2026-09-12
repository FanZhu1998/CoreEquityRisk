"""Point-in-time fundamentals from EDGAR XBRL company facts (blueprint §4.5, Appendix C).

Every fact is an observation of one (concept, period) that became public on its filing date.
For each item and period this module replays facts in filing order and emits a row whenever the
chosen value changes: the first concept in the item's chain that has a value wins, and each
concept keeps its latest vintage. Restatements therefore enter only from their own filing date.

Trailing twelve months at a period end e: the fiscal-year value if e ends a fiscal year, else
FY(prior year) + YTD(e) - YTD(e - 1 year), each component taken at its latest known vintage. This
works from 10-Q year-to-date columns and needs no Q4 derivation.

Rows become usable on `available_date`: the first session after filing (+ lag; rule 1).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from eqrisk.config import ConceptItem, ConceptsConfig, FundamentalsCfg, split_concept

PIT_SCHEMA: dict[str, Any] = {
    "cik": pl.Int64, "item": pl.String, "period_end": pl.Date, "value": pl.Float64, "filed": pl.Date,
    "available_date": pl.Date, "concept": pl.String, "accn": pl.String,
}
_YEAR = timedelta(days=365)


@dataclass(frozen=True)
class Fact:
    concept: str                 # "taxonomy:Concept"
    start: date | None
    end: date
    val: float
    filed: date
    accn: str


Event = tuple[date, float, str, str]          # (filed, value, concept, accn)


def _canon(name: str) -> str:
    tax, concept = split_concept(name)
    return f"{tax}:{concept}"


def _events(facts: list[Fact], chain: list[str], fallback: list[str]) -> list[Event]:
    """Replay facts for one period in filing order; emit each change of the chosen value."""
    state: dict[str, tuple[float, str]] = {}
    out: list[Event] = []
    last: float | None = None
    for f in sorted(facts, key=lambda f: (f.filed, f.accn)):
        state[f.concept] = (f.val, f.accn)
        chosen: tuple[float, str, str] | None = None
        for c in chain:
            if c in state:
                chosen = (state[c][0], c, state[c][1])
                break
        if chosen is None and fallback:
            present = [c for c in fallback if c in state]
            if present:
                chosen = (sum(state[c][0] for c in present), "+".join(present), f.accn)
        if chosen is not None and chosen[0] != last:
            out.append((f.filed, chosen[0], chosen[1], chosen[2]))
            last = chosen[0]
    return out


def _combine(parts: list[list[Event]], signs: list[float], label: str) -> list[Event]:
    """Linear combination of event series, defined once every part is known."""
    times = sorted({ev[0] for p in parts for ev in p})
    out: list[Event] = []
    last: float | None = None
    for t in times:
        latest = []
        for p in parts:
            known = [ev for ev in p if ev[0] <= t]
            if not known:
                break
            latest.append(known[-1])
        else:
            v = sum(s * ev[1] for s, ev in zip(signs, latest, strict=True))
            if v != last:
                accn = max(latest, key=lambda ev: ev[0])[3]
                out.append((t, v, label, accn))
                last = v
    return out


class _Durations:
    def __init__(self, cfg: FundamentalsCfg) -> None:
        self.cfg = cfg

    def kind(self, start: date | None, end: date) -> str | None:
        if start is None:
            return None
        d = (end - start).days
        for name, (lo, hi) in (("Q", self.cfg.quarter_days), ("H", self.cfg.half_year_days),
                               ("9M", self.cfg.nine_month_days), ("FY", self.cfg.year_days)):
            if lo <= d <= hi:
                return name
        return None


def _near(a: date, b: date, tol: int) -> bool:
    return abs((a - b).days) <= tol


def company_pit(facts: pl.DataFrame, concepts: ConceptsConfig, cfg: FundamentalsCfg,
                forms: list[str], available: Callable[[date], date]) -> pl.DataFrame:
    """PIT rows for one company. `facts` is its raw companyfacts frame; `available` maps a filing
    date to the first session the value may be used."""
    if facts.height == 0:
        return pl.DataFrame(schema=PIT_SCHEMA)
    cik = int(facts["cik"][0])
    wanted = {f"{t}:{c}" for t, c in concepts.concepts()}
    sub = (facts.filter(pl.col("form").is_in(forms))
           .with_columns(key=pl.col("taxonomy") + ":" + pl.col("concept"))
           .filter(pl.col("key").is_in(sorted(wanted)) & pl.col("val").is_not_null()
                   & pl.col("end").is_not_null() & pl.col("filed").is_not_null()))
    by_concept_unit: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for r in sub.select("key", "unit", "start", "end", "val", "filed", "accn").iter_rows():
        by_concept_unit[(r[0], r[1])].append(Fact(r[0], r[2], r[3], float(r[4]), r[5], r[6] or ""))

    durs = _Durations(cfg)
    rows: list[dict[str, Any]] = []

    def emit(item: str, period_end: date, events: list[Event]) -> None:
        for filed, value, concept, accn in events:
            rows.append({"cik": cik, "item": item, "period_end": period_end, "value": value,
                         "filed": filed, "available_date": available(filed), "concept": concept, "accn": accn})

    for item_name, item in concepts.items.items():
        chain = [_canon(c) for c in item.chain]
        fallback = [_canon(c) for c in (item.fallback_sum or [])]
        pool = [f for c in [*chain, *fallback] for f in by_concept_unit.get((c, item.unit), [])]
        if item.unit == "shares":
            pool = [f for f in pool if f.val > 0]           # a class tagged with 0 shares is not a count
        if not pool:
            continue
        if item.per_concept:
            for c in chain:
                by_end: dict[date, list[Fact]] = defaultdict(list)
                for f in pool:
                    if f.concept == c:
                        by_end[f.end].append(f)
                for end, fs in by_end.items():
                    emit(item_name, end, _events(fs, [c], []))
        elif item.kind == "instant":
            by_end = defaultdict(list)
            for f in pool:
                by_end[f.end].append(f)
            for end, fs in by_end.items():
                emit(item_name, end, _events(fs, chain, fallback))
        elif item.kind == "annual":
            by_end = defaultdict(list)
            for f in pool:
                if durs.kind(f.start, f.end) == "FY":
                    by_end[f.end].append(f)
            for end, fs in by_end.items():
                emit(item_name, end, _events(fs, chain, fallback))
        else:
            _ttm(item_name, item, pool, chain, fallback, durs, cfg, emit)
    if not rows:
        return pl.DataFrame(schema=PIT_SCHEMA)
    return pl.DataFrame(rows, schema=PIT_SCHEMA).sort("item", "period_end", "filed")


def _ttm(item_name: str, item: ConceptItem, pool: list[Fact], chain: list[str], fallback: list[str],
         durs: _Durations, cfg: FundamentalsCfg, emit: Callable[[str, date, list[Event]], None]) -> None:
    by_key: dict[tuple[date, date], list[Fact]] = defaultdict(list)
    kinds: dict[tuple[date, date], str] = {}
    for f in pool:
        k = durs.kind(f.start, f.end)
        if k is None or f.start is None:
            continue
        by_key[(f.start, f.end)].append(f)
        kinds[(f.start, f.end)] = k
    series = {key: _events(fs, chain, fallback) for key, fs in by_key.items()}
    fy = {key: ev for key, ev in series.items() if kinds[key] == "FY"}
    tol = cfg.comparative_tolerance_days
    for end in sorted({e for _, e in series}):
        fy_here = [ev for (s, e), ev in fy.items() if e == end]
        if fy_here:
            emit(item_name, end, fy_here[0])
            continue
        # year-to-date column ending here: the longest sub-annual duration
        ytd_keys = sorted(((s, e) for (s, e) in series if e == end and kinds[(s, e)] in ("Q", "H", "9M")),
                          key=lambda k: k[0])
        if not ytd_keys:
            continue
        ytd = ytd_keys[0]
        length = (ytd[1] - ytd[0]).days
        prior_fy = [ev for (s, e), ev in fy.items() if _near(e, ytd[0] - timedelta(days=1), tol)]
        prior_ytd = [ev for (s, e), ev in series.items()
                     if _near(e, end - _YEAR, tol) and abs((e - s).days - length) <= tol and kinds[(s, e)] != "FY"]
        if prior_fy and prior_ytd:
            emit(item_name, end, _combine([prior_fy[0], series[ytd], prior_ytd[0]], [1.0, 1.0, -1.0], "TTM"))


def asof(pit: pl.DataFrame, dates: pl.DataFrame, item: str, max_staleness_days: int) -> pl.DataFrame:
    """Latest value of `item` known on each (date, cik): rows available on or before the date,
    latest period end, latest vintage of that period; stale periods drop out.

    `dates` has columns date, cik. Returns date, cik, value, period_end, filed (the vintage used).
    """
    rows = (pit.filter(pl.col("item") == item)
            .select("cik", "period_end", "available_date", "value", "filed")
            .sort("available_date"))
    # Running best (period_end, value, filed) per cik as rows become available.
    best = []
    for (cik,), g in rows.group_by("cik"):
        cur: dict[date, tuple[float, date]] = {}
        top: date | None = None
        ordered = g.sort("available_date").select("period_end", "available_date", "value", "filed")
        for pe, av, v, fd in ordered.iter_rows():
            cur[pe] = (v, fd)
            top = pe if top is None or pe > top else top
            best.append((cik, av, top, cur[top][0], cur[top][1]))
    tl = pl.DataFrame(best, schema={"cik": pl.Int64, "available_date": pl.Date, "period_end": pl.Date,
                                    "value": pl.Float64, "filed": pl.Date}, orient="row")
    tl = tl.unique(["cik", "available_date"], keep="last").sort("available_date")
    out = dates.sort("date").join_asof(tl, left_on="date", right_on="available_date", by="cik",
                                       strategy="backward", check_sortedness=False)   # both sorted on the key
    fresh = (pl.col("date") - pl.col("period_end")).dt.total_days() <= max_staleness_days
    return out.with_columns(value=pl.when(fresh).then(pl.col("value")).otherwise(None),
                            period_end=pl.when(fresh).then(pl.col("period_end")).otherwise(None),
                            filed=pl.when(fresh).then(pl.col("filed")).otherwise(None),
                            ).select("date", "cik", "value", "period_end", "filed")


def asof_priority(pit: pl.DataFrame, dates: pl.DataFrame, item: str, order: list[str],
                  max_staleness_days: int) -> pl.DataFrame:
    """`asof` per concept, then the first concept in `order` with a fresh value on each date.

    Share counts use this so one definition (cover page, balance sheet, weighted average) is not
    swapped for another every time a filing dated a few days later arrives.
    Returns date, cik, value, period_end, filed, concept.
    """
    rows = pit.filter(pl.col("item") == item)
    out = dates.select("date", "cik").unique()
    parts = []
    for rank, concept in enumerate(order):
        sub = rows.filter(pl.col("concept") == concept)
        if sub.height == 0:
            continue
        a = asof(sub, out, item, max_staleness_days)
        parts.append(a.with_columns(rank=pl.lit(rank), concept=pl.lit(concept)).drop_nulls("value"))
    if not parts:
        return out.with_columns(value=pl.lit(None, pl.Float64), period_end=pl.lit(None, pl.Date),
                                filed=pl.lit(None, pl.Date), concept=pl.lit(None, pl.String))
    best = pl.concat(parts).sort("rank").unique(["date", "cik"], keep="first")
    return out.join(best.drop("rank"), on=["date", "cik"], how="left")
