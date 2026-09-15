"""The engine's read surface for the desktop app: `eqrisk serve`, JSON lines on stdin and stdout.

The desktop app keeps one of these processes running and asks it for everything it shows, so a
page opens in milliseconds instead of paying Python's 3-6 second start-up on every click:

    -> {"id": 7, "method": "day", "params": {"date": "2026-09-11"}}
    <- {"id": 7, "ok": true, "result": {...}, "ms": 12}
    <- {"id": 7, "ok": false, "error": {"type": "LookupError", "message": "no factor covariance for ..."}}

The first line the server writes is {"event": "ready", ...}. Three rules keep it safe to leave
running (DECISIONS D-025):

- Read only. Everything goes through ModelStore and the staged tables. Jobs that change data run
  as separate `eqrisk` processes, so this process can never collide with a daily run.
- No keys. The desktop app starts it without API keys in its environment, and `keys` reports only
  whether .env sets each one. A value never enters a response.
- stdout carries protocol lines and nothing else. Logs go to stderr and logs/.

Results are cached until the model changes on disk (LATEST_GOOD, the run manifests, the staged
universe, the validation reports); `refresh` clears them outright.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO, cast

import numpy as np
import polars as pl

from eqrisk import __version__
from eqrisk.analytics.risk import MONTHS_PER_YEAR, RiskReport, holdings_vectors, portfolio_risk
from eqrisk.calendar import get_calendar
from eqrisk.config import Project, load_settings
from eqrisk.log import get_logger
from eqrisk.manifest import RunManifest
from eqrisk.model.exposures import STYLES
from eqrisk.model.regression import COUNTRY
from eqrisk.model.snapshot import LATEST_GOOD, ModelStore, RiskModelSnapshot
from eqrisk.optimize.service import METHODS, Method, OptimizeSpec, optimize_portfolio
from eqrisk.pipeline.daily import now_et, processed_dates, read_latest_good, target_session
from eqrisk.pipeline.export_site import history_payload, validation_payload
from eqrisk.store import latest_dated_dir, raw_partitions

log = get_logger(__name__)

PROTOCOL = 1
ANN = math.sqrt(MONTHS_PER_YEAR)        # monthly volatility -> annualized
DEFAULT_RUNS = 60                        # manifests `runs` returns unless asked for more
EXCEPTION_ROWS = 5000                    # rows `exceptions` returns; its counts cover every row
SNAPSHOTS_KEPT = 6                       # as-of dates whose snapshot stays in memory
_ZERO = 1e-12                            # an exposure below this is numerically zero
_HELD = 1e-6                             # an optimized weight below this is not a holding

# Reference datasets shown on the desktop's Data page: (label, source, dataset).
REFERENCE = [("S&P 500 membership", "fja05680", "components"), ("Risk-free rate", "fred", "DTB3"),
             ("Ken French factors", "famafrench", "daily"), ("Vendor symbol list", "eodhd", "listings"),
             ("SEC ticker map", "edgar", "company_tickers"), ("SEC company names", "edgar", "cik_lookup")]

Params = dict[str, Any]


def clean(x: Any) -> Any:
    """Make a result JSON-safe: numpy to Python, dates to ISO text, NaN and infinity to null."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (float, np.floating)):              # np.float64 subclasses float: convert it too
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, (date, datetime)):                   # datetime is a subclass of date
        return x.isoformat()
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, pl.DataFrame):
        return clean(x.to_dicts())
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [clean(v) for v in x]
    return str(x)


def _group(factor: str) -> str:
    return "country" if factor == COUNTRY else ("style" if factor in STYLES else "industry")


def _group_expr() -> pl.Expr:
    return (pl.when(pl.col("factor") == COUNTRY).then(pl.lit("country"))
            .when(pl.col("factor").is_in(list(STYLES))).then(pl.lit("style"))
            .otherwise(pl.lit("industry")).alias("group"))


def _cell(v: str) -> Any:
    """A CSV cell as the value it spells: bool, number, null or text (polars writes lowercase booleans)."""
    if v.lower() in ("", "none", "null", "nan"):
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return float(v)
    except ValueError:
        return v


