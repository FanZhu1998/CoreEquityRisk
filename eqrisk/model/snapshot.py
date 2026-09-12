"""Risk model snapshots and the read-only model store (blueprint §3.2, §10).

The UI, notebooks and optimizers read the model only through ModelStore, which returns a
RiskModelSnapshot: exposures X, the monthly factor covariance F and monthly specific variances
for every coverage name on one date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import Project
from eqrisk.manifest import RunManifest, read_manifests
from eqrisk.model.exposures import STYLES
from eqrisk.model.regression import COUNTRY

LATEST_GOOD = "LATEST_GOOD.json"


@dataclass(frozen=True)
class RiskModelSnapshot:
    as_of: date
    model_id: str
    sids: np.ndarray                 # (N,)
    tickers: np.ndarray              # (N,)
    factors: list[str]               # (K,)
    groups: dict[str, np.ndarray]    # {"country": idx, "industry": idx, "style": idx}
    X: np.ndarray                    # (N, K) exposures at the close of as_of
    F: np.ndarray                    # (K, K) monthly-horizon factor covariance
    spec_var: np.ndarray             # (N,) monthly specific variance
    mcap: np.ndarray                 # (N,)
    in_estu: np.ndarray              # (N,) bool

    def asset_cov(self) -> np.ndarray:
        """Sigma = X F X' + diag(spec_var), monthly."""
        cov: np.ndarray = self.X @ self.F @ self.X.T + np.diag(self.spec_var)
        return cov

    def positions(self, tickers: list[str]) -> np.ndarray:
        where = {t: i for i, t in enumerate(self.tickers.tolist())}
        return np.array([where.get(t, -1) for t in tickers])

    def save(self, path: Path) -> None:
        arrays: dict[str, Any] = {
            "as_of": np.array(self.as_of.isoformat()), "model_id": np.array(self.model_id), "sids": self.sids,
            "tickers": self.tickers.astype(str), "factors": np.array(self.factors), "X": self.X, "F": self.F,
            "spec_var": self.spec_var, "mcap": self.mcap, "in_estu": self.in_estu,
            **{f"group_{k}": v for k, v in self.groups.items()}}
        with open(path, "wb") as fh:
            np.savez_compressed(fh, **arrays)

    @classmethod
    def load(cls, path: Path) -> RiskModelSnapshot:
        with np.load(path, allow_pickle=False) as z:
            return cls(as_of=date.fromisoformat(str(z["as_of"])), model_id=str(z["model_id"]), sids=z["sids"],
                       tickers=z["tickers"], factors=[str(f) for f in z["factors"]],
                       groups={k.removeprefix("group_"): z[k] for k in z.files if k.startswith("group_")},
                       X=z["X"], F=z["F"], spec_var=z["spec_var"], mcap=z["mcap"], in_estu=z["in_estu"])

    def same_as(self, other: RiskModelSnapshot) -> bool:
        arrays = ("sids", "tickers", "X", "F", "spec_var", "mcap", "in_estu")
        return (self.as_of == other.as_of and self.model_id == other.model_id and self.factors == other.factors
                and all(np.array_equal(getattr(self, a), getattr(other, a), equal_nan=a not in ("tickers", "in_estu",
                                                                                           "sids"))
                        for a in arrays)
                and self.groups.keys() == other.groups.keys()
                and all(np.array_equal(self.groups[k], other.groups[k]) for k in self.groups))


