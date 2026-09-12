"""Security master (blueprint §4.2): membership tickers -> securities, issuers, vendor codes, CIKs.

Ingest uses the candidate sets to decide what to pull (a cheap superset). Staging then resolves
every membership era from raw data alone (DECISIONS D-008):

0. Override spans (configs/overrides/ticker_map.csv) cut eras at their boundaries.
1. Vendor code: among the ticker's codes that price at least `min_era_coverage` of the era, a
   current listing for a live era and a superseded one (`XXX_old`) for an ended era. The vendor
   keeps an old company's history under a reused code while naming today's holder.
2. An era no single code prices is split, either where another ticker entered the index and
   carries the old history (IR -> TT in 2020), or where the ticker's own codes hand over.
3. Renames: an ended era the vendor does not price (FB after the move to META) links to the
   ticker that entered the index on the next session, if that ticker's code prices the old era.
4. CIK: override; else the constituent list's CIK (live eras); else SEC's ticker map when the
   title matches and the company filed during the era; else an exact company-name key against
   SEC's full name list, preferring current names, then filings during the era.
5. Securities: eras linked by renames, or sharing (CIK, code), or adjacent with the same CIK.
   sids are numbered by first appearance, so a rebuild reproduces them.
6. Share classes: an issuer's concurrent securities keep the most liquid as primary.
7. `issuer_ciks`: an issuer's CIK plus predecessor registrants (holding-company
   reorganizations such as BlackRock 2024 or Disney 2019), found by name or listed in
   configs/overrides/cik_links.csv, so fundamentals reach back before the new CIK first filed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from eqrisk.calendar import TradingCalendar
from eqrisk.config import SecurityMasterCfg
from eqrisk.ids import name_key, name_key_expr, symbol_expr, token_similarity

OLD_SUFFIX_RE = r"(?i)_old\d*$"
FAR_PAST, FAR_FUTURE = date(1900, 1, 1), date(2999, 12, 31)
_EPOCH = date(1970, 1, 1)

EXCEPTION_SCHEMA: dict[str, Any] = {"ticker": pl.String, "start": pl.Date, "end": pl.Date,
                                    "issue": pl.String, "detail": pl.String}
LINK_SCHEMA: dict[str, Any] = {"issuer_id": pl.Int64, "cik": pl.Int64, "role": pl.String}


# --------------------------------------------------------------------------- #
# Candidates (used by ingest)                                                 #
# --------------------------------------------------------------------------- #


def candidate_codes(tickers: Iterable[str], listings: pl.DataFrame) -> pl.DataFrame:
    """Every EODHD code that could carry a membership ticker's history.

    That is the ticker itself (current or delisted) and every superseded holder of it
    (`XXX_old`, `XXX_old1`, ...). A ticker with no listing still gets its own code as a
    fallback, because the vendor sometimes serves history for symbols its lists omit.
    """
    t = pl.DataFrame({"ticker": sorted(set(tickers))}, schema={"ticker": pl.String})
    lst = listings.with_columns(base=symbol_expr(pl.col("code").str.replace(OLD_SUFFIX_RE, "")))
    hits = t.join(lst, left_on="ticker", right_on="base", how="inner").select(
        "ticker", "code", "name", "type", "exchange", "delisted")
    fallback = t.join(hits.select("ticker").unique(), on="ticker", how="anti").with_columns(
        code=pl.col("ticker").str.replace_all(".", "-", literal=True),
        name=pl.lit(None, pl.String), type=pl.lit(None, pl.String),
        exchange=pl.lit(None, pl.String), delisted=pl.lit(None, pl.Boolean))
    return pl.concat([hits, fallback], how="vertical_relaxed").unique(["ticker", "code"]).sort("ticker", "code")


def candidate_ciks(tickers: Iterable[str], current: pl.DataFrame, sec_tickers: pl.DataFrame,
                   cik_lookup: pl.DataFrame, names: Iterable[str]) -> pl.DataFrame:
    """Every CIK that could be the issuer behind a membership ticker: (cik, via).

    Sources, in order of trust: the current constituent list's CIK column, SEC's current
    ticker map, and exact name-key matches of the vendor's company names against SEC's full
    name list (which also covers delisted companies).
    """
    tset = sorted(set(tickers))
    a = (current.with_columns(symbol_expr(pl.col("ticker")))
         .filter(pl.col("ticker").is_in(tset) & pl.col("cik").is_not_null())
         .select(pl.col("cik").cast(pl.Int64), via=pl.lit("fja_current")))
    b = sec_tickers.filter(pl.col("ticker").is_in(tset)).select(pl.col("cik").cast(pl.Int64), via=pl.lit("sec_ticker"))
    keys = (pl.DataFrame({"name": list(names)}, schema={"name": pl.String}).drop_nulls()
            .select(key=name_key_expr(pl.col("name"))).filter(pl.col("key") != "").unique())
    c = (cik_lookup.with_columns(key=name_key_expr(pl.col("name")))
         .join(keys, on="key", how="inner").select(pl.col("cik").cast(pl.Int64), via=pl.lit("name_key")))
    return pl.concat([a, b, c]).unique("cik", keep="first", maintain_order=True)


# --------------------------------------------------------------------------- #
# Override files                                                              #
# --------------------------------------------------------------------------- #


def read_ticker_map(path: Any) -> pl.DataFrame:
    schema = {"ticker": pl.String, "start_date": pl.Date, "end_date": pl.Date, "eodhd_code": pl.String,
              "cik": pl.Int64, "share_class": pl.String, "note": pl.String}
    try:
        df = pl.read_csv(path, comment_prefix="#", schema_overrides=schema, try_parse_dates=True)
    except (FileNotFoundError, pl.exceptions.NoDataError):
        return pl.DataFrame(schema=schema)
    return df.with_columns(symbol_expr(pl.col("ticker")))


def read_cik_links(path: Any) -> pl.DataFrame:
    schema = {"cik": pl.Int64, "predecessor_cik": pl.Int64, "note": pl.String}
    try:
        return pl.read_csv(path, comment_prefix="#", schema_overrides=schema)
    except (FileNotFoundError, pl.exceptions.NoDataError):
        return pl.DataFrame(schema=schema)


# --------------------------------------------------------------------------- #
# Resolution                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecurityMaster:
    master: pl.DataFrame           # one row per sid
    ticker_history: pl.DataFrame   # one row per (possibly split) membership era
    codes: pl.DataFrame            # sid -> vendor code validity windows
    exceptions: pl.DataFrame       # every era that needs a human (rule 7)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _days(d: date) -> int:
    return (d - _EPOCH).days


def _date_index(code_dates: pl.DataFrame) -> dict[str, np.ndarray]:
    """code -> sorted day numbers of sessions the code prices."""
    out: dict[str, np.ndarray] = {}
    for (code,), g in code_dates.group_by("code"):
        out[str(code)] = np.sort(g["date"].cast(pl.Int32).to_numpy())
    return out


@dataclass
class _Ctx:
    idx: dict[str, np.ndarray]
    cal: TradingCalendar
    cfg: SecurityMasterCfg
    last: date
    cand_map: dict[str, list[dict[str, Any]]]
    names: dict[str, str]

    def cov(self, code: str | None, start: date, end: date, n: int | None = None) -> float:
        arr = self.idx.get(code) if code else None
        n = self.cal.count(start, end) if n is None else n
        if arr is None or n <= 0:
            return 0.0
        hits = np.searchsorted(arr, _days(end), "right") - np.searchsorted(arr, _days(start), "left")
        return float(hits) / n

    def ok(self, cov: float) -> bool:
        return cov >= self.cfg.min_era_coverage


def _is_old(code: str) -> bool:
    return re.search(OLD_SUFFIX_RE, code) is not None


def _override_for(overrides: list[dict[str, Any]], e: dict[str, Any]) -> dict[str, Any] | None:
    for o in overrides:
        if o["ticker"] != e["ticker"]:
            continue
        if (o["start_date"] or FAR_PAST) <= e["end"] and (o["end_date"] or FAR_FUTURE) >= e["start"]:
            return o
    return None


def _split_at_overrides(E: list[dict[str, Any]], ovr: list[dict[str, Any]],
                        cal: TradingCalendar) -> list[dict[str, Any]]:
    out = []
    for e in E:
        cuts: set[date] = set()
        for o in ovr:
            if o["ticker"] != e["ticker"]:
                continue
            if o["start_date"]:
                s = o["start_date"] if cal.is_session(o["start_date"]) else cal.next_session(o["start_date"])
                if e["start"] < s <= e["end"]:
                    cuts.add(s)
            if o["end_date"]:
                s = cal.next_session(o["end_date"])
                if e["start"] < s <= e["end"]:
                    cuts.add(s)
        bounds = [e["start"], *sorted(cuts)]
        for k, b in enumerate(bounds):
            end = e["end"] if k == len(bounds) - 1 else cal.prev_session(bounds[k + 1])
            out.append({**e, "start": b, "end": end, "n_sessions": cal.count(b, end)})
    return out


def _choose_own(e: dict[str, Any], ctx: _Ctx) -> None:
    o = e["override"]
    if o and o["eodhd_code"]:
        code = o["eodhd_code"]
        e.update(code=code, coverage=ctx.cov(code, e["start"], e["end"], e["n_sessions"]),
                 vendor_name=ctx.names.get(code), code_method="override")
        return
    live = e["end"] >= ctx.last
    opts = []
    for c in ctx.cand_map.get(e["ticker"], []):
        cov = ctx.cov(c["code"], e["start"], e["end"], e["n_sessions"])
        old, gone = _is_old(c["code"]), bool(c["delisted"])
        pref = (0 if not (old or gone) else 1 if not old else 2) if live else (0 if old else 1 if gone else 2)
        opts.append((not ctx.ok(cov), pref, -cov, c["code"], cov, c))
    if opts:
        best = min(opts, key=lambda x: x[:4])
        e.update(code=best[5]["code"], coverage=best[4], vendor_name=best[5]["name"], code_method="coverage")
    else:
        e.update(code=None, coverage=0.0, vendor_name=None, code_method="none")


def _hidden_rename(e: dict[str, Any], by_start: dict[date, list[dict[str, Any]]],
                   ctx: _Ctx) -> list[dict[str, Any]] | None:
    """Split where another ticker entered the index and its code carries this era's early history."""
    own = ctx.cand_map.get(e["ticker"], [])
    best: tuple[float, date, dict[str, Any], float, float, dict[str, Any]] | None = None
    for s, others in by_start.items():
        if not (e["start"] < s <= e["end"]):
            continue
        pre_end = ctx.cal.prev_session(s)
        scored = [(ctx.cov(c["code"], s, e["end"]), c) for c in own]
        if not scored:
            continue
        scov, sc = max(scored, key=lambda x: x[0])
        if not ctx.ok(scov):
            continue
        for o in others:
            if o["ticker"] == e["ticker"] or not o.get("code") or not ctx.ok(o["coverage"]):
                continue
            pcov = ctx.cov(o["code"], e["start"], pre_end)
            if ctx.ok(pcov) and (best is None or pcov + scov > best[0]):
                best = (pcov + scov, s, o, pcov, scov, sc)
    if best is None:
        return None
    _, s, o, pcov, scov, sc = best
    pre_end = ctx.cal.prev_session(s)
    return [{**e, "end": pre_end, "n_sessions": ctx.cal.count(e["start"], pre_end), "code": o["code"],
             "coverage": pcov, "vendor_name": o["vendor_name"], "code_method": f"renamed_to:{o['ticker']}", "link": o},
            {**e, "start": s, "n_sessions": ctx.cal.count(s, e["end"]), "code": sc["code"], "coverage": scov,
             "vendor_name": sc["name"], "code_method": "coverage", "link": None}]


