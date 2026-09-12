"""Staging orchestration (blueprint §13.4, stage 'stage'): raw -> point-in-time tables.

Staging is a pure function of the raw store (DECISIONS D-010): every run rebuilds every staged
table, so a rerun is bit-identical and a fixed override flows through everything downstream.
Fundamentals are cached per CIK, keyed by the raw vintage file and a hash of the concept map, so
a daily run only recomputes companies that filed.
"""

from __future__ import annotations

import functools
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from eqrisk.calendar import TradingCalendar, get_calendar
from eqrisk.config import Project, split_concept
from eqrisk.frames import as_float, as_int
from eqrisk.log import get_logger
from eqrisk.staging import fundamentals_pit as fpit
from eqrisk.staging.corp_actions import unconfirmed_splits
from eqrisk.staging.industry import assign_industries, read_industries, read_overrides, read_sic_map
from eqrisk.staging.mcap import OVERRIDE, build_mcap, clean_counts, override_rows
from eqrisk.staging.membership import daily_membership, membership_eras, parse_components
from eqrisk.staging.rawio import cik_partitions, read_edgar, read_eod, read_reference
from eqrisk.staging.returns import build_prices, risk_free
from eqrisk.staging.security_master import (
    EXCEPTION_SCHEMA,
    candidate_codes,
    issuer_ciks,
    read_cik_links,
    read_ticker_map,
    resolve,
)
from eqrisk.staging.universe import build_universe, members_by_sid
from eqrisk.store import atomic_write_bytes, refresh_catalog, replace_table, write_parquet

log = get_logger(__name__)

# table -> date column used for year partitions (None: single file)
STAGED_TABLES: dict[str, str | None] = {
    "security_master": None, "ticker_history": None, "security_codes": None, "issuer_ciks": None,
    "exceptions": None, "membership": "date", "rf": None, "prices": "date", "fundamentals_pit": None,
    "mcap": "date", "industry": None, "universe": "date",
}


def _available_fn(cal: TradingCalendar, lag_sessions: int) -> functools._lru_cache_wrapper[date]:
    @functools.cache
    def available(filed: date) -> date:
        d = cal.next_session(filed)
        return cal.offset(d, lag_sessions - 1) if lag_sessions > 1 else d

    return available


def stage_fundamentals(project: Project, ciks: list[int], cal: TradingCalendar) -> pl.DataFrame:
    cfg = project.config
    cache = project.staged_dir / "_cache" / "fundamentals"
    fingerprint = hashlib.sha256(json.dumps({
        "concepts": project.concepts.model_dump(mode="json"), "durations": cfg.fundamentals.model_dump(mode="json"),
        "forms": project.sources.edgar.forms, "lag": cfg.descriptors.fundamentals.availability_lag_sessions,
    }, sort_keys=True).encode()).hexdigest()[:16]
    index_path = cache / "index.json"
    index: dict[str, str] = json.loads(index_path.read_text()) if index_path.exists() else {}
    avail = _available_fn(cal, cfg.descriptors.fundamentals.availability_lag_sessions)
    parts = cik_partitions(project.raw_dir, "companyfacts")
    frames, recomputed = [], 0
    for cik in sorted(set(ciks)):
        src = parts.get(cik)
        if src is None:
            continue
        key = f"{src.parent.name}/{src.name}|{fingerprint}"
        cpath = cache / f"cik={cik:010d}.parquet"
        if index.get(str(cik)) == key and cpath.exists():
            frames.append(pl.read_parquet(cpath))
            continue
        pit = fpit.company_pit(pl.read_parquet(src), project.concepts, cfg.fundamentals,
                               project.sources.edgar.forms, avail)
        write_parquet(pit, cpath)
        index[str(cik)] = key
        frames.append(pit)
        recomputed += 1
    atomic_write_bytes(json.dumps(index, indent=1, sort_keys=True).encode(), index_path)
    log.info("fundamentals staged", ciks=len(frames), recomputed=recomputed)
    frames = [f for f in frames if f.height]
    return pl.concat(frames) if frames else pl.DataFrame(schema=fpit.PIT_SCHEMA)


def to_issuers(pit: pl.DataFrame, links: pl.DataFrame) -> pl.DataFrame:
    """Relabel every registrant's rows with its issuer id, so predecessor filings extend history."""
    return (pit.join(links.select("cik", "issuer_id"), on="cik", how="inner")
            .with_columns(cik=pl.col("issuer_id")).drop("issuer_id").select(list(fpit.PIT_SCHEMA)))


