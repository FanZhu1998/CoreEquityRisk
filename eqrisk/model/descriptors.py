"""Raw descriptors for every (session, security) as of the close (blueprint §6.2).

Price descriptors use excess returns and trailing windows ending at t. Fundamental descriptors
use point-in-time values available by t, relabelled to the issuer. Per-share fundamentals (EPS,
sales per share) are put on a common split basis with the primary class's splits, using each
value's filing date (statements restate per-share data for splits before issuance). Splits
before the price history are unknown (DECISIONS D-012).
"""

from __future__ import annotations

import bisect
from datetime import date

import numpy as np
import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.kernels import rolling_ewma_beta
from eqrisk.kernels.descriptors import block_turnover, cmra, growth_rate, lag_rows, rolling_ewma_moments
from eqrisk.model.panel import Panel, pivot
from eqrisk.staging.fundamentals_pit import asof
from eqrisk.staging.mcap import staleness_days

DESCRIPTORS = ("HBETA", "HSIGMA", "RSTR", "LNCAP", "DASTD", "CMRA", "STOM", "STOQ", "STOA",
               "ETOP", "CETOP", "DTOP", "BTOP", "MLEV", "DTOA", "BLEV", "EGRO", "SGRO")
ANNUAL_STALENESS_EXTRA_MONTHS = 12   # a fiscal-year value stays current until the next one is due


def _pad(X: np.ndarray, window: int) -> np.ndarray:
    """Prepend window-1 missing rows so windows reaching before the data start still evaluate;
    the minimum-observation rule, not the window length, then decides."""
    return np.vstack([np.full((window - 1, X.shape[1]), np.nan), X])


def _partial_moments(X: np.ndarray, window: int, half_life: float, min_obs: int) -> tuple[np.ndarray, np.ndarray]:
    m, s = rolling_ewma_moments(_pad(X, window), window, half_life, min_obs)
    return m[window - 1:], s[window - 1:]