def _code_switch(e: dict[str, Any], ctx: _Ctx) -> list[dict[str, Any]] | None:
    """Split where one of the ticker's own codes hands over to another."""
    own = ctx.cand_map.get(e["ticker"], [])
    best: tuple[float, date, dict[str, Any], float, dict[str, Any], float] | None = None
    for B in own:
        arr = ctx.idx.get(B["code"])
        if arr is None:
            continue
        i = int(np.searchsorted(arr, _days(e["start"]) + 1, "left"))
        if i >= len(arr):
            continue
        s = _EPOCH + timedelta(days=int(arr[i]))
        if s > e["end"]:
            continue
        scov = ctx.cov(B["code"], s, e["end"])
        if not ctx.ok(scov):
            continue
        pre_end = ctx.cal.prev_session(s)
        for A in own:
            if A["code"] == B["code"]:
                continue
            pcov = ctx.cov(A["code"], e["start"], pre_end)
            if ctx.ok(pcov) and (best is None or pcov + scov > best[0]):
                best = (pcov + scov, s, A, pcov, B, scov)
    if best is None:
        return None
    _, s, A, pcov, B, scov = best
    pre_end = ctx.cal.prev_session(s)
    return [{**e, "end": pre_end, "n_sessions": ctx.cal.count(e["start"], pre_end), "code": A["code"],
             "coverage": pcov, "vendor_name": A["name"], "code_method": "code_switch", "link": None},
            {**e, "start": s, "n_sessions": ctx.cal.count(s, e["end"]), "code": B["code"], "coverage": scov,
             "vendor_name": B["name"], "code_method": "coverage", "link": None}]