def _dir_size(path: Path) -> int:
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(base, f))
    return total


def _run_row(m: RunManifest) -> dict[str, Any]:
    timings = m.counts.get("timings")
    secs = timings.get("total_s") if isinstance(timings, dict) else None
    if secs is None and m.finished_at is not None:
        secs = (m.finished_at - m.started_at).total_seconds()
    results = m.gates.get("results", [])
    return {"run_id": m.run_id, "command": m.command, "as_of": m.as_of, "status": m.status,
            "started_at": m.started_at, "finished_at": m.finished_at,
            "minutes": secs / 60 if secs else None, "gates": results,
            "fail_gates": [g["gate"] for g in results if not g.get("ok") and g.get("level") == "FAIL"],
            "warn_gates": [g["gate"] for g in results if not g.get("ok") and g.get("level") != "FAIL"],
            "config_hash": m.config_hash[:16], "git_sha": (m.git_sha or "")[:12], "git_dirty": m.git_dirty}


def _holdings_frame(rows: Any) -> pl.DataFrame:
    """[{ticker, weight[, bench_weight]}, ...] as the frame `holdings_vectors` expects."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("holdings must be a non-empty list of {ticker, weight[, bench_weight]}")
    tickers: list[str] = []
    weights: list[float] = []
    bench: list[float] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or "ticker" not in r or "weight" not in r:
            raise ValueError(f"holding {i} needs a ticker and a weight")
        tickers.append(str(r["ticker"]).strip().upper())
        weights.append(float(r["weight"]))
        bench.append(float(r.get("bench_weight") or 0.0))
    data: dict[str, list[Any]] = {"ticker": tickers, "weight": weights}
    if any(isinstance(r, dict) and r.get("bench_weight") is not None for r in rows):
        data["bench_weight"] = bench
    return pl.DataFrame(data)


def _report_payload(rep: RiskReport) -> dict[str, Any]:
    total = rep.sigma ** 2
    factors = (rep.factors.filter(pl.col("exposure").abs() > _ZERO)
               .with_columns(vol_ann=pl.col("vol") * ANN, xsr_ann=pl.col("xsr") * ANN)
               .sort(pl.col("xsr").abs(), descending=True)
               .select("factor", "group", "exposure", "vol_ann", "corr", "xsr_ann", "pct_var"))
    assets = (rep.assets.filter(pl.col("weight") != 0).with_columns(mctr_ann=pl.col("mctr") * ANN)
              .sort(pl.col("contrib").abs(), descending=True)
              .select("ticker", "weight", "mctr_ann", "pct_risk", "beta"))
    return {"active": rep.active, "sigma_ann": rep.sigma_ann,
            "factor_share": rep.factor_var / total if total else None,
            "specific_share": rep.specific_var / total if total else None, "beta": rep.beta,
            "groups": rep.groups.select("group", "pct_var"), "factors": factors, "assets": assets}


def _num(params: Params, key: str) -> float:
    if params.get(key) is None:
        raise ValueError(f"missing number {key!r}")
    return float(params[key])


def _method(value: Any) -> Method:
    if value not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    return cast(Method, value)


class Feed:
    """Every read the desktop app makes, cached until the model on disk changes."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.store = ModelStore(project)
        self._cache: dict[str, Any] = {}
        self._snaps: OrderedDict[date, RiskModelSnapshot] = OrderedDict()
        self._seen: tuple[float, ...] | None = None
        self.methods: dict[str, Callable[[Params], Any]] = {
            "hello": self.hello, "refresh": self.refresh, "status": self.status, "keys": self.keys,
            "dates": self.dates, "day": self.day, "history": self.history, "factor_risk": self.factor_risk,
            "exposures": self.exposures, "specific": self.specific, "validation": self.validation,
            "inventory": self.inventory, "exceptions": self.exceptions, "runs": self.runs,
            "config": self.config, "outputs": self.outputs, "portfolio": self.portfolio, "optimize": self.optimize,
        }

    # ---- dispatch and caching ------------------------------------------------------------------

    def call(self, method: str, params: Params) -> Any:
        fn = self.methods.get(method)
        if fn is None:
            raise KeyError(f"unknown method {method!r}; expected one of {sorted(self.methods)}")
        stamp = self._stamp()
        if stamp != self._seen:
            self._forget()
            self._seen = stamp
        return clean(fn(params))

    def _stamp(self) -> tuple[float, ...]:
        """Modification times that move whenever a run, a restage or a validation lands."""
        p = self.project
        watched = (p.model_dir / LATEST_GOOD, p.model_dir / "manifests", p.staged_dir / "universe",
                   p.reports_dir)
        return tuple(w.stat().st_mtime if w.exists() else 0.0 for w in watched)

    def _forget(self) -> None:
        self._cache.clear()
        self._snaps.clear()

    def _cached[T](self, key: str, build: Callable[[], T]) -> T:
        if key not in self._cache:
            self._cache[key] = build()
        return cast(T, self._cache[key])

    def _snapshot(self, d: date) -> RiskModelSnapshot:
        if d in self._snaps:
            self._snaps.move_to_end(d)
            return self._snaps[d]
        snap = self.store.snapshot(d)
        self._snaps[d] = snap
        while len(self._snaps) > SNAPSHOTS_KEPT:
            self._snaps.popitem(last=False)
        return snap

    def _processed(self) -> list[date]:
        return self._cached("processed", lambda: sorted(processed_dates(self.project)))

    def _manifests(self) -> list[RunManifest]:
        return self._cached("manifests", self.store.manifests)

    def _factor_returns(self) -> pl.DataFrame:
        return self._cached("factor_returns", self.store.factor_returns)

    def _regression_stats(self) -> pl.DataFrame:
        return self._cached("regression_stats", self.store.regression_stats)

    def _vra(self) -> pl.DataFrame:
        return self._cached("vra", self.store.vra)

    def _as_of(self, params: Params) -> date:
        """The requested date, else LATEST_GOOD: the date the viewer and snapshots default to."""
        raw = params.get("date")
        return date.fromisoformat(str(raw)) if raw else self.store.latest()

    # ---- methods -------------------------------------------------------------------------------

    def hello(self, _: Params) -> dict[str, Any]:
        return {"protocol": PROTOCOL, "engine_version": __version__, "model_id": self.project.config.model_id,
                "root": self.project.root, "python": sys.version.split()[0], "pid": os.getpid(),
                "methods": sorted(self.methods)}

    def refresh(self, _: Params) -> dict[str, Any]:
        self._forget()
        return {"cleared": True}

    def keys(self, _: Params) -> list[dict[str, Any]]:
        """Which keys .env sets, re-read on every call. Presence only: never a value."""
        present = load_settings(self.project.root).presence()
        return [{"env": name.upper(), "present": bool(ok)} for name, ok in present.items()]

    def status(self, _: Params) -> dict[str, Any]:
        """Where the model stands: last processed session, what is pending, the latest daily run."""
        p = self.project
        cal = get_calendar(p.config.calendar)
        done = self._processed()
        last = done[-1] if done else None
        now = now_et()
        target = target_session(p, now)
        pending = cal.sessions(cal.next_session(last), target) if last is not None and last < target else []
        runs = [m for m in self._manifests() if m.command.startswith("run-daily")]
        pointer = read_latest_good(p)
        return {"model_id": p.config.model_id, "engine_version": __version__,
                "now_et": now.replace(tzinfo=None).isoformat(timespec="minutes"),
                "last": last, "target": target, "pending": pending,
                "next_session": cal.next_session(last) if last is not None else None,
                "ready_after_et": p.config.pipeline.vendor_ready_after_et,
                "latest_good": date.fromisoformat(pointer["as_of"]) if pointer else last,
                "daily_runs": len(runs), "last_run": _run_row(runs[-1]) if runs else None,
                "keys": self.keys({})}

    def dates(self, _: Params) -> dict[str, Any]:
        days = self._cached("dates", self.store.dates)
        return {"dates": days, "latest": self.store.latest() if days else None}

    def day(self, params: Params) -> dict[str, Any]:
        """One session: the market (country) return, fit, regime multipliers and every factor's move."""
        done = self._processed()
        raw = params.get("date")
        d = date.fromisoformat(str(raw)) if raw else (done[-1] if done else None)
        if d is None:
            raise LookupError("no processed sessions yet; run `eqrisk backfill` first")

        def build() -> dict[str, Any]:
            fr = self._factor_returns().filter(pl.col("date") == d)
            rs = self._regression_stats().filter(pl.col("date") == d)
            v = self._vra().filter(pl.col("date") <= d).tail(1)
            country = fr.filter(pl.col("factor") == COUNTRY)
            row = rs.row(0, named=True) if rs.height else {}
            return {"date": d, "country": country["f"][0] if country.height else None,
                    "r2": row.get("r2_w"), "n": row.get("n"), "cond": row.get("cond"),
                    "fit_status": row.get("status"),
                    "lambda_F": v["lambda_F"][0] if v.height else None,
                    "lambda_S": v["lambda_S"][0] if v.height else None,
                    "moves": fr.select("factor", _group_expr(), "f", "t_stat", "f_over_sigma")}

        return self._cached(f"day:{d}", build)

    def history(self, params: Params) -> dict[str, Any]:
        """Cumulative factor returns, factor volatility, regime multipliers and fit over `years`."""
        years = int(params.get("years") or self.project.config.outputs.export_site.years)
        d = self._as_of(params)
        return self._cached(f"history:{d}:{years}", lambda: {
            "as_of": d, "years": years, **history_payload(self.store, self._snapshot(d), years)})

    def factor_risk(self, params: Params) -> dict[str, Any]:
        """Annualized factor volatilities (before and after the eigen adjustment) and correlations."""
        d = self._as_of(params)

        def build() -> dict[str, Any]:
            snap = self._snapshot(d)
            vol = np.sqrt(np.diag(snap.F))
            corr = snap.F / np.outer(vol, vol)
            diag = self._cached("factor_risk", self.store.factor_risk).filter(pl.col("date") == d)
            pre = dict(zip(diag["factor"].to_list(), diag["vol_pre_eigen"].to_list(), strict=True))
            rows = [{"factor": f, "group": _group(f), "vol_ann": vol[i] * ANN,
                     "vol_pre_eigen_ann": pre[f] * ANN if pre.get(f) is not None else None}
                    for i, f in enumerate(snap.factors)]
            return {"as_of": d, "factors": rows, "corr": np.round(corr, 4)}

        return self._cached(f"factor_risk:{d}", build)

    def exposures(self, params: Params) -> dict[str, Any]:
        d = self._as_of(params)

        def build() -> dict[str, Any]:
            ex = self.store.exposures(d)
            styles = [s for s in STYLES if s in ex.columns]
            cols = [c for c in ("sid", "ticker", "industry", "in_estu", *styles) if c in ex.columns]
            return {"as_of": d, "styles": styles, "rows": ex.select(cols).sort("ticker", nulls_last=True)}

        return self._cached(f"exposures:{d}", build)

    def specific(self, params: Params) -> dict[str, Any]:
        """Every specific-risk layer on the date, annualized, with each name's ticker and industry."""
        d = self._as_of(params)

        def build() -> dict[str, Any]:
            sp = self.store.specific(d)
            sig = [c for c in ("sigma_ts", "sigma_str", "sigma_blend", "sigma_final") if c in sp.columns]
            sp = sp.with_columns([pl.col(c) * ANN for c in sig])
            cols = [c for c in ("sid", "ticker", "industry", "in_estu", *sig, "gamma") if c in sp.columns]
            return {"as_of": d, "rows": sp.select(cols).sort("ticker", nulls_last=True)}

        return self._cached(f"specific:{d}", build)

    def validation(self, _: Params) -> dict[str, Any] | None:
        """The latest `eqrisk validate` report: scorecard, external checks and the battery tables."""

        def build() -> dict[str, Any] | None:
            payload = validation_payload(self.store)
            vdir = self.store.validation_dir()
            if payload is None or vdir is None:
                return None
            for table in ("factor", "eigen", "specific_deciles", "market"):
                payload[table] = [{k: _cell(v) for k, v in row.items()} for row in payload[table]]
            return {**payload, "dir": vdir, "figures": sorted(p.name for p in vdir.glob("*.png"))}

        return self._cached("validation", build)

    def inventory(self, _: Params) -> dict[str, Any]:
        """What is on disk: price partitions, EDGAR companies, reference snapshots, staged range."""

        def build() -> dict[str, Any]:
            p = self.project
            raw = p.raw_dir
            eod = raw_partitions(raw / "eodhd" / "eod", "date")
            refs = []
            for label, src, ds in REFERENCE:
                hit = latest_dated_dir(raw / src / ds)
                refs.append({"dataset": label, "source": src, "snapshot": hit[0] if hit else None})
            staged_last = None
            uni = p.staged_dir / "universe"
            if uni.exists():
                staged_last = pl.scan_parquet(str(uni / "**" / "*.parquet"), hive_partitioning=False).select(
                    pl.col("date").max()).collect().item()
            facts = raw / "edgar" / "companyfacts"
            return {"eod_first": eod[0][0] if eod else None, "eod_last": eod[-1][0] if eod else None,
                    "eod_sessions": len(eod),
                    "edgar_companies": sum(1 for _ in facts.glob("cik=*")) if facts.exists() else 0,
                    "refs": refs, "staged_last": staged_last, "watermarks": self.store.watermarks(),
                    "data_dir": p.data_dir, "data_bytes": _dir_size(p.data_dir)}

        return self._cached("inventory", build)

    def exceptions(self, params: Params) -> dict[str, Any]:
        """Data-quality exceptions from staging, counted by area and issue."""
        limit = int(params.get("limit") or EXCEPTION_ROWS)

        def build() -> dict[str, Any]:
            path = self.project.staged_dir / "exceptions"
            if not path.exists():
                return {"total": 0, "counts": [], "rows": []}
            exc = pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False).collect()
            keys = [c for c in ("area", "issue") if c in exc.columns]
            counts = exc.group_by(keys).len().sort("len", descending=True).rename({"len": "count"})
            return {"total": exc.height, "counts": counts, "rows": exc.sort(keys).head(limit)}

        return self._cached(f"exceptions:{limit}", build)

    def runs(self, params: Params) -> dict[str, Any]:
        """Run manifests, newest first: command, session, status, gates, timings, config and git."""
        limit = int(params.get("limit") or DEFAULT_RUNS)
        ms = self._manifests()
        return {"total": len(ms), "runs": [_run_row(m) for m in reversed(ms[-limit:])]}

    def config(self, _: Params) -> dict[str, Any]:
        p = self.project
        cfg = p.config
        fc, sr, src = cfg.factor_cov, cfg.specific_risk, cfg.sources
        return {"model_id": cfg.model_id, "preset": cfg.preset, "horizon_days": cfg.horizon_days,
                "config_hash": cfg.config_hash()[:16],
                "factor_cov": {"vol_half_life": fc.vol.half_life, "vol_nw_lags": fc.vol.nw_lags,
                               "corr_half_life": fc.corr.half_life, "corr_nw_lags": fc.corr.nw_lags,
                               "eigen_a": fc.eigen.a, "vra_half_life": fc.vra.half_life},
                "specific_risk": {"half_life": sr.ts.half_life, "shrinkage_q": sr.shrinkage.q},
                "sources": {"prices": src.prices, "fundamentals": src.fundamentals, "membership": src.membership,
                            "risk_free": src.risk_free.series},
                "paths": {"root": p.root, "config": p.config_path, "data": p.data_dir, "reports": p.reports_dir,
                          "logs": p.logs_dir},
                "vendor_ready_after_et": cfg.pipeline.vendor_ready_after_et}

    def outputs(self, _: Params) -> dict[str, Any]:
        """Where published outputs live: the static viewer (`export-site`) and snapshot files."""
        p = self.project

        def stamp(path: Path) -> str:
            return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")

        site = p.resolve(p.config.outputs.export_site.out)
        marker = site / "data" / "snapshot.json"
        exported = marker.exists()
        exports = p.data_dir / "exports"
        files = sorted(exports.glob("*.npz"), key=lambda f: f.stat().st_mtime, reverse=True) if exports.exists() else []
        snapshots = [{"name": f.name, "path": f, "bytes": f.stat().st_size, "modified": stamp(f)} for f in files]
        return {"site": {"path": site, "exported": exported,
                         "bytes": sum(f.stat().st_size for f in site.rglob("*") if f.is_file()) if exported else 0,
                         "modified": stamp(marker) if exported else None},
                "exports_dir": exports,
                "snapshots": snapshots}

    def portfolio(self, params: Params) -> dict[str, Any]:
        """Total (or, with bench_weight, active) risk of a holdings list, decomposed."""
        d = self._as_of(params)
        snap = self._snapshot(d)
        h, hb, unmatched = holdings_vectors(snap, _holdings_frame(params.get("holdings")))
        if not np.any(h):
            raise ValueError("none of the tickers is in the model on this date")
        return {"as_of": d, **_report_payload(portfolio_risk(snap, h, hb)), "unmatched": unmatched,
                "matched": int(np.count_nonzero(h))}

    def optimize(self, params: Params) -> dict[str, Any]:
        """Optimize against the cap-weighted estimation universe (`eqrisk.optimize.service`)."""
        d = self._as_of(params)
        snap = self._snapshot(d)
        alpha = params.get("alpha_factor") or None
        spec = OptimizeSpec(method=_method(params.get("method", "factor")), te_max_ann=_num(params, "te_max_ann"),
                            style_band=_num(params, "style_band"), ind_band=_num(params, "ind_band"),
                            w_max=_num(params, "w_max"), turnover_max=_num(params, "turnover_max"),
                            alpha_factor=str(alpha) if alpha else None,
                            alpha_per_unit=float(params.get("alpha_per_unit") or 0.0))
        res = optimize_portfolio(snap, spec)
        if res.weights is None or res.report is None:
            return {"as_of": d, "ok": False, "status": res.status}
        w, wb = res.weights, res.benchmark
        holdings = (pl.DataFrame({"ticker": snap.tickers, "weight": w, "benchmark": wb})
                    .filter(pl.col("weight") > _HELD).sort("weight", descending=True))
        return {"as_of": d, "ok": True, "status": res.status, **_report_payload(res.report),
                "names_held": int((w > _HELD).sum()), "turnover": float(np.abs(w - wb).sum()),
                "holdings": holdings}


