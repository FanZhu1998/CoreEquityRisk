# CoreEquityRisk (EQRisk)

A fundamental equity factor risk model for US large caps, in the style of USE4. It is updated
daily with a one-month horizon, and its coverage is the point-in-time S&P 500. It estimates:
- exposures to a country factor, 20 SIC-based industries and 12 styles
- daily factor returns by constrained weighted least squares
- a four-layer factor covariance (EWMA with Newey-West, eigenfactor adjustment, volatility regime)
- five-layer specific risk

It validates itself, runs unattended, and exposes one `RiskModelSnapshot` that the workbench,
the static viewer, notebooks and the optimizers all consume.

The design is `docs/BLUEPRINT.md`. Every deviation or gap-filling choice is logged in
`docs/DECISIONS.md`, and day-to-day operation is in `docs/RUNBOOK.md`.

## Quick start

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync                                   # install
copy NUL .env                             # then add the keys below
uv run eqrisk init                        # folders, catalog, config validation, key check
uv run eqrisk doctor                      # connectivity and entitlements
uv run eqrisk backfill --end 2026-09-10   # ingest, stage, model (hours: EDGAR is rate-limited)
uv run eqrisk validate                    # bias battery and the §1.3 scorecard -> reports/
uv run eqrisk ui                          # Streamlit workbench at http://localhost:8501
```

`.env` keys (names only; values are never logged or written anywhere else):
- `EODHD_API_KEY`
- `FRED_API_KEY`
- `SEC_USER_AGENT` (your name and contact email, as the SEC requires)
- `DATABENTO_API_KEY` (optional, metered)

## Commands

| Command | What it does |
|---|---|
| `eqrisk backfill --stage ingest\|stage\|model\|all` | Stage-wise history build (§13.4) |
| `eqrisk run-daily [--date D] [--force] [--offline]` | Catch up every pending session: ingest, stage, model, §11.4 gates, `LATEST_GOOD` |
| `eqrisk validate [--start] [--end]` | Point-in-time backtest (§11.2) and PASS/FAIL scorecard |
| `eqrisk snapshot --date D --out snap.npz` | X, F and specific variance for notebooks |
| `eqrisk ui` | Eight-page Streamlit workbench |
| `eqrisk export-site [--date D]` | Static offline viewer in `site/`, served with `python -m http.server -d site` |
| `eqrisk compact --month YYYY-MM` | Merge a month's daily model files |
| `eqrisk pull-overrides` | Fetch what `configs/overrides/*.csv` names, after editing them |

The daily job is scheduled with Windows Task Scheduler; see `docs/RUNBOOK.md` §2.

## Data

| Need | Source |
|---|---|
| Prices and volume | EODHD end-of-day (unadjusted and adjusted close) |
| Splits and dividends | Implied from EODHD's adjusted vs unadjusted close, confirmed against EDGAR share counts (D-007, D-011) |
| Shares, SIC, fundamentals | SEC EDGAR company facts and submissions, point in time by filing date |
| Membership | `fja05680/sp500` point-in-time constituents |
| Risk-free | FRED DTB3 |
| External checks | Ken French daily factors |

Databento and Sharadar adapters are wired behind the same interface but unused (D-001). Raw
vendor data stays under `data/`, which is git-ignored. Check each vendor's license before
publishing anything derived from it; `export-site` writes model outputs only.

## Layout

```text
eqrisk/            package: config, calendar, store, sources/, staging/, model/, analytics/,
                   optimize/, validation/, pipeline/, kernels/ (Appendix A reference kernels)
configs/           model YAML (every parameter), presets, sources, EDGAR concept map, overrides/
app/               Streamlit workbench (main.py, views/)
site_template/     static viewer (index.html, app.js, analyzer.js, style.css)
tests/             unit tests; tests/golden/ runs the §17 phase acceptance on real data
docs/              BLUEPRINT.md, DECISIONS.md, RUNBOOK.md
data/              raw (immutable vintages), staged, model  (git-ignored)
```

## Tests

```powershell
uv run pytest                       # unit tests (live vendor calls are excluded by default)
uv run pytest -m golden             # phase acceptance tests on the development data
uv run ruff check .
uv run mypy --strict eqrisk/kernels
```

## License

MIT (see `LICENSE`). No code from `use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model`
(no license) is included. "Barra" and "USE4" are MSCI trademarks; they appear here only as
literature references.