def _former_name_keys(companies: pl.DataFrame) -> pl.DataFrame:
    """(key, cik, current) for every current and former SEC name of the pulled companies."""
    names: list[tuple[str, int, bool]] = []
    for row in companies.select("cik", "name", "former_names").iter_rows(named=True):
        names.append((row["name"] or "", row["cik"], True))
        for fn in json.loads(row["former_names"] or "[]"):
            names.append((fn.get("name") or "", row["cik"], False))
    df = pl.DataFrame({"name": [n for n, _, _ in names], "cik": [c for _, c, _ in names],
                       "current": [k for _, _, k in names]},
                      schema={"name": pl.String, "cik": pl.Int64, "current": pl.Boolean})
    return df.select(key=name_key_expr(pl.col("name")), cik=pl.col("cik"), current=pl.col("current"))


def _keymap(cik_lookup: pl.DataFrame, companies: pl.DataFrame, keys: set[str]) -> dict[str, set[int]]:
    keyed = pl.concat([
        cik_lookup.select(key=name_key_expr(pl.col("name")), cik=pl.col("cik").cast(pl.Int64)),
        _former_name_keys(companies).select("key", "cik"),
    ]).filter(pl.col("key").is_in(sorted(keys))).unique()
    out: dict[str, set[int]] = defaultdict(set)
    for r in keyed.iter_rows(named=True):
        out[r["key"]].add(int(r["cik"]))
    return out


