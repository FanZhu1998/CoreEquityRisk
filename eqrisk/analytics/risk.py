"""Portfolio risk analytics (blueprint §10). Monthly units; x sqrt(12) annualizes a volatility.

For holdings h (and benchmark h_b; active h_a = h - h_b) with factor exposures x = X'h:
total variance x'Fx + h'Delta h; Euler factor contributions x_k (Fx)_k, summed by group;
x-sigma-rho contributions x_k sigma_k rho_{k,p}; MCTR = (XFx + Delta h) / sigma_p, whose
holdings-weighted sum is sigma_p; predicted betas vs the benchmark (or, without one, the
cap-weighted ESTU).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.kernels import risk_decomposition
from eqrisk.model.snapshot import RiskModelSnapshot

MONTHS_PER_YEAR = 12


@dataclass
class RiskReport:
    as_of: date
    active: bool
    sigma: float                  # monthly total (or active) volatility
    factor_var: float
    specific_var: float
    beta: float                   # predicted beta of the portfolio vs the benchmark
    factors: pl.DataFrame         # factor, group, exposure, vol, corr, xsr, contrib_var, pct_var
    groups: pl.DataFrame          # group, contrib_var, pct_var
    assets: pl.DataFrame          # sid, ticker, weight, mctr, contrib, pct_risk, beta

    @property
    def sigma_ann(self) -> float:
        return self.sigma * math.sqrt(MONTHS_PER_YEAR)


def market_portfolio(snap: RiskModelSnapshot) -> np.ndarray:
    """Cap weights over the ESTU."""
    w = np.where(snap.in_estu & np.isfinite(snap.mcap), snap.mcap, 0.0)
    out: np.ndarray = w / w.sum()
    return out


def portfolio_risk(snap: RiskModelSnapshot, h: np.ndarray, h_b: np.ndarray | None = None) -> RiskReport:
    w = h - h_b if h_b is not None else h
    X, F, spec = snap.X, snap.F, snap.spec_var
    K = len(snap.factors)
    group_of = np.empty(K, dtype=object)
    for g, idx in snap.groups.items():
        group_of[idx] = g
    d: dict[str, Any]
    if not np.any(w):
        zeros = np.zeros(K)
        d = {"sigma": 0.0, "exposures": zeros, "factor_contrib_var": zeros, "specific_var": 0.0,
             "mctr": np.zeros(len(w)), "xsr_corr": zeros}
    else:
        d = risk_decomposition(X, F, spec, w)
    sigma = float(d["sigma"])
    x = np.asarray(d["exposures"])
    vol = np.sqrt(np.diag(F))
    corr = np.asarray(d["xsr_corr"])
    contrib = np.asarray(d["factor_contrib_var"])
    total_var = sigma * sigma
    pct = contrib / total_var if total_var > 0 else np.zeros(K)
    factors = pl.DataFrame({"factor": snap.factors, "group": group_of.tolist(), "exposure": x, "vol": vol,
                            "corr": corr, "xsr": x * vol * corr, "contrib_var": contrib, "pct_var": pct})
    groups = (factors.group_by("group").agg(contrib_var=pl.col("contrib_var").sum())
              .vstack(pl.DataFrame({"group": ["specific"], "contrib_var": [float(d["specific_var"])]}))
              .with_columns(pct_var=pl.col("contrib_var") / total_var if total_var > 0 else pl.lit(0.0)))

    bench = h_b if h_b is not None else market_portfolio(snap)
    xb = X.T @ bench
    var_b = float(xb @ F @ xb + (bench * bench * spec).sum())
    betas = (X @ (F @ xb) + spec * bench) / var_b
    mctr = np.asarray(d["mctr"])
    assets = pl.DataFrame({"sid": snap.sids, "ticker": snap.tickers, "weight": w, "mctr": mctr,
                           "contrib": w * mctr, "pct_risk": w * mctr / sigma if sigma > 0 else np.zeros(len(w)),
                           "beta": betas})
    return RiskReport(as_of=snap.as_of, active=h_b is not None, sigma=sigma, factor_var=float(contrib.sum()),
                      specific_var=float(d["specific_var"]), beta=float(h @ betas), factors=factors,
                      groups=groups.sort("group"), assets=assets)


def holdings_vectors(snap: RiskModelSnapshot, holdings: pl.DataFrame) -> tuple[np.ndarray, np.ndarray | None,
                                                                                list[str]]:
    """(h, h_b or None, unmatched tickers) from a frame with ticker, weight[, bench_weight]."""
    pos = snap.positions(holdings["ticker"].to_list())
    unmatched = [t for t, p in zip(holdings["ticker"].to_list(), pos, strict=True) if p < 0]
    h = np.zeros(len(snap.sids))
    np.add.at(h, pos[pos >= 0], holdings["weight"].to_numpy()[pos >= 0])
    h_b = None
    if "bench_weight" in holdings.columns:
        h_b = np.zeros(len(snap.sids))
        np.add.at(h_b, pos[pos >= 0], holdings["bench_weight"].fill_null(0.0).to_numpy()[pos >= 0])
    return h, h_b, unmatched
