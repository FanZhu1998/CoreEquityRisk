"""`eqrisk export-site` (blueprint §15.2): a static viewer that works offline.

site/ is site_template/ (index.html, app.js, analyzer.js, style.css) plus data/*.json for one
as-of date:
- the snapshot: X, F, specific variance, ESTU cap weights, industries
- up to `years` of factor-return, factor-volatility and volatility-regime history
- the latest run status
- the latest validation summary

Only model outputs are written, keyed by ticker: no vendor prices, volumes, market caps or
fundamentals. app.js draws its charts as SVG, with no external library, so the folder stays small
and runs from `python -m http.server` with no network. The blueprint suggests Plotly.js and
DuckDB-WASM, but they alone exceed the 5 MB budget.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from eqrisk.analytics.risk import market_portfolio
from eqrisk.config import Project
from eqrisk.model.snapshot import ModelStore, RiskModelSnapshot

TEMPLATE_FILES = ("index.html", "app.js", "analyzer.js", "style.css")
MARKER = ".eqrisk-site"
_DAYS_PER_YEAR = 365.25
_MONTHS_PER_YEAR = 12
_RECENT_RUNS = 30


def _g(values: Any, sig: int) -> list[float | None]:
    """Round to `sig` significant digits for JSON; NaN becomes null."""
    return [float(f"{v:.{sig}g}") if np.isfinite(v) else None for v in np.asarray(values, dtype=float).ravel()]


def snapshot_payload(snap: RiskModelSnapshot) -> dict[str, Any]:
    ind = snap.groups["industry"]
    Xi = snap.X[:, ind]
    industry = [snap.factors[ind[j]] if Xi[i, j] > 0 else None for i, j in enumerate(Xi.argmax(axis=1))]
    return {"as_of": snap.as_of.isoformat(), "model_id": snap.model_id, "factors": list(snap.factors),
            "groups": {k: [int(i) for i in v] for k, v in snap.groups.items()},
            "tickers": [str(t) for t in snap.tickers], "industry": industry,
            "in_estu": [bool(b) for b in snap.in_estu], "capw": _g(market_portfolio(snap), 10),
            "X": [_g(row, 8) for row in snap.X], "F": [_g(row, 12) for row in snap.F],
            "spec_var": _g(snap.spec_var, 10)}


def snapshot_from_payload(p: dict[str, Any]) -> RiskModelSnapshot:
    """The exported arrays as a snapshot, so Python analytics can run on exactly what the browser sees."""
    return RiskModelSnapshot(
        as_of=date.fromisoformat(p["as_of"]), model_id=p["model_id"], sids=np.arange(len(p["tickers"])),
        tickers=np.array(p["tickers"]), factors=list(p["factors"]),
        groups={k: np.array(v, dtype=np.int64) for k, v in p["groups"].items()},
        X=np.array(p["X"], dtype=float), F=np.array(p["F"], dtype=float), spec_var=np.array(p["spec_var"], dtype=float),
        mcap=np.array([np.nan if c is None else c for c in p["capw"]], dtype=float),
        in_estu=np.array(p["in_estu"], dtype=bool))


def _wide(df: pl.DataFrame, value: str, factors: list[str]) -> tuple[list[str], dict[str, np.ndarray]]:
    w = df.pivot(on="factor", index="date", values=value).sort("date")
    return [d.isoformat() for d in w["date"].to_list()], {f: w[f].to_numpy() for f in factors if f in w.columns}


def history_payload(store: ModelStore, snap: RiskModelSnapshot, years: int) -> dict[str, Any]:
    d = snap.as_of
    window = pl.col("date").is_between(d - timedelta(days=round(years * _DAYS_PER_YEAR)), d)
    ann = float(np.sqrt(_MONTHS_PER_YEAR))
    fr_dates, fr = _wide(store.factor_returns().filter(window), "f", snap.factors)
    vol_dates, vol = _wide(store.factor_risk().filter(window), "vol_final", snap.factors)
    vra = store.vra().filter(window)
    r2 = store.regression_stats().filter(window & (pl.col("status") == "ok"))
    return {
        "dates": fr_dates, "cum": {f: _g(np.cumprod(1.0 + np.nan_to_num(v)) - 1.0, 5) for f, v in fr.items()},
        "vol_dates": vol_dates, "vol_ann": {f: _g(v * ann, 4) for f, v in vol.items()},
        "vra_dates": [x.isoformat() for x in vra["date"].to_list()], "lambda_F": _g(vra["lambda_F"].to_numpy(), 4),
        "lambda_S": _g(vra["lambda_S"].to_numpy(), 4), "cs_vol": _g(vra["cs_vol"].to_numpy(), 4),
        "r2_dates": [x.isoformat() for x in r2["date"].to_list()], "r2": _g(r2["r2_w"].to_numpy(), 4),
    }


def status_payload(store: ModelStore) -> dict[str, Any]:
    runs = [m for m in store.manifests() if m.command.startswith("run-daily")][-_RECENT_RUNS:]
    return {"latest_good": store.latest_good_pointer(), "latest": str(store.latest()),
            "runs": [{"as_of": str(m.as_of), "status": m.status,
                      "started_at": m.started_at.isoformat(timespec="seconds"),
                      "failed_gates": [g["gate"] for g in m.gates.get("results", []) if not g["ok"]],
                      "gates": m.gates.get("results", [])} for m in runs]}


def specific_payload(store: ModelStore, snap: RiskModelSnapshot) -> dict[str, Any]:
    sp = store.specific(snap.as_of)
    order = pl.DataFrame({"sid": snap.sids, "_i": np.arange(len(snap.sids))})
    aligned = order.join(sp, on="sid", how="left").sort("_i")
    ann = float(np.sqrt(_MONTHS_PER_YEAR))
    return {col: _g(aligned[col].to_numpy() * (ann if col.startswith("sigma") else 1.0), 5)
            for col in ("sigma_ts", "sigma_str", "sigma_blend", "sigma_final", "gamma")}


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def validation_payload(store: ModelStore) -> dict[str, Any] | None:
    vdir = store.validation_dir()
    if vdir is None:
        return None
    summary = json.loads((vdir / "summary.json").read_text(encoding="utf-8"))
    return {"start": summary["start"], "end": summary["end"], "periods": summary["periods"],
            "scorecard": summary["scorecard"], "external": summary["external"],
            "factor": _csv(vdir / "factor.csv"), "eigen": _csv(vdir / "eigen.csv"),
            "specific_deciles": _csv(vdir / "specific_deciles.csv"), "market": _csv(vdir / "market.csv")}


def export_site(project: Project, as_of: date | None = None, years: int | None = None,
                out: Path | None = None) -> tuple[Path, int]:
    """Render the viewer for `as_of` (default LATEST_GOOD). Returns (folder, total bytes)."""
    store = ModelStore(project)
    snap = store.snapshot(as_of)
    cfg = project.config.outputs.export_site
    out = out or project.resolve(cfg.out)
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).exists():
            raise FileExistsError(f"{out} exists and was not written by export-site; refusing to replace it")
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    for name in TEMPLATE_FILES:
        shutil.copy2(project.root / "site_template" / name, out / name)
    (out / MARKER).write_text("written by `eqrisk export-site`; the whole folder is replaced on each export\n",
                              encoding="utf-8")
    payloads = {"snapshot": snapshot_payload(snap), "history": history_payload(store, snap, years or cfg.years),
                "status": status_payload(store), "specific": specific_payload(store, snap),
                "validation": validation_payload(store)}
    for name, payload in payloads.items():
        (out / "data" / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":"), default=str),
                                                   encoding="utf-8")
    return out, sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