def resolve(*, eras: pl.DataFrame, cands: pl.DataFrame, code_dates: pl.DataFrame, cal: TradingCalendar,
            last_session: date, current: pl.DataFrame, sec_tickers: pl.DataFrame, cik_lookup: pl.DataFrame,
            companies: pl.DataFrame, filing_spans: pl.DataFrame, overrides: pl.DataFrame,
            dollar_volume: pl.DataFrame, cfg: SecurityMasterCfg,
            code_names: dict[str, str] | None = None) -> SecurityMaster:
    cand_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, str] = dict(code_names or {})
    for c in cands.to_dicts():
        cand_map[c["ticker"]].append(c)
        if c["name"]:
            names.setdefault(c["code"], c["name"])
    ctx = _Ctx(idx=_date_index(code_dates), cal=cal, cfg=cfg, last=last_session, cand_map=cand_map, names=names)
    ovr = overrides.to_dicts()
    exc: list[dict[str, Any]] = []

    # 0-1. override cuts, then each era's own vendor code
    E = _split_at_overrides(eras.sort("start", "ticker").to_dicts(), ovr, cal)
    for e in E:
        e["override"] = _override_for(ovr, e)
        e["link"] = None
        _choose_own(e, ctx)

    # 2. split eras no single code prices (decide on the unsplit list, then apply)
    by_start: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for e in E:
        by_start[e["start"]].append(e)
    plan = {id(e): (_hidden_rename(e, by_start, ctx) or _code_switch(e, ctx))
            for e in E if not ctx.ok(e["coverage"]) and not e["override"] and e["n_sessions"] > 1}
    E = [piece for e in E for piece in (plan.get(id(e)) or [e])]

    # 3. renames: newest eras first, so a chain A -> B -> C settles B before A links to it
    starts: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for e in E:
        starts[e["start"]].append(e)
    for e in sorted(E, key=lambda e: e["end"], reverse=True):
        if ctx.ok(e["coverage"]) or e["end"] >= last_session or e["link"] is not None:
            continue
        best: tuple[float, dict[str, Any]] | None = None
        for o in starts.get(cal.next_session(e["end"]), []):
            if o["ticker"] == e["ticker"]:
                continue
            cov = ctx.cov(o["code"], e["start"], e["end"], e["n_sessions"])
            if ctx.ok(cov) and (best is None or cov > best[0]):
                best = (cov, o)
        if best is not None:
            o = best[1]
            e.update(code=o["code"], coverage=best[0], vendor_name=o["vendor_name"],
                     code_method=f"renamed_to:{o['ticker']}", link=o)
    for e in E:
        if not ctx.ok(e["coverage"]):
            exc.append({"ticker": e["ticker"], "start": e["start"], "end": e["end"], "issue": "unpriced_era",
                        "detail": f"best vendor code {e['code']} prices {e['coverage']:.0%} of sessions"})

    # 4. CIK per era
    cur_map = dict(zip(current.with_columns(symbol_expr(pl.col("ticker")))["ticker"].to_list(),
                       current["cik"].to_list(), strict=True))
    sec_map: dict[str, tuple[int, str]] = {}
    for r in sec_tickers.iter_rows(named=True):
        sec_map.setdefault(r["ticker"], (int(r["cik"]), r["title"]))
    comp_tickers = {r["cik"]: {s for s in (r["tickers"] or "").replace("-", ".").split("|") if s}
                    for r in companies.select("cik", "tickers").iter_rows(named=True)}
    cur_key = dict(zip(companies["cik"].to_list(),
                       companies.select(k=name_key_expr(pl.col("name").fill_null("")))["k"].to_list(), strict=True))
    spans = {r["cik"]: (r["first_filed"], r["last_filed"], r["n_filings"]) for r in filing_spans.iter_rows(named=True)}
    slack = timedelta(days=cfg.filing_slack_days)
    keymap = _keymap(cik_lookup, companies, {name_key(e["vendor_name"]) for e in E if e["vendor_name"]} - {""})

    def filed_during(c: int, e: dict[str, Any]) -> bool:
        sp = spans.get(c)
        return sp is not None and sp[0] <= e["end"] + slack and sp[1] >= e["start"] - slack

    def pick_cik(e: dict[str, Any]) -> tuple[int | None, str]:
        o = e["override"]
        if o and o["cik"]:
            return int(o["cik"]), "override"
        t, name, live = e["ticker"], e["vendor_name"], e["end"] >= last_session
        if live and cur_map.get(t):
            return int(cur_map[t]), "fja_current"
        if t in sec_map:
            cik, title = sec_map[t]
            in_time = filed_during(cik, e) or (live and cik not in spans)
            similar = bool(name) and token_similarity(name, title) >= cfg.name_match_min_similarity
            if in_time and (similar or (not name and live)):
                return cik, "sec_ticker"
        if not name:
            return None, "unresolved"
        # Only registrants that filed periodic reports during the era qualify: that rejects shells
        # ("PerkinElmer Holdings") and today's holder of a name the vendor re-used (LandBridge for
        # L Brands' "LB"). Among filers, a current name outranks a former one ("Viacom Inc.").
        key = name_key(name)
        filers = sorted(c for c in keymap.get(key, set()) if filed_during(c, e))
        if not filers:
            return None, "unresolved"
        if len(filers) == 1:
            return filers[0], "name_key"
        current_named = [c for c in filers if cur_key.get(c) == key]
        if len(current_named) == 1:
            return current_named[0], "name_key+current_name"
        rest = current_named or filers
        tick = [c for c in rest if t in comp_tickers.get(c, set())]
        if len(tick) == 1:
            return tick[0], "name_key+ticker"
        return max(rest, key=lambda c: (c in has_sic, spans[c][1], spans[c][2])), "name_key_ambiguous"

    has_sic = set(companies.filter(pl.col("sic").is_not_null())["cik"].to_list())
    for e in sorted(E, key=lambda e: e["end"], reverse=True):
        if e["link"] is not None:
            e["cik"], e["cik_method"] = e["link"]["cik"], "rename"
            continue
        e["cik"], e["cik_method"] = pick_cik(e)
    # An ended era with no identifiable filer may be a rename the vendor priced under the old code
    # (L Brands' history under "LB", renamed BBWI): link it to the next session's entrant.
    for e in sorted(E, key=lambda e: e["end"], reverse=True):
        if e["cik"] is not None or e["end"] >= last_session or e["link"] is not None:
            continue
        best_o: tuple[float, dict[str, Any]] | None = None
        for o in starts.get(cal.next_session(e["end"]), []):
            if o["ticker"] == e["ticker"] or o.get("cik") is None:
                continue
            cov = ctx.cov(o["code"], e["start"], e["end"], e["n_sessions"])
            if ctx.ok(cov) and (best_o is None or cov > best_o[0]):
                best_o = (cov, o)
        if best_o is not None:
            o = best_o[1]
            e.update(code=o["code"], coverage=best_o[0], vendor_name=o["vendor_name"], link=o,
                     code_method=f"renamed_to:{o['ticker']}", cik=o["cik"], cik_method="rename")
    for e in E:
        if e["cik"] is None:
            exc.append({"ticker": e["ticker"], "start": e["start"], "end": e["end"], "issue": "no_cik",
                        "detail": f"vendor name {e['vendor_name']!r}"})
        elif e["cik_method"] == "name_key_ambiguous":
            exc.append({"ticker": e["ticker"], "start": e["start"], "end": e["end"], "issue": "ambiguous_cik",
                        "detail": f"{e['vendor_name']!r} -> chose {e['cik']}"})

    # 5. securities
    pos = {id(e): i for i, e in enumerate(E)}
    uf = _UnionFind(len(E))
    for i, e in enumerate(E):
        if e["link"] is not None and id(e["link"]) in pos:
            uf.union(i, pos[id(e["link"])])
    first_of: dict[tuple[int, str], int] = {}
    for i, e in enumerate(E):
        if e["cik"] is not None and e["code"] is not None:
            first_of.setdefault((e["cik"], e["code"]), i)
            uf.union(first_of[(e["cik"], e["code"])], i)
    by_cik: dict[int, list[int]] = defaultdict(list)
    for i, e in enumerate(E):
        if e["cik"] is not None:
            by_cik[e["cik"]].append(i)
    for ids in by_cik.values():
        ids.sort(key=lambda i: E[i]["start"])
        for a, b in zip(ids, ids[1:], strict=False):
            if E[b]["start"] == cal.next_session(E[a]["end"]):
                uf.union(a, b)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(E)):
        groups[uf.find(i)].append(i)
    ordered = sorted(groups.values(),
                     key=lambda ids: (min(E[i]["start"] for i in ids), min(E[i]["ticker"] for i in ids)))

    # 6. rows
    th_rows, code_rows, master_rows = [], [], []
    comp_rows = {r["cik"]: r for r in companies.iter_rows(named=True)}
    for sid, ids in enumerate(ordered, start=1):
        es = sorted((E[i] for i in ids), key=lambda e: e["start"])
        segs: list[dict[str, Any]] = []
        for k, e in enumerate(es):
            th_rows.append({"sid": sid, "ticker": e["ticker"], "start_date": e["start"],
                            "end_date": None if e["end"] >= last_session else e["end"],
                            "eodhd_code": e["code"], "cik": e["cik"], "coverage": e["coverage"],
                            "code_method": e["code_method"], "cik_method": e["cik_method"],
                            "vendor_name": e["vendor_name"]})
            if e["code"] is None:
                continue
            frm = FAR_PAST if k == 0 else cal.next_session(es[k - 1]["end"])
            to = FAR_FUTURE if k == len(es) - 1 else e["end"]
            if segs and segs[-1]["code"] == e["code"]:
                segs[-1]["valid_to"] = to
            else:
                segs.append({"sid": sid, "code": e["code"], "valid_from": frm, "valid_to": to})
        if segs:
            segs[-1]["valid_to"] = FAR_FUTURE
        code_rows.extend(segs)
        ciks = [e["cik"] for e in es if e["cik"] is not None]
        cik = ciks[-1] if ciks else None
        comp = comp_rows.get(cik, {}) if cik is not None else {}
        master_rows.append({
            "sid": sid, "issuer_id": cik if cik is not None else -sid, "cik": cik,
            "name": comp.get("name") or es[-1]["vendor_name"], "eodhd_code": es[-1]["code"],
            "first_date": es[0]["start"], "last_date": None if es[-1]["end"] >= last_session else es[-1]["end"],
            "sic": comp.get("sic"), "tickers": "|".join(dict.fromkeys(e["ticker"] for e in es)),
            "n_eras": len(es)})

    th = pl.DataFrame(th_rows, schema={"sid": pl.Int64, "ticker": pl.String, "start_date": pl.Date,
                                       "end_date": pl.Date, "eodhd_code": pl.String, "cik": pl.Int64,
                                       "coverage": pl.Float64, "code_method": pl.String, "cik_method": pl.String,
                                       "vendor_name": pl.String})
    codes = pl.DataFrame(code_rows, schema={"sid": pl.Int64, "code": pl.String, "valid_from": pl.Date,
                                            "valid_to": pl.Date})
    master = pl.DataFrame(master_rows, schema={
        "sid": pl.Int64, "issuer_id": pl.Int64, "cik": pl.Int64, "name": pl.String, "eodhd_code": pl.String,
        "first_date": pl.Date, "last_date": pl.Date, "sic": pl.Int64, "tickers": pl.String, "n_eras": pl.Int64})
    master = _share_classes(master, th, codes, dollar_volume, cal, last_session, cfg)
    exceptions = pl.DataFrame(exc, schema=EXCEPTION_SCHEMA).sort("issue", "ticker", "start")
    return SecurityMaster(master=master, ticker_history=th.sort("sid", "start_date"), codes=codes,
                          exceptions=exceptions)


