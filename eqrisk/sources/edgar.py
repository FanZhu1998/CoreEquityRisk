"""SEC EDGAR (blueprint §4.5, Appendix C.3): tickers, names, submissions, XBRL company facts.

Every request carries the declared User-Agent from SEC_USER_AGENT and stays under
`edgar.requests_per_second` (SEC's ceiling is 10). Company facts keep the `dei` and `us-gaap`
taxonomies in full, so adding a concept to the Appendix C chains never needs a re-pull.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Any

import polars as pl

from eqrisk.config import SourcesConfig
from eqrisk.ids import cik10, symbol_expr
from eqrisk.sources.base import HttpClient, NotFoundError, Source, SourceError

FACT_TAXONOMIES = ("dei", "us-gaap")
FACT_SCHEMA: dict[str, Any] = {
    "cik": pl.Int64, "taxonomy": pl.String, "concept": pl.String, "unit": pl.String,
    "start": pl.Date, "end": pl.Date, "val": pl.Float64, "accn": pl.String, "fy": pl.Int64,
    "fp": pl.String, "form": pl.String, "filed": pl.Date, "frame": pl.String,
}
COMPANY_SCHEMA: dict[str, Any] = {
    "cik": pl.Int64, "name": pl.String, "entity_type": pl.String, "sic": pl.Int64,
    "sic_description": pl.String, "tickers": pl.String, "exchanges": pl.String,
    "fiscal_year_end": pl.String, "state_of_incorporation": pl.String, "former_names": pl.String,
}
FILING_SCHEMA: dict[str, Any] = {
    "cik": pl.Int64, "accession": pl.String, "form": pl.String, "filing_date": pl.Date,
    "report_date": pl.Date, "acceptance": pl.String,
}


def _to_date(e: pl.Expr) -> pl.Expr:
    return e.str.to_date(strict=False)


def parse_companyfacts(j: dict[str, Any], cik: int) -> pl.DataFrame:
    cols: dict[str, list[Any]] = {k: [] for k in FACT_SCHEMA if k != "cik"}
    for tax, concepts in j.get("facts", {}).items():
        if tax not in FACT_TAXONOMIES:
            continue
        for concept, body in concepts.items():
            for unit, facts in body.get("units", {}).items():
                for f in facts:
                    cols["taxonomy"].append(tax)
                    cols["concept"].append(concept)
                    cols["unit"].append(unit)
                    cols["start"].append(f.get("start"))
                    cols["end"].append(f.get("end"))
                    cols["val"].append(f.get("val"))
                    cols["accn"].append(f.get("accn"))
                    cols["fy"].append(f.get("fy"))
                    cols["fp"].append(f.get("fp"))
                    cols["form"].append(f.get("form"))
                    cols["filed"].append(f.get("filed"))
                    cols["frame"].append(f.get("frame"))
    string_schema = {k: (pl.String if FACT_SCHEMA[k] == pl.Date else FACT_SCHEMA[k]) for k in cols}
    df = pl.DataFrame(cols, schema=string_schema, strict=False)
    return df.with_columns(_to_date(pl.col("start")), _to_date(pl.col("end")), _to_date(pl.col("filed")),
                           cik=pl.lit(cik, pl.Int64)).select(list(FACT_SCHEMA))


def parse_submissions(j: dict[str, Any], cik: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    sic = str(j.get("sic") or "").strip()
    company = pl.DataFrame([{
        "cik": cik, "name": j.get("name"), "entity_type": j.get("entityType"),
        "sic": int(sic) if sic.isdigit() else None, "sic_description": j.get("sicDescription"),
        "tickers": "|".join(j.get("tickers") or []), "exchanges": "|".join(str(x) for x in j.get("exchanges") or []),
        "fiscal_year_end": j.get("fiscalYearEnd"), "state_of_incorporation": j.get("stateOfIncorporation"),
        "former_names": json.dumps(j.get("formerNames") or [], sort_keys=True),
    }], schema=COMPANY_SCHEMA)
    rec = j.get("filings", {}).get("recent", {})
    n = len(rec.get("accessionNumber", []))
    filings = pl.DataFrame({
        "accession": rec.get("accessionNumber", []), "form": rec.get("form", []),
        "filing_date": rec.get("filingDate", []), "report_date": [x or None for x in rec.get("reportDate", [None] * n)],
        "acceptance": rec.get("acceptanceDateTime", []),
    }, schema={"accession": pl.String, "form": pl.String, "filing_date": pl.String,
               "report_date": pl.String, "acceptance": pl.String})
    filings = filings.with_columns(_to_date(pl.col("filing_date")), _to_date(pl.col("report_date")),
                                   cik=pl.lit(cik, pl.Int64)).select(list(FILING_SCHEMA))
    return company, filings


def parse_daily_index(text: str) -> pl.DataFrame:
    """EDGAR daily master index: 'CIK|Company Name|Form Type|Date Filed|File Name' after a dashed rule."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("----")) + 1
    except StopIteration:
        start = len(lines)
    rows = [line.split("|") for line in lines[start:] if line.count("|") == 4]
    df = pl.DataFrame({"cik": [r[0] for r in rows], "company": [r[1] for r in rows],
                       "form": [r[2] for r in rows], "filed": [r[3] for r in rows],
                       "filename": [r[4] for r in rows]},
                      schema={k: pl.String for k in ("cik", "company", "form", "filed", "filename")})
    return df.with_columns(pl.col("cik").cast(pl.Int64), pl.col("filed").str.to_date("%Y%m%d"))