def _filing_spans(raw_dir: Path, forms: list[str]) -> pl.DataFrame:
    paths = [str(p) for p in cik_partitions(raw_dir, "companyfacts").values()]
    if not paths:
        return pl.DataFrame(schema={"cik": pl.Int64, "first_filed": pl.Date, "last_filed": pl.Date,
                                    "n_filings": pl.UInt32})
    return (pl.scan_parquet(paths).filter(pl.col("form").is_in(forms))
            .group_by("cik").agg(first_filed=pl.col("filed").min(), last_filed=pl.col("filed").max(),
                                 n_filings=pl.col("accn").n_unique()).collect())


def _code_names(listings: pl.DataFrame) -> dict[str, str]:
    named = listings.drop_nulls("name").sort("delisted").unique("code", keep="first")
    return dict(zip(named["code"].to_list(), named["name"].to_list(), strict=True))


def run_staging(project: Project, end: date | None = None) -> dict[str, Any]:
    cfg, raw = project.config, project.raw_dir
    cal = get_calendar(cfg.calendar)
    eod = read_eod(raw, cfg.history.price_start, end)
    if eod.height == 0:
        raise FileNotFoundError("no raw prices; run `eqrisk backfill --stage ingest` first")
    last_raw = eod["date"].max()
    assert isinstance(last_raw, date)
    end = min(end, last_raw) if end else last_raw
    sessions = cal.sessions(cfg.history.price_start, end)
    log.info("staging", start=str(sessions[0]), end=str(end), sessions=len(sessions), eod_rows=eod.height)

    comps = parse_components(read_reference(raw, "fja05680", "components"))
    daily = daily_membership(comps, sessions)
    eras = membership_eras(daily, sessions)
    listings = read_reference(raw, "eodhd", "listings")
    cands = candidate_codes(eras["ticker"].unique().to_list(), listings)
    code_dates = (eod.filter(pl.col("close") > 0).select("code", "date")
                  .join(pl.DataFrame({"date": sessions}, schema={"date": pl.Date}), on="date", how="semi"))
    companies = read_edgar(raw, "submissions")
    lookup = read_reference(raw, "edgar", "cik_lookup")
    spans = _filing_spans(raw, project.sources.edgar.forms)
    sm = resolve(
        eras=eras, cands=cands, code_dates=code_dates, cal=cal, last_session=sessions[-1],
        current=read_reference(raw, "fja05680", "current"),
        sec_tickers=read_reference(raw, "edgar", "company_tickers"), cik_lookup=lookup,
        companies=companies, filing_spans=spans, overrides=read_ticker_map(project.overrides_dir / "ticker_map.csv"),
        dollar_volume=eod.select("code", "date", dv=pl.col("close") * pl.col("volume")),
        cfg=cfg.security_master, code_names=_code_names(listings))
    links, link_exc = issuer_ciks(sm.master, sm.ticker_history, companies, lookup, spans,
                                  read_cik_links(project.resolve(cfg.security_master.cik_links_file)),
                                  cfg.security_master)
    log.info("security master", sids=sm.master.height, eras=sm.ticker_history.height,
             exceptions=sm.exceptions.height, predecessor_links=int((links["role"] != "primary").sum()))

    # Fundamentals, relabelled to issuers (predecessor registrants included).
    pit = to_issuers(stage_fundamentals(project, links["cik"].unique().to_list(), cal), links)
    avail = _available_fn(cal, cfg.descriptors.fundamentals.availability_lag_sessions)
    shares_ovr = pl.read_csv(project.resolve(cfg.security_master.shares_overrides_file), comment_prefix="#",
                             try_parse_dates=True, schema_overrides={"cik": pl.Int64, "shares": pl.Float64,
                                                                     "source": pl.String, "note": pl.String})
    shares_pit = pl.concat([pit.filter(pl.col("item") == "shares_out"), override_rows(shares_ovr, avail)])
    order = [OVERRIDE] + ["{}:{}".format(*split_concept(c)) for c in project.concepts.items["shares_out"].chain]

    # Returns; then splits that share counts contradict are re-run as non-splits.
    rf = risk_free(read_reference(raw, "fred", cfg.sources.risk_free.series), sessions)
    prices = build_prices(eod, sm.codes, rf, sessions, cfg.qa)
    smc, stale_m = cfg.security_master, cfg.descriptors.fundamentals.max_staleness_months
    clean0, _ = clean_counts(prices, sm.master, shares_pit, stale_m, smc)
    forced = unconfirmed_splits(prices, sm.master.select("sid", "cik").drop_nulls(), clean0, cfg.qa.corp_actions)
    if forced.height:
        prices = build_prices(eod, sm.codes, rf, sessions, cfg.qa, force_distribution=forced)
    mcap, dropped = build_mcap(prices, sm.master, shares_pit, pit.filter(pl.col("item") == "public_float"), order,
                               stale_m, smc.public_float_max_age_months, cfg.qa.prices.mcap_continuity_tol, smc)

    industries_tbl = read_industries(project.overrides_dir / "industries.csv")
    parents = dict(zip(industries_tbl["industry"].to_list(), industries_tbl["parent"].to_list(), strict=True))
    ind, ind_exc = assign_industries(sm.master, sm.ticker_history,
                                     read_sic_map(project.resolve(cfg.industries.map_file)),
                                     read_overrides(project.resolve(cfg.industries.overrides_file)), industries_tbl)
    members = members_by_sid(daily, sm.ticker_history)
    universe, thin_exc = build_universe(members, prices, mcap, ind, sm.master, parents, sessions, cfg)

    split_exc = forced.join(sm.codes.select("code", "sid").unique("code"), on="code", how="left").select(
        ticker=pl.col("code"), start=pl.col("date"), end=pl.col("date"), issue=pl.lit("split_reclassified"),
        detail=pl.lit("share counts did not move by the split ratio: not a split"))
    share_exc = dropped.select(ticker=pl.col("cik").cast(pl.String), start=pl.col("period_end"),
                               end=pl.col("filed"), issue=pl.lit("share_count_outlier"),
                               detail=pl.format("{}: {} reported {} shares", pl.col("reason"), pl.col("concept"),
                                                pl.col("value")))
    vol_exc = (prices.filter(pl.col("volume_basis_err") > cfg.qa.corp_actions.volume_basis_tol).group_by("code")
               .agg(start=pl.col("date").min(), end=pl.col("date").max(), n=pl.len(),
                    worst=pl.col("volume_basis_err").max())
               .select(ticker=pl.col("code"), start="start", end="end", issue=pl.lit("volume_basis_unmatched"),
                       detail=pl.format("{} rows; vendor adjustment up to {} (log) from every split basis",
                                        pl.col("n"), pl.col("worst").round(3))))
    exceptions = pl.concat([
        sm.exceptions.with_columns(area=pl.lit("identity")), link_exc.with_columns(area=pl.lit("identity")),
        ind_exc.with_columns(area=pl.lit("industry")), thin_exc.with_columns(area=pl.lit("thin_industry")),
        split_exc.select(list(EXCEPTION_SCHEMA)).with_columns(area=pl.lit("corp_actions")),
        vol_exc.select(list(EXCEPTION_SCHEMA)).with_columns(area=pl.lit("corp_actions")),
        share_exc.select(list(EXCEPTION_SCHEMA)).with_columns(area=pl.lit("shares")),
    ])

    tables = {"security_master": sm.master, "ticker_history": sm.ticker_history, "security_codes": sm.codes,
              "issuer_ciks": links, "exceptions": exceptions, "membership": members, "rf": rf, "prices": prices,
              "fundamentals_pit": pit, "mcap": mcap, "industry": ind, "universe": universe}
    for name, df in tables.items():
        replace_table(df, project.staged_dir / name, STAGED_TABLES[name])
    refresh_catalog(project.catalog_path, {n: project.staged_dir / n for n in tables})
    return staging_report(tables)