def _share_classes(master: pl.DataFrame, th: pl.DataFrame, codes: pl.DataFrame, dollar_volume: pl.DataFrame,
                   cal: TradingCalendar, last_session: date, cfg: SecurityMasterCfg) -> pl.DataFrame:
    """primary_class / linked_sid: among an issuer's securities that are index members at the same
    time, the one with the higher median dollar volume over the last `primary_class_lookback_days`
    shared sessions is primary (DECISIONS D-008)."""
    spans = th.group_by("sid").agg(lo=pl.col("start_date").min(),
                                   hi=pl.col("end_date").fill_null(last_session).max())
    m = master.join(spans, on="sid", how="left")
    primary = {s: True for s in m["sid"].to_list()}
    linked: dict[int, int] = {}
    dv = dollar_volume.join(codes, on="code").filter(
        pl.col("date").is_between(pl.col("valid_from"), pl.col("valid_to")))
    for (_iss,), grp in m.filter(pl.col("cik").is_not_null()).group_by("cik"):
        if grp.height < 2:
            continue
        rows = grp.sort("sid").to_dicts()
        lo, hi = max(r["lo"] for r in rows), min(r["hi"] for r in rows)
        if lo > hi:
            continue                                  # sequential, not concurrent
        window = cal.sessions(lo, hi)[-cfg.primary_class_lookback_days:]
        med = (dv.filter(pl.col("sid").is_in([r["sid"] for r in rows]) & pl.col("date").is_in(window))
               .group_by("sid").agg(pl.col("dv").median()))
        score = dict(zip(med["sid"].to_list(), med["dv"].to_list(), strict=True))
        best = max(rows, key=lambda r: (score.get(r["sid"]) or 0.0, -r["sid"]))["sid"]
        for r in rows:
            if r["sid"] != best:
                primary[r["sid"]] = False
                linked[r["sid"]] = best
    return master.with_columns(
        primary_class=pl.col("sid").replace_strict(primary, return_dtype=pl.Boolean),
        linked_sid=pl.col("sid").replace_strict(linked, default=None, return_dtype=pl.Int64))


