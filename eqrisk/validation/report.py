"""Validation report: Markdown with matplotlib figures, CSV tables and a JSON summary (Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from eqrisk.validation.backtest import ValidationResult

LAYERS = {"pre": "Layers 1-2 (EWMA + Newey-West)", "post": "Layers 1-3 (+ eigenfactor adjustment)",
          "final": "Layers 1-4 (+ volatility regime)"}


def _fmt(v: Any, digits: int = 3) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}" if np.isfinite(v) else ""
    return str(v)


def md_table(df: pl.DataFrame, digits: int = 3) -> str:
    head = "| " + " | ".join(df.columns) + " |"
    rule = "|" + "|".join("---" for _ in df.columns) + "|"
    return "\n".join([head, rule, *("| " + " | ".join(_fmt(v, digits) for v in row) + " |" for row in df.iter_rows())])


def _rows(rows: list[dict[str, str]]) -> str:
    return md_table(pl.DataFrame(rows)) if rows else "_none_"


def _family_line(df: pl.DataFrame) -> str:
    if df.height == 0:
        return "no portfolios with enough periods"
    return (f"{df.height} portfolios: mean bias {df['bias'].mean():.3f}, "
            f"{df['inside'].mean():.0%} inside their 95% band, mean MRAD {df['mrad'].mean():.3f}, "
            f"mean QLIKE {df['qlike'].mean():.3f}")


def _deciles(res: ValidationResult) -> pl.DataFrame:
    rows = []
    for kind in ("size", "vol"):
        keys = sorted(set(res.specific["full"][kind]) | set(res.specific["ts"][kind]))
        for k in keys:
            rows.append({"grouping": "size decile" if kind == "size" else "forecast-vol decile", "decile": k + 1,
                         "full stack": res.specific["full"][kind].get(k),
                         "time series only": res.specific["ts"][kind].get(k)})
    return pl.DataFrame(rows)


def write_report(res: ValidationResult, out: Path) -> Path:
    """Write report.md, its figures, CSV tables and summary.json under `out`; returns report.md."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    band = float(np.sqrt(2.0 / max(res.periods, 1)))

    def save(fig: Any, name: str) -> str:
        fig.tight_layout()
        fig.savefig(out / name, dpi=110)
        plt.close(fig)
        return name

    figs: dict[str, str] = {}
    f = res.factor
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar(np.arange(f.height), f["bias"].to_numpy(), color="#4C72B0")
    ax.axhspan(1 - band, 1 + band, color="grey", alpha=0.2, label="95% band")
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_xticks(np.arange(f.height), f["portfolio"].to_list(), rotation=90, fontsize=7)
    ax.set_ylabel("bias statistic")
    ax.set_title("(a) Pure factor portfolios")
    ax.legend()
    figs["factor"] = save(fig, "factor_bias.png")

    fig, ax = plt.subplots(figsize=(11, 4))
    for label, (dates, series) in res.rolling.items():
        ax.plot(dates, series, label=label, lw=1.2)
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_ylabel("rolling bias statistic")
    ax.set_title("Rolling bias over non-overlapping periods")
    ax.legend()
    figs["rolling"] = save(fig, "rolling_bias.png")

    e = res.eigen
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(e["k"].to_numpy() + 1, e["bias_before"].to_numpy(), "o-", label="Layers 1-2")
    ax.plot(e["k"].to_numpy() + 1, e["bias_after"].to_numpy(), "s-", label="after eigenfactor adjustment")
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_xlabel("eigenfactor (1 = smallest eigenvalue)")
    ax.set_ylabel("bias statistic")
    ax.set_title("(b) Eigenfactor portfolios")
    ax.legend()
    figs["eigen"] = save(fig, "eigen_smile.png")

    fig, ax = plt.subplots(figsize=(8, 4))
    for layer, df in res.random_alpha.items():
        ax.hist(df["bias"].to_numpy(), bins=25, alpha=0.5, label=LAYERS[layer])
    ax.axvline(1.0, color="black", lw=0.8)
    ax.set_xlabel("bias statistic")
    ax.set_title("(c) Random-alpha minimum-risk factor portfolios")
    ax.legend(fontsize=8)
    figs["alpha"] = save(fig, "random_alpha.png")

    dec = _deciles(res)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, grouping in zip(axes, ("size decile", "forecast-vol decile"), strict=True):
        g = dec.filter(pl.col("grouping") == grouping)
        for col, style in (("full stack", "o-"), ("time series only", "s--")):
            ax.plot(g["decile"].to_numpy(), g[col].to_numpy(), style, label=col)
        ax.axhline(1.0, color="black", lw=0.8)
        ax.set_xlabel(grouping)
        ax.set_title(f"(g) Specific bias by {grouping}")
    axes[0].set_ylabel("cap-weighted bias statistic")
    axes[0].legend()
    figs["specific"] = save(fig, "specific_deciles.png")

    fig, ax = plt.subplots(figsize=(11, 3.5))
    for col in ("lambda_F", "lambda_S"):
        s = res.vra.drop_nulls(col)
        ax.plot(s["date"].to_list(), s[col].to_numpy(), label=col, lw=1)
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_title("Volatility regime adjustment")
    ax.legend()
    figs["vra"] = save(fig, "lambda.png")

    ra = pl.DataFrame([{"matrix": LAYERS[k], "portfolios": df.height, "mean bias": df["bias"].mean(),
                        "inside band": df["inside"].mean(), "mean MRAD": df["mrad"].mean(),
                        "mean QLIKE": df["qlike"].mean()} for k, df in res.random_alpha.items()])
    e10 = e.head(10)
    lines = [
        f"# EQRisk validation: {res.model_id}, {res.start} to {res.end}",
        "",
        f"Config hash `{res.config_hash[:16]}`. Forecasts are scored over the next {res.horizon} sessions on "
        f"{res.periods} non-overlapping periods, so a bias statistic's 95% band is 1 ± {band:.3f}.",
        "",
        "## Summary (§1.3 success criteria)",
        "",
        _rows(res.scorecard),
        "",
        "## External checks (§11.3)",
        "",
        _rows(res.external),
        "",
        "## (a) Pure factor portfolios",
        "",
        f"![factor bias]({figs['factor']})",
        "",
        md_table(res.factor),
        "",
        "## Rolling bias",
        "",
        f"![rolling bias]({figs['rolling']})",
        "",
        "## (b) Eigenfactor portfolios",
        "",
        f"![eigen smile]({figs['eigen']})",
        "",
        f"Smallest ten eigenfactors: mean bias {e10['bias_before'].mean():.3f} before, "
        f"{e10['bias_after'].mean():.3f} after the adjustment.",
        "",
        md_table(e),
        "",
        "## (c) Random-alpha minimum-risk factor portfolios",
        "",
        f"![random alpha]({figs['alpha']})",
        "",
        md_table(ra),
        "",
        "## (d) Estimation universe, cap- and equal-weighted",
        "",
        md_table(res.market),
        "",
        "## (e) Cap-weighted industry portfolios",
        "",
        _family_line(res.industry),
        "",
        md_table(res.industry),
        "",
        "## (f) Random long-only portfolios",
        "",
        _family_line(res.random),
        "",
        "## (g) Specific risk by size and forecast-volatility decile",
        "",
        f"Cap-weighted bias: full stack {res.specific['full']['overall']:.3f}, time series only "
        f"{res.specific['ts']['overall']:.3f}.",
        "",
        f"![specific deciles]({figs['specific']})",
        "",
        md_table(dec),
        "",
        "## Volatility regime",
        "",
        f"![lambda]({figs['vra']})",
        "",
    ]
    report = out / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    for name, df in (("factor", res.factor), ("eigen", res.eigen), ("market", res.market), ("industry", res.industry),
                     ("random", res.random), ("specific_deciles", dec),
                     *((f"random_alpha_{k}", df) for k, df in res.random_alpha.items())):
        df.write_csv(out / f"{name}.csv")
    summary = {"model_id": res.model_id, "config_hash": res.config_hash, "start": res.start, "end": res.end,
               "periods": res.periods, "scorecard": res.scorecard, "external": res.external, "metrics": res.metrics}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return report
