"""One optimizer entry point for every front end: the workbench page and the desktop app.

It wraps the two blueprint implementations behind a single specification and always measures the
result against the same benchmark, the cap-weighted estimation universe:

- `factor`: the native factor-form problem (Appendix A.3, `factor_form.optimize_active`), with a
  tracking-error cap, style and industry exposure bands, a weight cap and a turnover limit.
- `riskfolio`: Riskfolio-Lib with the model covariance injected (§12). Its tracking error is
  historical, so the tracking-error cap and the turnover limit apply to the factor form only.

Both front ends used to carry this logic inline; keeping it here means they cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from eqrisk.analytics.risk import RiskReport, market_portfolio, portfolio_risk
from eqrisk.model.snapshot import RiskModelSnapshot

Method = Literal["factor", "riskfolio"]
METHODS: tuple[Method, ...] = ("factor", "riskfolio")


@dataclass(frozen=True)
class OptimizeSpec:
    """What to solve. Bands and caps are fractions (0.03 is 3%); the tracking error is annualized."""

    method: Method
    te_max_ann: float
    style_band: float
    ind_band: float
    w_max: float
    turnover_max: float
    alpha_factor: str | None = None     # a style factor to tilt towards; None minimizes active risk
    alpha_per_unit: float = 0.0         # expected monthly return per unit of that exposure

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, not {self.method!r}")
        for name in ("te_max_ann", "style_band", "ind_band", "w_max", "turnover_max"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")


@dataclass
class OptimizeResult:
    status: str
    weights: np.ndarray | None          # None when the solver found no solution
    benchmark: np.ndarray
    report: RiskReport | None           # active risk of `weights` against `benchmark`

    @property
    def ok(self) -> bool:
        return self.weights is not None


def optimize_portfolio(snap: RiskModelSnapshot, spec: OptimizeSpec) -> OptimizeResult:
    w_b = market_portfolio(snap)
    styles, inds = list(snap.groups["style"]), list(snap.groups["industry"])
    alpha = None
    if spec.alpha_factor is not None:
        if spec.alpha_factor not in snap.factors:
            raise ValueError(f"unknown factor {spec.alpha_factor!r}")
        alpha = snap.X[:, snap.factors.index(spec.alpha_factor)] * spec.alpha_per_unit

    w: np.ndarray | None
    if spec.method == "factor":
        from eqrisk.optimize.factor_form import optimize_active

        # optimize_active is blueprint Appendix A.3 verbatim, so it carries no annotations.
        w, status = optimize_active(snap.X, snap.F, snap.spec_var, w_b,  # type: ignore[no-untyped-call]
                                    alpha=alpha, te_max_ann=spec.te_max_ann,
                                    style_idx=styles, style_bound=spec.style_band,
                                    ind_idx=inds, ind_bound=spec.ind_band,
                                    w_max=spec.w_max, w_prev=w_b, turnover_max=spec.turnover_max)
    else:
        import pandas as pd

        from eqrisk.optimize.riskfolio_adapter import exposure_bounds, to_riskfolio

        ids = [str(s) for s in snap.sids]
        X = pd.DataFrame(snap.X, index=ids, columns=snap.factors)
        port = to_riskfolio(X, pd.DataFrame(snap.F, index=snap.factors, columns=snap.factors),
                            pd.Series(snap.spec_var, index=ids))
        bounds = {snap.factors[k]: (-spec.style_band, spec.style_band) for k in styles}
        port.ainequality, port.binequality = exposure_bounds(X, bounds, pd.Series(w_b, index=ids))
        port.upperlng = spec.w_max
        res = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)
        w, status = (None, "infeasible") if res is None else (res["weights"].reindex(ids).to_numpy(), "optimal")

    if w is None:
        return OptimizeResult(status=str(status), weights=None, benchmark=w_b, report=None)
    weights = np.clip(np.asarray(w, dtype=float), 0.0, None)
    return OptimizeResult(status=str(status), weights=weights, benchmark=w_b,
                          report=portfolio_risk(snap, weights, w_b))