def market_excess_return(P: Panel) -> np.ndarray:
    """R^e_M,t: cap-weighted ESTU excess return, ESTU and caps from the prior close."""
    w = lag_rows(np.where(P["in_estu"], P["capw"], np.nan), 1)
    r = P["ret_excess"]
    ok = np.isfinite(w) & np.isfinite(r)
    num = np.where(ok, w * r, 0.0).sum(axis=1)
    den = np.where(ok, w, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _fundamental_panel(P: Panel, sid_cik: dict[int, int], pit: pl.DataFrame, item: str,
                       stale_days: int) -> np.ndarray:
    keys = pl.DataFrame({"sid": list(sid_cik), "cik": list(sid_cik.values())},
                        schema={"sid": pl.Int64, "cik": pl.Int64})
    dates = pl.DataFrame({"date": P.dates}, schema={"date": pl.Date})
    grid = dates.join(keys.select("cik").unique(), how="cross")
    vals = asof(pit, grid, item, stale_days)
    long = vals.join(keys, on="cik").select("date", "sid", "value")
    return pivot(long, "value", P.dates, P.sids)


class _CumSplit:
    """Cumulative split factor of each issuer's reference security on any date."""

    def __init__(self, P: Panel, issuer_ref: dict[int, int]) -> None:
        self.dates = [d.toordinal() for d in P.dates]
        self.series: dict[int, np.ndarray] = {}
        for cik, sid in issuer_ref.items():
            j = P.col.get(sid)
            if j is not None:
                s = P["cum_split"][:, j].copy()
                # carry forward over unpriced sessions; before the first price the factor is 1
                ok = np.isfinite(s)
                idx = np.where(ok, np.arange(len(s)), -1)
                np.maximum.accumulate(idx, out=idx)
                self.series[cik] = np.where(idx >= 0, s[np.maximum(idx, 0)], 1.0)

    def at(self, cik: int, d: date) -> float:
        s = self.series.get(cik)
        if s is None:
            return 1.0
        i = bisect.bisect_right(self.dates, d.toordinal()) - 1
        return float(s[i]) if i >= 0 else 1.0


def growth_events(pit: pl.DataFrame, item: str, cum: _CumSplit, years: int, min_years: int,
                  denominator: str | None = None) -> pl.DataFrame:
    """(cik, available_date, value, latest_fy) each time an issuer's growth rate changes.

    Annual values (per-share, or `item` / `denominator` for sales per share) are put on a common
    split basis via their filing dates, then growth_rate() over the last `years` fiscal years.
    """
    rows = pit.filter(pl.col("item").is_in([item] + ([denominator] if denominator else [])))
    out: list[tuple[int, date, float, date]] = []
    for (cik,), g in rows.group_by("cik"):
        known: dict[str, dict[date, tuple[float, date]]] = {item: {}, **({denominator: {}} if denominator else {})}
        g = g.sort("available_date")
        for av, grp in g.group_by("available_date", maintain_order=True):
            for it, pe, v, fd in grp.select("item", "period_end", "value", "filed").iter_rows():
                known[it][pe] = (v, fd)
            ends = sorted(known[item])[-years:]
            vals = []
            for pe in ends:
                v, fd = known[item][pe]
                if denominator:
                    if pe not in known[denominator] or known[denominator][pe][0] <= 0:
                        vals.append(np.nan)
                        continue
                    sh, fds = known[denominator][pe]
                    vals.append(v / sh * cum.at(int(cik), fds))
                else:
                    vals.append(v * cum.at(int(cik), fd))
            gr = growth_rate(np.array(vals, dtype=float), min_years)
            if ends:
                out.append((int(cik), av[0], gr, ends[-1]))
    return pl.DataFrame(out, schema={"cik": pl.Int64, "available_date": pl.Date, "value": pl.Float64,
                                     "latest_fy": pl.Date}, orient="row")


def _event_panel(P: Panel, sid_cik: dict[int, int], ev: pl.DataFrame, stale_days: int) -> np.ndarray:
    keys = pl.DataFrame({"sid": list(sid_cik), "cik": list(sid_cik.values())},
                        schema={"sid": pl.Int64, "cik": pl.Int64})
    grid = pl.DataFrame({"date": P.dates}, schema={"date": pl.Date}).join(keys, how="cross").sort("date")
    hit = grid.join_asof(ev.sort("available_date"), left_on="date", right_on="available_date", by="cik",
                         strategy="backward", check_sortedness=False)
    hit = hit.with_columns(value=pl.when((pl.col("date") - pl.col("latest_fy")).dt.total_days() <= stale_days)
                           .then(pl.col("value")))
    return pivot(hit, "value", P.dates, P.sids)


def compute_descriptors(P: Panel, pit: pl.DataFrame, master: pl.DataFrame,
                        cfg: ModelConfig) -> dict[str, np.ndarray]:
    d = cfg.descriptors
    T, N = P.shape
    out: dict[str, np.ndarray] = {}

    # --- price descriptors -------------------------------------------------
    M = market_excess_return(P)
    R = np.where(np.isfinite(M)[:, None], P["ret_excess"], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):      # all-missing windows divide 0/0
        out["HBETA"], out["HSIGMA"] = rolling_ewma_beta(_pad(R, d.beta.window), np.nan_to_num(
            _pad(M[:, None], d.beta.window)[:, 0]), d.beta.window, d.beta.half_life, d.beta.min_obs, chunk=16)
    out["HBETA"], out["HSIGMA"] = out["HBETA"][d.beta.window - 1:], out["HSIGMA"][d.beta.window - 1:]
    with np.errstate(invalid="ignore"):
        x = np.log1p(P["ret"]) - np.log1p(P.vectors["rf"])[:, None]
    mom = d.momentum
    out["RSTR"] = _partial_moments(lag_rows(x, mom.lag + 1), mom.window, mom.half_life, mom.min_obs)[0]
    dast = d.resvol.dastd
    out["DASTD"] = _partial_moments(P["ret_excess"], dast.window, dast.half_life, dast.min_obs)[1]
    out["CMRA"] = cmra(x, d.resvol.cmra_months, cfg.horizon_days, d.resvol.cmra_min_obs, d.resvol.cmra_z_floor)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["LNCAP"] = np.where(P["mcap"] > 0, np.log(P["mcap"]), np.nan)
        turnover = np.where(P["shares"] > 0, P["volume"] / P["shares"], np.nan)
    blk, frac = d.liquidity.block, d.liquidity.min_block_fraction
    out["STOM"] = block_turnover(turnover, blk, 1, frac)
    out["STOQ"] = block_turnover(turnover, blk, 3, frac)
    out["STOA"] = block_turnover(turnover, blk, 12, frac)

    # --- fundamentals (issuer-level, point in time) -------------------------
    sid_cik = {int(s): int(c) for s, c in master.select("sid", "cik").drop_nulls().iter_rows()}
    stale = staleness_days(d.fundamentals.max_staleness_months)
    F = {item: _fundamental_panel(P, sid_cik, pit, item, stale)
         for item in ("ni_ttm", "cfo_ttm", "da_ttm", "dps_ttm", "book_equity", "preferred_equity",
                      "long_term_debt", "current_debt", "total_assets")}
    mc = np.where(P["mcap"] > 0, P["mcap"], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["ETOP"] = F["ni_ttm"] / mc
        cash = F["cfo_ttm"] if d.earnings_yield.cash_earnings == "cfo" else F["ni_ttm"] + F["da_ttm"]
        out["CETOP"] = cash / mc
        be = F["book_equity"]
        out["BTOP"] = be / mc
        has_bs = np.isfinite(be) | np.isfinite(F["total_assets"])
        pe = np.where(np.isfinite(F["preferred_equity"]), F["preferred_equity"], np.where(has_bs, 0.0, np.nan))
        ld = np.where(np.isfinite(F["long_term_debt"]), F["long_term_debt"], np.where(has_bs, 0.0, np.nan))
        cd = np.where(np.isfinite(F["current_debt"]), F["current_debt"], np.where(has_bs, 0.0, np.nan))
        out["MLEV"] = (mc + pe + ld) / mc
        out["DTOA"] = (ld + cd) / F["total_assets"]
        out["BLEV"] = np.where(be > 0, (be + pe + ld) / be, np.nan)

    # Dividend yield: implied regular dividends over the lookback, on today's share basis.
    lb = d.dividend_yield.lookback_sessions
    cum = P["cum_split"]
    base_div = np.nan_to_num(P["dividend"] * cum)                  # per original-basis share
    cs = np.vstack([np.zeros((1, N)), np.cumsum(base_div, axis=0)])
    hi = np.arange(1, T + 1)
    trail = cs[hi] - cs[np.maximum(hi - lb, 0)]
    with np.errstate(invalid="ignore", divide="ignore"):
        implied = (trail / cum) / P["close_unadj"]
        edgar = F["dps_ttm"] / P["close_unadj"]
    # Point in time: has the vendor adjusted this series for any action up to and including t?
    adjusts = np.logical_or.accumulate(P["vendor_adjusts"], axis=0)
    out["DTOP"] = np.where(adjusts, implied, np.where(np.isfinite(edgar), edgar, implied))

    # Growth: event-driven on annual filings.
    ref = master.filter(pl.col("cik").is_not_null()).select("cik", ref=pl.coalesce("linked_sid", "sid")).unique("cik")
    cumsplit = _CumSplit(P, {int(c): int(s) for c, s in ref.iter_rows()})
    g_stale = staleness_days(d.fundamentals.max_staleness_months + ANNUAL_STALENESS_EXTRA_MONTHS)
    out["EGRO"] = _event_panel(P, sid_cik, growth_events(pit, "eps_fy", cumsplit, d.growth.years,
                                                         d.growth.min_years), g_stale)
    out["SGRO"] = _event_panel(P, sid_cik, growth_events(pit, "revenue_fy", cumsplit, d.growth.years,
                                                         d.growth.min_years, denominator="shares_wa_fy"), g_stale)
    for k, v in out.items():
        v[~np.isfinite(v)] = np.nan
        out[k] = v
    return out
