"""Splits and cash distributions implied by unadjusted vs adjusted closes (DECISIONS D-001, D-007).

EODHD's adjusted close is multiplicative. On an ex-date with k new shares per old share and cash
D per pre-split share, the adjusted relative is R = A_t/A_{t-1} = k P_t / (P_{t-1} - D), and with
the price relative p = P_t/P_{t-1}:

    g = R / p = k / (1 - D / P_{t-1})

A pure split makes g a clean ratio a:b with a small term (AAPL 2020-08-31: 3.999998). A regular
dividend makes g slightly above 1. A spin-off or special distribution makes g an arbitrary
number; its whole gap is treated as a cash-equivalent distribution. Prices alone cannot tell a
5:4 split from a 20% spin-off, so staging confirms splits between 1:3 and 3:1 against EDGAR share
counts and passes back the ones to treat as distributions (`force_distribution`). Total return
follows blueprint §4.3: r = (k P_t + D) / P_{t-1} - 1.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import polars as pl

from eqrisk.config import CorpActionQaCfg

NONE, SPLIT, DIVIDEND, DISTRIBUTION, UNEXPLAINED, BAD_PRICE = (
    "", "split", "dividend", "distribution", "unexplained", "bad_price")


def _col(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64).to_numpy(allow_copy=True)


def snap_split(g: float, qa: CorpActionQaCfg) -> tuple[float | None, float]:
    """Closest admissible split ratio to g and its relative distance.

    Admissible: n:1 or 1:n for any n, and a:b with min(a, b) <= `split_small_term_max` only while
    the ratio stays within (1/split_fraction_max, split_fraction_max): 3:2 and 5:4 yes, 28:3 no.
    """
    best, err = None, math.inf
    for m in range(1, qa.split_small_term_max + 1):
        n = round(g * m) if g >= 1.0 else round(m / g)
        if n <= 0:
            continue
        cand = n / m if g >= 1.0 else m / n
        if cand == 1.0:
            continue
        if m > 1 and not (1.0 / qa.split_fraction_max < cand < qa.split_fraction_max):
            continue
        e = abs(g / cand - 1.0)
        if e < err:
            best, err = cand, e
    return best, float(err)


def implied_actions(eod: pl.DataFrame, qa: CorpActionQaCfg,
                    force_distribution: pl.DataFrame | None = None) -> pl.DataFrame:
    """Add split_ratio (k), dividend (cash per post-split share), action, and ret_total.

    Needs code, date, close, adjusted_close, prev_close and prev_adjusted_close from the same vendor
    response. `force_distribution` (code, date) marks events that share counts showed are not splits.
    """
    P, P0 = _col(eod, "close"), _col(eod, "prev_close")
    A, A0 = _col(eod, "adjusted_close"), _col(eod, "prev_adjusted_close")
    n = len(P)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = P / P0
        g = (A / A0) / p
    good_px = np.isfinite(p) & (P > 0) & (P0 > 0)
    good_g = good_px & np.isfinite(g) & (g > 0)
    forced = np.zeros(n, dtype=bool)
    if force_distribution is not None and force_distribution.height and "code" in eod.columns:
        forced = eod.select("code", "date").join(
            force_distribution.select("code", "date").with_columns(_f=pl.lit(True)),
            on=["code", "date"], how="left")["_f"].fill_null(False).to_numpy()

    k = np.ones(n)
    action = np.full(n, NONE, dtype=object)
    big = good_g & (np.abs(g - 1.0) > qa.split_detect_tol)
    for i in np.flatnonzero(big):
        ratio, err = snap_split(float(g[i]), qa)
        if ratio is not None and err <= qa.split_ratio_rel_tol and not forced[i]:
            k[i] = ratio
            action[i] = SPLIT
        elif g[i] > 1.0:
            action[i] = DISTRIBUTION
        else:
            action[i] = UNEXPLAINED

    resid = np.where(good_g, g / k, 1.0)                 # 1 / (1 - D / P_{t-1})
    has_cash = good_g & (resid - 1.0 > qa.dividend_min_rel) & (action != UNEXPLAINED)
    cash = np.where(has_cash, P0 * (1.0 - 1.0 / resid), 0.0)
    action = np.where(has_cash & (action == NONE), DIVIDEND, action)
    action = np.where(good_g & (resid - 1.0 < -qa.dividend_min_rel) & (action == NONE), UNEXPLAINED, action)
    action = np.where(~good_px & np.isfinite(P0), BAD_PRICE, action)

    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(good_px, (k * P + cash) / P0 - 1.0, np.nan)
    return eod.with_columns(
        split_ratio=pl.Series(k),
        dividend=pl.Series(cash / k),
        action=pl.Series(action.astype(str)),
        ret_total=pl.Series(ret).fill_nan(None),
    )


def unconfirmed_splits(prices: pl.DataFrame, issuer_of: pl.DataFrame, shares: pl.DataFrame,
                       qa: CorpActionQaCfg) -> pl.DataFrame:
    """(code, date) of detected splits between 1:f and f:1 that EDGAR share counts contradict.

    For each such split, compare the issuer's last share count filed before the ex-date with the
    first filed on or after it (within `split_confirm_window_days`). Filing dates, not period ends:
    statements issued after a split restate earlier-dated counts on the new basis. If the ratio
    sits closer to 1 than to the split ratio, the event was a distribution. Splits without counts
    on both sides stand as detected.
    """
    f = qa.split_fraction_max
    ev = (prices.filter((pl.col("action") == SPLIT) & pl.col("split_ratio").is_between(1.0 / f, f, closed="none"))
          .select("sid", "code", "date", "split_ratio").join(issuer_of, on="sid"))
    out: list[tuple[str, date]] = []
    by_cik = {c: g.sort("filed", "period_end") for (c,), g in shares.group_by("cik")}
    window = qa.split_confirm_window_days
    for r in ev.iter_rows(named=True):
        g = by_cik.get(r["cik"])
        if g is None:
            continue
        fd, val = g["filed"].to_list(), g["value"].to_list()
        before = [v for d, v in zip(fd, val, strict=True) if d < r["date"] and (r["date"] - d).days <= window]
        after = [v for d, v in zip(fd, val, strict=True) if d >= r["date"] and (d - r["date"]).days <= window]
        if not before or not after or before[-1] <= 0:
            continue
        obs = math.log(after[0] / before[-1])
        if abs(obs - math.log(r["split_ratio"])) > abs(obs):
            out.append((r["code"], r["date"]))
    return pl.DataFrame(out, schema={"code": pl.String, "date": pl.Date}, orient="row")


def flag_special_dividends(prices: pl.DataFrame, qa: CorpActionQaCfg) -> pl.Series:
    """True where a cash distribution is special: any spin-off-like distribution, or a dividend
    above `special_dividend_multiple` x the median of the security's dividends in the previous
    `dividend_lookback_sessions` sessions. Needs sid, date, session index `i`, action, dividend."""
    ev = prices.select("sid", "i", "action", "dividend").with_row_index("_row").filter(
        pl.col("action").is_in([DIVIDEND, DISTRIBUTION]))
    special = np.zeros(prices.height, dtype=bool)
    for (_sid,), grp in ev.group_by("sid"):
        grp = grp.sort("i")
        rows, idx, amt, act = (grp[c].to_numpy() for c in ("_row", "i", "dividend", "action"))
        for j in range(len(rows)):
            if act[j] == DISTRIBUTION:
                special[rows[j]] = True
                continue
            prior = (idx < idx[j]) & (idx >= idx[j] - qa.dividend_lookback_sessions) & (act == DIVIDEND)
            if prior.any() and amt[j] > qa.special_dividend_multiple * float(np.median(amt[prior])):
                special[rows[j]] = True
    return pl.Series("special_dividend", special)