def parse_cik_lookup(text: str) -> pl.DataFrame:
    """cik-lookup-data.txt lines 'NAME:0000123456:' -> (name, cik); a name may itself contain ':'."""
    s = pl.Series("line", text.splitlines())
    parts = s.str.extract_groups(r"^(.*):(\d{10}):\s*$")
    df = pl.DataFrame({"name": parts.struct.field("1"), "cik": parts.struct.field("2")})
    return df.drop_nulls().filter(pl.col("name").str.len_chars() > 0).with_columns(
        pl.col("cik").cast(pl.Int64)).unique(maintain_order=True)


class Edgar:
    """Thin client; the Source adapters below wrap it for the ingest interface."""

    def __init__(self, cfg: SourcesConfig, user_agent: str | None, http: HttpClient | None = None) -> None:
        if not user_agent:
            raise SourceError("SEC_USER_AGENT is not set in .env (SEC requires a contact in the User-Agent)")
        self.cfg = cfg.edgar
        self.http = http or HttpClient(cfg.http, cfg.edgar.requests_per_second,
                                       headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    def company_tickers(self) -> pl.DataFrame:
        j = self.http.get_json(f"{self.cfg.www_url}/files/company_tickers.json")
        rows = list(j.values())
        df = pl.DataFrame({"cik": [int(r["cik_str"]) for r in rows], "ticker": [r["ticker"] for r in rows],
                           "title": [r["title"] for r in rows]})
        return df.with_columns(symbol_expr(pl.col("ticker")).alias("ticker")).sort("ticker", "cik")

    def cik_lookup(self) -> pl.DataFrame:
        blob = self.http.get(f"{self.cfg.www_url}/Archives/edgar/cik-lookup-data.txt").content
        return parse_cik_lookup(blob.decode("latin-1"))

    def submissions(self, cik: int) -> tuple[pl.DataFrame, pl.DataFrame]:
        return parse_submissions(self.http.get_json(f"{self.cfg.data_url}/submissions/CIK{cik10(cik)}.json"), cik)

    def companyfacts(self, cik: int) -> pl.DataFrame:
        try:
            j = self.http.get_json(f"{self.cfg.data_url}/api/xbrl/companyfacts/CIK{cik10(cik)}.json")
        except NotFoundError:
            return pl.DataFrame(schema=FACT_SCHEMA)
        return parse_companyfacts(j, cik)

    def daily_index(self, d: date) -> pl.DataFrame:
        q = (d.month - 1) // 3 + 1
        url = f"{self.cfg.www_url}/Archives/edgar/daily-index/{d.year}/QTR{q}/master.{d:%Y%m%d}.idx"
        try:
            return parse_daily_index(self.http.get(url).content.decode("latin-1"))
        except NotFoundError:          # weekends, holidays, days not yet published (SEC answers 403)
            return parse_daily_index("")


class EdgarFacts(Source):
    name = "edgar"
    dataset = "companyfacts"

    def __init__(self, client: Edgar, ciks: Sequence[int]) -> None:
        self.client = client
        self.ciks = sorted(set(ciks))

    def fetch(self, start: date, end: date) -> pl.DataFrame:
        """Facts filed in [start, end] for the configured CIKs."""
        frames = [self.client.companyfacts(c) for c in self.ciks]
        df = pl.concat(frames) if frames else pl.DataFrame(schema=FACT_SCHEMA)
        return df.filter(pl.col("filed").is_between(start, end))