class ModelStore:
    """Read-only access to the model tables (Parquet), returning snapshots by date."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.model_dir = project.model_dir
        self.staged_dir = project.staged_dir

    def _scan(self, root: Path, name: str) -> pl.LazyFrame:
        path = root / name
        if not path.exists():
            raise FileNotFoundError(f"table {name!r} is missing under {root}")
        return pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=False)

    def dates(self) -> list[date]:
        """Dates with both a factor covariance and specific risk."""
        cov = self._scan(self.model_dir, "factor_risk_diag").select("date").unique().collect()["date"].to_list()
        spec = self._scan(self.model_dir, "specific_risk").select("date").unique().collect()["date"].to_list()
        return sorted(set(cov) & set(spec))

    def latest(self) -> date:
        """The LATEST_GOOD pointer when the pipeline has written one, else the newest complete date."""
        pointer = self.model_dir / LATEST_GOOD
        if pointer.exists():
            return date.fromisoformat(json.loads(pointer.read_text(encoding="utf-8"))["as_of"])
        return self.dates()[-1]

    def snapshot(self, as_of: date | None = None) -> RiskModelSnapshot:
        d = as_of or self.latest()
        on = pl.col("date") == d
        cov = self._scan(self.model_dir, "factor_cov").filter(on).collect()
        if cov.height == 0:
            raise LookupError(f"no factor covariance for {d}")
        names = set(cov["factor_i"].to_list()) | set(cov["factor_j"].to_list())
        inds = sorted(names - {COUNTRY} - set(STYLES))
        factors = [COUNTRY, *inds, *STYLES]
        pos = {f: i for i, f in enumerate(factors)}
        K = len(factors)
        F = np.zeros((K, K))
        i = cov["factor_i"].replace_strict(pos).to_numpy()
        j = cov["factor_j"].replace_strict(pos).to_numpy()
        F[i, j] = cov["cov_final"].to_numpy()
        F[j, i] = cov["cov_final"].to_numpy()
        ex = self._scan(self.model_dir, "exposures").filter(on).collect()
        sr = self._scan(self.model_dir, "specific_risk").filter(on).select("sid", "sigma_final").collect()
        uni = self._scan(self.staged_dir, "universe").filter(on).select("sid", "ticker", "in_estu", "mcap").collect()
        df = ex.join(sr, on="sid", how="inner").join(uni, on="sid", how="left").sort("sid")
        unknown = set(df["industry"].drop_nulls().to_list()) - set(inds)
        if unknown:
            raise ValueError(f"{d}: industries {sorted(unknown)} have no factor in the covariance")
        N = df.height
        X = np.zeros((N, K))
        X[:, 0] = 1.0
        X[np.arange(N), df["industry"].replace_strict(pos).to_numpy()] = 1.0
        X[:, 1 + len(inds):] = df.select(list(STYLES)).to_numpy()
        return RiskModelSnapshot(
            as_of=d, model_id=self.project.config.model_id, sids=df["sid"].to_numpy(),
            tickers=df["ticker"].fill_null("").to_numpy().astype(str), factors=factors,
            groups={"country": np.array([0]), "industry": np.arange(1, 1 + len(inds)),
                    "style": np.arange(1 + len(inds), K)},
            X=X, F=F, spec_var=df["sigma_final"].to_numpy() ** 2, mcap=df["mcap"].to_numpy(),
            in_estu=df["in_estu"].fill_null(False).to_numpy())

    # ---- read-only views for the workbench and the static viewer (§15) ----------------------

    def _on(self, root: Path, name: str, d: date) -> pl.DataFrame:
        return self._scan(root, name).filter(pl.col("date") == d).collect()

    def factor_returns(self) -> pl.DataFrame:
        """date, factor, f, t_stat, f_over_sigma for every regression."""
        return self._scan(self.model_dir, "factor_returns").collect().sort("date", "factor")

    def regression_stats(self) -> pl.DataFrame:
        return self._scan(self.model_dir, "regression_stats").collect().sort("date")

    def factor_risk(self) -> pl.DataFrame:
        """date, factor, vol_final, vol_pre_eigen (monthly), lambda_F, provisional."""
        return self._scan(self.model_dir, "factor_risk_diag").collect().sort("date", "factor")

    def vra(self) -> pl.DataFrame:
        """date, lambda_F, cs_vol (cross-sectional factor volatility), lambda_S."""
        f = self._scan(self.model_dir, "vra_factor").select("date", "lambda_F", "cs_vol").collect()
        s = self._scan(self.model_dir, "vra_specific").select("date", "lambda_S").collect()
        return f.join(s, on="date", how="full", coalesce=True).sort("date")

    def eigen(self, d: date | None = None) -> pl.DataFrame:
        """Eigen diagnostics (k, eigenvalue, v_k, gamma_k) of the last simulation on or before d."""
        e = self._scan(self.model_dir, "eigen_diag").collect()
        days = sorted(x for x in e["date"].unique().to_list() if d is None or x <= d)
        return e.filter(pl.col("date") == days[-1]).sort("k") if days else e.clear()

    def universe(self, d: date) -> pl.DataFrame:
        return self._on(self.staged_dir, "universe", d)

    def exposures(self, d: date) -> pl.DataFrame:
        """Wide exposures on d with each name's ticker."""
        tick = self.universe(d).select("sid", "ticker", "in_estu")
        return self._on(self.model_dir, "exposures", d).join(tick, on="sid", how="left")

    def exposure_history(self, sid: int) -> pl.DataFrame:
        return self._scan(self.model_dir, "exposures").filter(pl.col("sid") == sid).collect().sort("date")

    def exposure_qa(self) -> pl.DataFrame:
        return self._scan(self.model_dir, "exposure_qa").collect().sort("date", "style")

    def specific(self, d: date) -> pl.DataFrame:
        """Every specific-risk layer on d (monthly) with each name's ticker and ESTU flag."""
        tick = self.universe(d).select("sid", "ticker", "in_estu", "industry")
        return self._on(self.model_dir, "specific_risk", d).join(tick, on="sid", how="left")

    def omega(self, d: date) -> pl.DataFrame:
        """Pure factor portfolio summaries for the regression on d: gross, net, top holdings."""
        return self._on(self.model_dir, "omega_summary", d)

    def manifests(self) -> list[RunManifest]:
        return read_manifests(self.model_dir)

    def latest_good_pointer(self) -> dict[str, Any] | None:
        pointer = self.model_dir / LATEST_GOOD
        out: dict[str, Any] | None = json.loads(pointer.read_text(encoding="utf-8")) if pointer.exists() else None
        return out

    def watermarks(self) -> dict[str, Any]:
        path = self.project.raw_dir / "_state" / "watermarks.json"
        out: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return out

    def validation_dir(self) -> Path | None:
        """The most recent `eqrisk validate` report for this model, if any."""
        runs = [p for p in self.project.reports_dir.glob(f"validation_{self.project.config.model_id}_*")
                if (p / "summary.json").exists()]
        return max(runs, key=lambda p: (p / "summary.json").stat().st_mtime) if runs else None