def issuer_ciks(master: pl.DataFrame, ticker_history: pl.DataFrame, companies: pl.DataFrame,
                cik_lookup: pl.DataFrame, filing_spans: pl.DataFrame, manual: pl.DataFrame,
                cfg: SecurityMasterCfg) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(issuer_id, cik, role) for every issuer: its own CIK, plus a predecessor registrant when the
    CIK first filed more than `predecessor_gap_days` after the security appeared. Predecessors are
    found by the issuer's SEC name or its vendor names, and must have filed before the new CIK.
    Manual links come from cik_links.csv. Returns (links, exceptions)."""
    spans = {r["cik"]: (r["first_filed"], r["last_filed"], r["n_filings"])
             for r in filing_spans.iter_rows(named=True)}
    cur_key = dict(zip(companies["cik"].to_list(),
                       companies.select(k=name_key_expr(pl.col("name").fill_null("")))["k"].to_list(), strict=True))
    vendor = ticker_history.group_by("cik").agg(pl.col("vendor_name").drop_nulls().unique()).drop_nulls("cik")
    vendor_keys = {r["cik"]: {name_key(v) for v in r["vendor_name"]} for r in vendor.iter_rows(named=True)}
    issuers = master.filter(pl.col("cik").is_not_null()).group_by("cik").agg(first=pl.col("first_date").min())
    need = set(cur_key.values()) | {k for ks in vendor_keys.values() for k in ks}
    keymap = _keymap(cik_lookup, companies, need - {""})
    gap, slack = timedelta(days=cfg.predecessor_gap_days), timedelta(days=cfg.filing_slack_days)
    rows: list[tuple[int, int, str]] = []
    exc: list[dict[str, Any]] = []
    for r in issuers.iter_rows(named=True):
        P, first = int(r["cik"]), r["first"]
        rows.append((P, P, "primary"))
        sp = spans.get(P)
        if sp is not None and sp[0] <= first + gap:
            continue
        keys = ({cur_key.get(P, "")} | vendor_keys.get(P, set())) - {""}
        pool = {c for k in keys for c in keymap.get(k, set())} - {P}
        pool = {c for c in pool if c in spans and spans[c][1] >= first - slack
                and (sp is None or spans[c][0] < sp[0])}
        if pool:
            rows.append((P, max(pool, key=lambda c: (spans[c][1], spans[c][2])), "predecessor"))
        elif P not in set(manual["cik"].to_list()):
            exc.append({"ticker": str(P), "start": first, "end": sp[0] if sp else None,
                        "issue": "no_predecessor",
                        "detail": f"CIK {P} files from {sp[0] if sp else 'never'}; security appears {first}"})
    for r in manual.iter_rows(named=True):
        rows.append((int(r["cik"]), int(r["predecessor_cik"]), "manual"))
    links = pl.DataFrame(rows, schema=LINK_SCHEMA, orient="row").unique(["issuer_id", "cik"], keep="first")
    return links.sort("issuer_id", "role"), pl.DataFrame(exc, schema=EXCEPTION_SCHEMA)
