"""Style exposures from raw descriptors, one cross-section per session (blueprint §6.3-§6.4).

Per session, over the coverage universe, with every moment taken on the ESTU:
  2. drop descriptor values beyond `error_robust_z` robust z-scores (data errors, counted),
     then clip to the mean +/- `clip_sigma` std (SIZE unclipped)
  3. standardize each descriptor (cap-weighted mean, equal-weighted std)
  4. combine into styles, renormalizing weights over each name's available descriptors
  5. re-standardize; nonlinear styles are cubes of standardized SIZE / BETA
  6. orthogonalize (RESVOL to BETA, NLSIZE to SIZE, NLBETA to BETA) by sqrt(cap)-weighted
     regression on the ESTU, trim, re-standardize
  7. impute missing values from a sqrt(cap)-weighted regression on industry dummies and SIZE
  8. re-impose orthogonality on the completed cross-section, then a final standardization, so
     the §17 acceptance (|rho| < 1e-8 against the target) holds exactly (DECISIONS D-012)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.kernels import orthogonalize, standardize, trim_sigma
from eqrisk.kernels.descriptors import robust_zscore, vif, weighted_corr
from eqrisk.model.panel import Panel

STYLES = ("BETA", "MOMENTUM", "SIZE", "EARNINGS_YIELD", "RESIDUAL_VOLATILITY", "GROWTH", "DIVIDEND_YIELD",
          "BOOK_TO_PRICE", "LEVERAGE", "LIQUIDITY", "NONLINEAR_SIZE", "NONLINEAR_BETA")
FUNDAMENTAL_STYLES = ("EARNINGS_YIELD", "GROWTH", "DIVIDEND_YIELD", "BOOK_TO_PRICE", "LEVERAGE")
NONLINEAR = {"NONLINEAR_SIZE": "SIZE", "NONLINEAR_BETA": "BETA"}
MIN_ESTU = 30          # below this a cross-section cannot be standardized meaningfully


def _weights[Name: str](configured: Mapping[Name, float]) -> dict[str, float]:
    """Configured descriptor weights keyed by name (the config types the keys as a Literal set)."""
    return {str(k): float(v) for k, v in configured.items()}


def style_weights(cfg: ModelConfig) -> dict[str, dict[str, float]]:
    d = cfg.descriptors
    return {"SIZE": {"LNCAP": 1.0}, "BETA": {"HBETA": 1.0}, "MOMENTUM": {"RSTR": 1.0},
            "RESIDUAL_VOLATILITY": _weights(d.resvol.weights), "EARNINGS_YIELD": _weights(d.earnings_yield.weights),
            "GROWTH": _weights(d.growth.weights), "DIVIDEND_YIELD": {"DTOP": 1.0}, "BOOK_TO_PRICE": {"BTOP": 1.0},
            "LEVERAGE": _weights(d.leverage.weights), "LIQUIDITY": _weights(d.liquidity.weights)}


def orthogonal_targets(cfg: ModelConfig) -> dict[str, list[str]]:
    d = cfg.descriptors
    return {"RESIDUAL_VOLATILITY": list(d.resvol.orthogonalize_to), "LIQUIDITY": list(d.liquidity.orthogonalize_to),
            "NONLINEAR_SIZE": list(d.nonlinear_size.orthogonalize_to),
            "NONLINEAR_BETA": list(d.nonlinear_beta.orthogonalize_to)}


@dataclass
class CrossSection:
    X: dict[str, np.ndarray]           # style -> (N,), NaN outside coverage
    imputed: dict[str, np.ndarray]     # style -> bool (N,)
    errors: dict[str, int]             # descriptor -> values dropped as data errors


def _std(x: np.ndarray, capw: np.ndarray, estu: np.ndarray) -> np.ndarray:
    ok = estu & np.isfinite(x)
    if ok.sum() < 2:
        return np.full(x.shape, np.nan)
    if np.std(x[ok]) == 0.0:
        return np.where(np.isfinite(x), 0.0, np.nan)
    return standardize(x, np.where(np.isfinite(capw), capw, 0.0), estu)


def _trim(x: np.ndarray, estu: np.ndarray, k: float | None) -> np.ndarray:
    if k is None or (estu & np.isfinite(x)).sum() < 2:
        return x
    return trim_sigma(x, estu, k)


def _orth(y: np.ndarray, targets: list[np.ndarray], w: np.ndarray, fit: np.ndarray) -> np.ndarray:
    Z = np.column_stack(targets)
    ok = fit & np.isfinite(y) & np.all(np.isfinite(Z), axis=1)
    if ok.sum() <= Z.shape[1] + 1:
        return y
    return orthogonalize(y, Z, np.where(np.isfinite(w), w, 0.0), fit)


def _impute(x: np.ndarray, miss: np.ndarray, industry: np.ndarray, n_ind: int, size: np.ndarray | None,
            w: np.ndarray, fit: np.ndarray) -> np.ndarray:
    """Step 7: regress on industry dummies (+ SIZE) over ESTU names with data; predict the missing."""
    has = industry >= 0
    D = np.zeros((len(x), n_ind))
    D[np.flatnonzero(has), industry[has]] = 1.0
    A = D if size is None else np.column_stack([D, size])
    usable = has & np.all(np.isfinite(A), axis=1)
    ok = fit & usable & np.isfinite(x)
    out = x.copy()
    if ok.sum() > A.shape[1]:
        sw = np.sqrt(w[ok])
        beta, *_ = np.linalg.lstsq(A[ok] * sw[:, None], x[ok] * sw, rcond=None)
        fill = miss & usable
        out[fill] = (A @ beta)[fill]
    out[miss & ~np.isfinite(out)] = 0.0          # fallback: the cap-weighted mean on this scale
    return out


def cross_section(desc: dict[str, np.ndarray], cov: np.ndarray, estu: np.ndarray, capw: np.ndarray,
                  mcap: np.ndarray, industry: np.ndarray, n_ind: int, cfg: ModelConfig) -> CrossSection:
    o = cfg.descriptors.outliers
    weights, ortho = style_weights(cfg), orthogonal_targets(cfg)
    with np.errstate(invalid="ignore"):
        w_reg = np.sqrt(np.where(mcap > 0, mcap, np.nan))
    fit = estu & np.isfinite(w_reg)
    errors: dict[str, int] = {}
    d_std: dict[str, np.ndarray] = {}
    for name in sorted({lab for w in weights.values() for lab in w}):
        x = np.where(cov, desc[name], np.nan)
        with np.errstate(invalid="ignore"):
            bad = np.abs(robust_zscore(x, estu)) > o.error_robust_z
        errors[name] = int(bad.sum())
        x = np.where(bad, np.nan, x)
        x = _trim(x, estu, o.size_clip_sigma if name == "LNCAP" else o.clip_sigma)
        d_std[name] = _std(x, capw, estu)

    X: dict[str, np.ndarray] = {}
    for style, w in weights.items():
        num = np.zeros(len(cov))
        den = np.zeros(len(cov))
        for name, wl in w.items():
            v = d_std[name]
            ok = np.isfinite(v)
            num += np.where(ok, wl * v, 0.0)
            den += np.where(ok, wl, 0.0)
        X[style] = _std(np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan), capw, estu)
    for style, base in NONLINEAR.items():
        X[style] = X[base] ** 3
    for style, targets in ortho.items():
        if targets:
            y = _orth(X[style], [X[t] for t in targets], w_reg, fit)
            X[style] = _std(_trim(y, estu, o.clip_sigma), capw, estu)

    imputed: dict[str, np.ndarray] = {}
    for style in ("SIZE", *[s for s in STYLES if s != "SIZE"]):
        miss = cov & ~np.isfinite(X[style])
        imputed[style] = miss
        if miss.any():
            X[style] = _impute(X[style], miss, industry, n_ind, None if style == "SIZE" else X["SIZE"], w_reg, fit)
    for style, targets in ortho.items():
        if targets:
            X[style] = _orth(X[style], [X[t] for t in targets], w_reg, fit)
    for style in STYLES:
        X[style] = np.where(cov, _std(X[style], capw, estu), np.nan)
    return CrossSection(X, imputed, errors)


def build_exposures(P: Panel, desc: dict[str, np.ndarray], cfg: ModelConfig, first: date,
                    vif_anchor: date) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(exposures, exposure_qa, descriptor_qa) for every session from `first` on.

    exposures is wide: date, sid, industry, one column per style, and `imp_<STYLE>` flags. VIF is
    computed every `stability_lag_days` sessions counted from `vif_anchor`, so a daily run that
    starts later lands on the same dates as the backfill.
    """
    T, N = P.shape
    t0 = P.row[first]
    Xs = {s: np.full((T, N), np.nan) for s in STYLES}
    imp = {s: np.zeros((T, N), dtype=bool) for s in STYLES}
    n_ind = len(P.industries)
    dqa: list[dict[str, Any]] = []
    for t in range(t0, T):
        cov, estu = P["in_cov"][t], P["in_estu"][t]
        if estu.sum() < MIN_ESTU:
            continue
        cs = cross_section({k: v[t] for k, v in desc.items()}, cov, estu, P["capw"][t], P["mcap"][t],
                           P["industry"][t], n_ind, cfg)
        for s in STYLES:
            Xs[s][t] = cs.X[s]
            imp[s][t] = cs.imputed[s]
        for name, n_err in cs.errors.items():
            dqa.append({"date": P.dates[t], "descriptor": name, "n_error": n_err,
                        "n_valid": int((cov & np.isfinite(desc[name][t])).sum())})
    rr, cc = np.nonzero(P["in_cov"][t0:])
    rr = rr + t0
    keep = np.isfinite(Xs["SIZE"][rr, cc])
    rr, cc = rr[keep], cc[keep]
    cols: dict[str, Any] = {"date": [P.dates[i] for i in rr], "sid": P.sids[cc],
                            "industry": [P.industries[k] if k >= 0 else None for k in P["industry"][rr, cc]]}
    for s in STYLES:
        cols[s] = Xs[s][rr, cc]
    for s in STYLES:
        cols[f"imp_{s}"] = imp[s][rr, cc]
    exposures = pl.DataFrame(cols).sort("date", "sid")
    qa = exposure_qa(P, Xs, imp, t0, P.row[vif_anchor], cfg)
    return exposures, qa, pl.DataFrame(dqa)