def staging_report(t: dict[str, pl.DataFrame]) -> dict[str, Any]:
    u, px, mc = t["universe"], t["prices"], t["mcap"]
    per_day = u.group_by("date").agg(cov=pl.len(), estu=pl.col("in_estu").sum())
    last = u["date"].max()
    return {
        "range": [str(u["date"].min()), str(last)],
        "sids": t["security_master"].height, "eras": t["ticker_history"].height,
        "predecessor_links": int((t["issuer_ciks"]["role"] != "primary").sum()),
        "exceptions": dict(t["exceptions"].group_by("issue").len().sort("issue").iter_rows()),
        "coverage_per_day": [as_int(per_day["cov"].min(), 0), as_int(per_day["cov"].max(), 0)],
        "estu_per_day": [as_int(per_day["estu"].min(), 0), as_int(per_day["estu"].max(), 0)],
        "exclusions_last_day": dict(u.filter(pl.col("date") == last).group_by("exclusion_reason").len()
                                    .drop_nulls("exclusion_reason").iter_rows()),
        "exclusions_all": dict(u.group_by("exclusion_reason").len().drop_nulls("exclusion_reason").iter_rows()),
        "coverage_with_mcap": round(u.filter(pl.col("mcap").is_not_null()).height / max(u.height, 1), 4),
        "share_sources": dict(mc.group_by("shares_source").len().drop_nulls("shares_source").iter_rows()),
        "price_flags": dict(px.group_by("price_flag").len().drop_nulls("price_flag").iter_rows()),
        "actions": dict(px.group_by("action").len().filter(pl.col("action") != "").iter_rows()),
        "mcap_flags": dict(mc.group_by("mcap_flag").len().drop_nulls("mcap_flag").iter_rows()),
        "max_mcap": as_float(mc["mcap_issuer"].max(), 0.0),
        "fundamentals_rows": t["fundamentals_pit"].height,
        "fundamentals_issuers": t["fundamentals_pit"]["cik"].n_unique(),
    }
