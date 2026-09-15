"""Ingest orchestration (blueprint §13.1-§13.4): `eqrisk ingest --date D` and
`eqrisk backfill --stage ingest`. Adapters fetch; this module decides what to fetch and writes
raw partitions. Every step is idempotent: re-running a date rewrites nothing that is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from eqrisk.calendar import get_calendar
from eqrisk.config import Project
from eqrisk.ids import cik10
from eqrisk.log import get_logger
from eqrisk.sources.base import IngestReport, SourceError
from eqrisk.sources.edgar import Edgar
from eqrisk.sources.eodhd_px import EodhdListings, EodhdPrices, write_eod
from eqrisk.sources.famafrench import FamaFrenchDaily
from eqrisk.sources.fred import FredSeries
from eqrisk.sources.sp500_membership import Fja05680
from eqrisk.staging import membership as mem
from eqrisk.staging import security_master as sm
from eqrisk.store import RawWrite, atomic_write_bytes, latest_dated_dir, latest_raw, read_dated, write_dated, write_raw

log = get_logger(__name__)

# Reference series (membership snapshots, T-bill yields, French factors) are small: always pull
# them whole, so every snapshot is complete and comparable byte for byte.
WHOLE_SERIES_START = date(1900, 1, 1)
# First-run EDGAR daily-index window when no watermark exists yet.
_EDGAR_FIRST_LOOKBACK_DAYS = 7


def dataset_dir(project: Project, source: str, dataset: str) -> Path:
    return project.raw_dir / source / dataset


def _count(report: IngestReport, w: RawWrite | None) -> None:
    if w is None or not w.created:
        report.partitions_unchanged += 1
    else:
        report.partitions_written += 1


# --------------------------------------------------------------------------- #
# Watermarks                                                                  #
# --------------------------------------------------------------------------- #


def _watermark_path(project: Project) -> Path:
    return project.raw_dir / "_state" / "watermarks.json"


def load_watermarks(project: Project) -> dict[str, str]:
    p = _watermark_path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_watermarks(project: Project, updates: dict[str, str]) -> dict[str, str]:
    wm = {**load_watermarks(project), **updates}
    atomic_write_bytes(json.dumps(wm, indent=2, sort_keys=True).encode(), _watermark_path(project))
    return wm


# --------------------------------------------------------------------------- #
# Reference datasets                                                          #
# --------------------------------------------------------------------------- #


def _stale(project: Project, source: str, dataset: str, today: date, max_age_days: int) -> bool:
    hit = latest_dated_dir(dataset_dir(project, source, dataset))
    return hit is None or (today - hit[0]).days >= max_age_days


def ingest_reference(project: Project, today: date, end: date, *, refresh_listings: bool = False,
                     refresh_cik_lookup: bool = False) -> list[IngestReport]:
    """Membership, risk-free, French factors, symbol lists, SEC ticker and name maps.

    Snapshots are partitioned by pull date (`today`) and skipped when unchanged.
    """
    src, settings = project.sources, project.settings
    reports: list[IngestReport] = []

    def put(source: str, dataset: str, frame: pl.DataFrame) -> None:
        rep = IngestReport(source, dataset, rows=frame.height)
        _count(rep, write_dated(frame, dataset_dir(project, source, dataset), today))
        reports.append(rep)
        log.info("ingested", source=source, dataset=dataset, rows=frame.height)

    fja = Fja05680(src)
    put("fja05680", "components", fja.fetch(WHOLE_SERIES_START, end))
    put("fja05680", "current", fja.fetch_current())
    put("fja05680", "spells", fja.fetch_spells())
    series = project.config.sources.risk_free.series
    put("fred", series, FredSeries(src, settings.fred_api_key, series).fetch(WHOLE_SERIES_START, end))
    put("famafrench", "daily", FamaFrenchDaily(src).fetch(WHOLE_SERIES_START, end))
    if refresh_listings or _stale(project, "eodhd", "listings", today, src.eodhd.listings_refresh_days):
        put("eodhd", "listings", EodhdListings(src, settings.eodhd_api_key).fetch(WHOLE_SERIES_START, end))
    edgar = Edgar(src, settings.sec_user_agent)
    put("edgar", "company_tickers", edgar.company_tickers())
    if refresh_cik_lookup or _stale(project, "edgar", "cik_lookup", today, src.edgar.cik_lookup_refresh_days):
        put("edgar", "cik_lookup", edgar.cik_lookup())
    return reports


def read_reference(project: Project, source: str, dataset: str) -> pl.DataFrame:
    df = read_dated(dataset_dir(project, source, dataset))
    if df is None:
        raise SourceError(f"no raw {source}/{dataset}; run `eqrisk backfill --stage ingest` first")
    return df


# --------------------------------------------------------------------------- #
# EDGAR                                                                       #
# --------------------------------------------------------------------------- #


def pull_edgar(project: Project, edgar: Edgar, ciks: Iterable[int], *, refresh: bool) -> IngestReport:
    """Submissions + company facts per CIK. Resumable: without `refresh`, CIKs already stored are skipped."""
    rep = IngestReport("edgar", "companyfacts")
    base = project.raw_dir / "edgar"
    todo = sorted(set(ciks))
    for i, cik in enumerate(todo):
        part = f"cik={cik10(cik)}"
        if not refresh and latest_raw(base / "companyfacts" / part) is not None:
            rep.partitions_unchanged += 1
            continue
        try:
            company, filings = edgar.submissions(cik)
            facts = edgar.companyfacts(cik)
        except SourceError as exc:
            rep.errors.append(f"{cik}: {exc}")
            continue
        write_raw(company, base / "submissions" / part)
        write_raw(filings, base / "filings" / part)
        _count(rep, write_raw(facts, base / "companyfacts" / part))
        rep.rows += facts.height
        if facts.height == 0:
            rep.missing.append(str(cik))
        if (i + 1) % 100 == 0:
            log.info("edgar progress", done=i + 1, total=len(todo))
    return rep


def known_ciks(project: Project) -> list[int]:
    base = project.raw_dir / "edgar" / "companyfacts"
    return sorted(int(p.name.split("=", 1)[1]) for p in base.glob("cik=*")) if base.exists() else []


def edgar_incremental(project: Project, d: date, edgar: Edgar | None = None) -> IngestReport:
    """Re-pull companies that filed a periodic report since the watermark (one index file per day).

    SEC publishes a daily index for business days only, some hours after the close. A day without one
    is a weekend or holiday, or not published yet, and which it was shows only once a later day has an
    index. So the watermark moves to the last day that had one, and the days after it are asked for
    again next run instead of being skipped for good (DECISIONS D-026).
    """
    wm = load_watermarks(project)
    last = date.fromisoformat(wm["edgar_daily_index"]) if "edgar_daily_index" in wm \
        else d - timedelta(days=_EDGAR_FIRST_LOOKBACK_DAYS)
    edgar = edgar or Edgar(project.sources, project.settings.sec_user_agent)
    known = set(known_ciks(project))
    forms = set(project.sources.edgar.forms)
    touched: set[int] = set()
    through = last
    without_index: list[str] = []
    day = last + timedelta(days=1)
    while day <= d:
        idx = edgar.daily_index(day)
        if idx.height:
            write_raw(idx, project.raw_dir / "edgar" / "daily_index" / f"date={day.isoformat()}")
            touched |= set(idx.filter(pl.col("form").is_in(sorted(forms)))["cik"].to_list()) & known
            through = day
        else:
            without_index.append(day.isoformat())
        day += timedelta(days=1)
    rep = pull_edgar(project, edgar, touched, refresh=True)
    if through > last:
        save_watermarks(project, {"edgar_daily_index": through.isoformat()})
    log.info("edgar daily index", through=through.isoformat(), without_index=without_index, refreshed=len(touched))
    return rep


# --------------------------------------------------------------------------- #
# Entry points                                                                #
# --------------------------------------------------------------------------- #


def membership_tickers(project: Project, start: date, end: date) -> list[str]:
    comps = mem.parse_components(read_reference(project, "fja05680", "components"))
    sessions = get_calendar(project.config.calendar).sessions(start, end)
    return mem.daily_membership(comps, sessions)["ticker"].unique().sort().to_list()


def override_targets(project: Project) -> tuple[list[str], list[int]]:
    """Vendor codes and CIKs named in override files (ticker_map.csv, cik_links.csv)."""
    tm = sm.read_ticker_map(project.overrides_dir / "ticker_map.csv")
    links = sm.read_cik_links(project.resolve(project.config.security_master.cik_links_file))
    codes = sorted(set(tm["eodhd_code"].drop_nulls().to_list()))
    ciks = set(tm["cik"].drop_nulls().to_list()) | set(links["cik"].to_list()) | set(links["predecessor_cik"].to_list())
    return codes, sorted(int(c) for c in ciks)


def pull_overrides(project: Project, start: date, end: date) -> list[IngestReport]:
    """Pull just what the override files name, so a fix does not need a full backfill."""
    codes, ciks = override_targets(project)
    reports = []
    if codes:
        px = EodhdPrices(project.sources, project.settings.eodhd_api_key, codes)
        rep = IngestReport("eodhd", "eod")
        write_eod(px.fetch(start, end), project.raw_dir, rep)
        rep.missing = list(px.missing)
        reports.append(rep)
    if ciks:
        edgar = Edgar(project.sources, project.settings.sec_user_agent)
        reports.append(pull_edgar(project, edgar, ciks, refresh=False))
    return reports


def backfill_ingest(project: Project, start: date, end: date, *, refresh_edgar: bool = False) -> list[IngestReport]:
    today = date.today()
    reports = ingest_reference(project, today, end, refresh_listings=True)
    tickers = membership_tickers(project, start, end)
    cands = sm.candidate_codes(tickers, read_reference(project, "eodhd", "listings"))
    extra_codes, extra_ciks = override_targets(project)
    codes = sorted(set(cands["code"].to_list()) | set(extra_codes))
    log.info("backfill prices", tickers=len(tickers), codes=len(codes), start=str(start), end=str(end))

    px = EodhdPrices(project.sources, project.settings.eodhd_api_key, codes)
    rep = IngestReport("eodhd", "eod")
    write_eod(px.fetch(start, end), project.raw_dir, rep)
    rep.missing = list(px.missing)
    reports.append(rep)

    ciks = sm.candidate_ciks(
        tickers, current=read_reference(project, "fja05680", "current"),
        sec_tickers=read_reference(project, "edgar", "company_tickers"),
        cik_lookup=read_reference(project, "edgar", "cik_lookup"),
        names=cands["name"].drop_nulls().unique().to_list())
    all_ciks = sorted(set(ciks["cik"].to_list()) | set(extra_ciks))
    log.info("backfill edgar", ciks=len(all_ciks))
    edgar = Edgar(project.sources, project.settings.sec_user_agent)
    reports.append(pull_edgar(project, edgar, all_ciks, refresh=refresh_edgar))
    save_watermarks(project, {"eodhd_eod": end.isoformat(), "edgar_daily_index": today.isoformat()})
    return reports


def active_codes(project: Project, d: date) -> list[str]:
    """Vendor codes to pull for session `d`: the staged security master when it exists, else the
    candidate codes of everyone in the index on `d`."""
    master = project.staged_dir / "security_master" / "security_master.parquet"
    if master.exists():
        th = pl.read_parquet(project.staged_dir / "ticker_history" / "ticker_history.parquet")
        live = th.filter((pl.col("start_date") <= d) & (pl.col("end_date").is_null() | (pl.col("end_date") >= d)))
        return sorted(set(live["eodhd_code"].drop_nulls().to_list()))
    tickers = membership_tickers(project, d, d)
    return sm.candidate_codes(tickers, read_reference(project, "eodhd", "listings"))["code"].unique().sort().to_list()


def ingest_date(project: Project, d: date) -> list[IngestReport]:
    """Pull every source for one session (§13.2 step 3). Re-running the same date is a no-op."""
    reports = ingest_reference(project, date.today(), d)
    px = EodhdPrices(project.sources, project.settings.eodhd_api_key, active_codes(project, d))
    rep = IngestReport("eodhd", "eod")
    write_eod(px.fetch(d, d), project.raw_dir, rep, only_dates={d})
    rep.missing = list(px.missing)
    reports.append(rep)
    if known_ciks(project):
        reports.append(edgar_incremental(project, d))
    wm = load_watermarks(project)
    if wm.get("eodhd_eod", "") < d.isoformat():
        save_watermarks(project, {"eodhd_eod": d.isoformat()})
    return reports