def serve(project: Project, stdin: TextIO | None = None, stdout: TextIO | None = None,
          feed: Feed | None = None) -> int:
    """Answer requests until stdin closes or a `shutdown` request arrives. Returns requests served."""
    real = stdout is None
    inp = stdin if stdin is not None else io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    out = stdout if stdout is not None else io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n",
                                                             write_through=True)
    if real:
        sys.stdout = sys.stderr          # a stray print() anywhere must never corrupt the protocol stream
    feed = feed or Feed(project)

    def send(msg: dict[str, Any]) -> None:
        out.write(json.dumps(msg, separators=(",", ":"), allow_nan=False) + "\n")
        out.flush()

    send({"event": "ready", **clean(feed.hello({}))})
    served = 0
    for line in inp:
        text = line.strip()
        if not text:
            continue
        try:
            req = json.loads(text)
            if not isinstance(req, dict):
                raise ValueError("a request is a JSON object")
        except ValueError as exc:
            send({"id": None, "ok": False, "error": {"type": "BadRequest", "message": str(exc)}})
            continue
        rid, method, params = req.get("id"), str(req.get("method") or ""), req.get("params") or {}
        if method == "shutdown":
            send({"id": rid, "ok": True, "result": None})
            break
        t0 = time.perf_counter()
        try:
            if not isinstance(params, dict):
                raise ValueError("params must be a JSON object")
            result = feed.call(method, params)
            send({"id": rid, "ok": True, "result": result, "ms": round((time.perf_counter() - t0) * 1000)})
        except Exception as exc:          # every failure becomes a response; the server keeps running
            log.warning("feed request failed", method=method, error=f"{type(exc).__name__}: {exc}")
            send({"id": rid, "ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})
        served += 1
    return served
