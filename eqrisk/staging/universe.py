"""Coverage and estimation universes, weights, and the thin-industry rule (blueprint §4.8, §5).

Coverage C_t = S&P 500 members at the close of t. ESTU E_t = C_t minus, in this order of reason
codes: secondary share classes, no industry, no price or a flagged price that day, missing
market cap, fewer than `min_history_days` returns. Every exclusion keeps its reason (rule 7).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from eqrisk.config import ModelConfig
from eqrisk.staging.security_master import EXCEPTION_SCHEMA, FAR_FUTURE

REASONS = ("secondary_class", "no_industry", "no_price", "price_flag", "missing_mcap", "short_history")


def members_by_sid(daily: pl.DataFrame, ticker_history: pl.DataFrame) -> pl.DataFrame:
    """(date, ticker) membership -> (date, sid) through the era containing each date."""
    th = ticker_history.select("sid", "ticker", "start_date", end=pl.col("end_date").fill_null(FAR_FUTURE))
    return (daily.join(th, on="ticker")
            .filter(pl.col("date").is_between(pl.col("start_date"), pl.col("end")))
            .select("date", "sid", "ticker").unique(["date", "sid"]).sort("date", "sid"))


def build_universe(members: pl.DataFrame, prices: pl.DataFrame, mcap: pl.DataFrame, industries: pl.DataFrame,
                   master: pl.DataFrame, parents: dict[str, str], sessions: list[date],
                   cfg: ModelConfig) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The §14 `universe` table and the thin-industry exception log."""
    estu_cfg = cfg.universe.estu
    hist = prices.sort("sid", "date").select(
        "date", "sid", "price_flag", n_hist=pl.col("ret").is_not_null().cast(pl.Int64).cum_sum().over("sid"))
    df = (members.join(hist, on=["date", "sid"], how="left")
          .join(prices.select("date", "sid", has_px=pl.lit(True)), on=["date", "sid"], how="left")
          .join(mcap.select("date", "sid", "mcap_issuer"), on=["date", "sid"], how="left")
          .join(industries.select("sid", industry_raw=pl.col("industry")), on="sid", how="left")
          .join(master.select("sid", "primary_class"), on="sid", how="left"))
    checks: dict[str, pl.Expr] = {
        "secondary_class": pl.lit(estu_cfg.exclude_secondary_share_class) & ~pl.col("primary_class").fill_null(True),
        "no_industry": pl.col("industry_raw").is_null(),
        "no_price": pl.col("has_px").is_null(),
        "price_flag": pl.col("price_flag").is_not_null(),
        "missing_mcap": pl.col("mcap_issuer").is_null() | (pl.col("mcap_issuer") <= 0),
        "short_history": pl.col("n_hist").fill_null(0) < estu_cfg.min_history_days,
    }
    reason: pl.Expr = pl.lit(None, pl.String)
    for name in reversed(REASONS):
        reason = pl.when(checks[name]).then(pl.lit(name)).otherwise(reason)
    df = df.with_columns(exclusion_reason=reason).with_columns(in_estu=pl.col("exclusion_reason").is_null())
    df = _weights(df)
    df, thin = _thin_rule(df, parents, sessions, cfg)
    df = _weights(df)          # weights do not depend on industry, but keep one code path
    return df.select("date", "sid", "ticker", in_coverage=pl.lit(True), in_estu="in_estu",
                     exclusion_reason="exclusion_reason", v_reg="v_reg", capw="capw", industry="industry",
                     industry_raw="industry_raw", mcap=pl.col("mcap_issuer")).sort("date", "sid"), thin


def _weights(df: pl.DataFrame) -> pl.DataFrame:
    sq = pl.when(pl.col("in_estu")).then(pl.col("mcap_issuer").sqrt())
    cap = pl.when(pl.col("in_estu")).then(pl.col("mcap_issuer"))
    return df.with_columns(v_reg=sq / sq.sum().over("date"), capw=cap / cap.sum().over("date"))


def _thin_rule(df: pl.DataFrame, parents: dict[str, str], sessions: list[date],
               cfg: ModelConfig) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Merge an industry into its parent on dates where it breached N_eff/count on more than
    `max_breach_days` of the last `lookback_days` sessions (§4.8). One level only."""
    rule = cfg.industries.thin_rule
    est = df.filter(pl.col("in_estu"))
    stats = est.group_by("date", "industry_raw").agg(
        count=pl.len(), neff=pl.col("v_reg").sum() ** 2 / (pl.col("v_reg") ** 2).sum())
    grid = pl.DataFrame({"date": sessions}, schema={"date": pl.Date}).join(
        pl.DataFrame({"industry_raw": sorted(parents)}), how="cross")
    g = (grid.join(stats, on=["date", "industry_raw"], how="left")
         .with_columns(pl.col("count").fill_null(0), pl.col("neff").fill_null(0.0))
         .with_columns(breach=((pl.col("neff") < rule.min_neff) | (pl.col("count") < rule.min_count)).cast(pl.Int64))
         .sort("industry_raw", "date")
         .with_columns(breaches=pl.col("breach").rolling_sum(rule.lookback_days, min_samples=1).over("industry_raw"))
         .with_columns(merged=pl.col("breaches") > rule.max_breach_days))
    merged = g.filter(pl.col("merged")).select("date", "industry_raw")
    exc: list[dict[str, Any]] = []
    if merged.height:
        m = merged.with_columns(parent=pl.col("industry_raw").replace_strict(parents))
        both = m.join(m.select("date", parent=pl.col("industry_raw")), on=["date", "parent"], how="semi")
        spans = both.group_by("industry_raw").agg(first=pl.col("date").min(), last=pl.col("date").max())
        for r in spans.iter_rows(named=True):
            exc.append({"ticker": r["industry_raw"], "start": r["first"], "end": r["last"], "issue": "thin_parent",
                        "detail": "industry and its parent both thin: scheme-design error (4.8)"})
        for r in m.group_by("industry_raw", "parent").agg(first=pl.col("date").min(), last=pl.col("date").max(),
                                                          days=pl.len()).iter_rows(named=True):
            exc.append({"ticker": r["industry_raw"], "start": r["first"], "end": r["last"], "issue": "thin_merged",
                        "detail": f"merged into {r['parent']} on {r['days']} sessions"})
        df = df.join(m.select("date", "industry_raw", "parent"), on=["date", "industry_raw"], how="left")
        df = df.with_columns(industry=pl.coalesce("parent", "industry_raw")).drop("parent")
    else:
        df = df.with_columns(industry=pl.col("industry_raw"))
    return df, pl.DataFrame(exc, schema=EXCEPTION_SCHEMA)