def exposure_qa(P: Panel, Xs: dict[str, np.ndarray], imp: dict[str, np.ndarray], t0: int, anchor: int,
                cfg: ModelConfig) -> pl.DataFrame:
    """Per session and style: moments, coverage without imputation, stability vs `stability_lag_days`
    earlier (sqrt-cap weights on names in both ESTUs), and VIF every `stability_lag_days` sessions
    counted from row `anchor`."""
    lag = cfg.gates.stability_lag_days
    T, _ = P.shape
    rows: list[dict[str, Any]] = []
    n_ind = len(P.industries)
    for t in range(t0, T):
        cov, estu, capw = P["in_cov"][t], P["in_estu"][t], P["capw"][t]
        if not np.isfinite(Xs["SIZE"][t][estu]).any():
            continue
        w = np.sqrt(np.where(P["mcap"][t] > 0, P["mcap"][t], np.nan))
        vifs: dict[str, float] = {}
        if (t - anchor) % lag == 0:
            ind = P["industry"][t]
            D = np.zeros((len(ind), n_ind))
            D[np.flatnonzero(ind >= 0), ind[ind >= 0]] = 1.0
            A = np.column_stack([D] + [Xs[s][t] for s in STYLES])
            v = vif(A, np.nan_to_num(w), estu, list(range(n_ind, n_ind + len(STYLES))))
            vifs = dict(zip(STYLES, v.tolist(), strict=True))
        for s in STYLES:
            x = Xs[s][t]
            ok = estu & np.isfinite(x)
            cw = np.where(ok, np.nan_to_num(capw), 0.0)
            stab = float("nan")
            if t - lag >= t0:
                both = estu & P["in_estu"][t - lag]
                stab = weighted_corr(x, Xs[s][t - lag], np.nan_to_num(w), both)
            rows.append({
                "date": P.dates[t], "style": s,
                "mean_cw": float((cw * np.nan_to_num(x)).sum() / cw.sum()) if cw.sum() > 0 else float("nan"),
                "std_ew": float(np.std(x[ok])) if ok.any() else float("nan"),
                "coverage": float((cov & ~imp[s][t] & np.isfinite(x)).sum() / max(int(cov.sum()), 1)),
                "imputed": int((cov & imp[s][t]).sum()), "stability": stab, "vif": vifs.get(s)})
    return pl.DataFrame(rows, schema_overrides={"vif": pl.Float64})
