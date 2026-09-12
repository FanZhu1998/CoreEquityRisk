# EQRisk — Build Blueprint for a USE4-Style Equity Factor Risk Model (Local-First)

> **Version** 1.0 · 2026-09-11 · **Status** build spec · **Scope** S&P 500 coverage, daily updates, 1-month forecast horizon
>
> **End state.** A private Python application that, every trading day and without manual steps, pulls prices, corporate actions, fundamentals, risk-free rates and index membership; rebuilds a USE4-style fundamental factor model (country + 20 industries + 12 styles); publishes stock-level exposures **X**, factor returns **f**, the factor covariance **F** (EWMA → Newey-West → eigenfactor → volatility-regime), and specific risk **Δ** (time-series → structural → blend → Bayesian shrinkage → volatility-regime); validates itself with bias statistics; and serves results through a local UI, a serverless static viewer, and optimizer adapters (Riskfolio-Lib and native cvxpy).

---

## Table of contents

0. [How to use this blueprint in VS Code](#0-how-to-use-this-blueprint-in-vs-code)
1. [Scope, success criteria, key decisions](#1-scope-success-criteria-key-decisions)
2. [Reference repositories — what we take and what we don't](#2-reference-repositories--what-we-take-and-what-we-dont)
3. [Architecture](#3-architecture)
4. [Data layer](#4-data-layer)
5. [Universes and weights](#5-universes-and-weights)
6. [Factor structure and exposures](#6-factor-structure-and-exposures)
7. [Factor returns — daily constrained WLS](#7-factor-returns--daily-constrained-wls)
8. [Factor covariance — four layers](#8-factor-covariance--four-layers)
9. [Specific risk — five layers](#9-specific-risk--five-layers)
10. [Risk analytics API](#10-risk-analytics-api)
11. [Validation and monitoring](#11-validation-and-monitoring)
12. [Optimization integration (Riskfolio-Lib + cvxpy)](#12-optimization-integration-riskfolio-lib--cvxpy)
13. [Daily pipeline, backfill, scheduling](#13-daily-pipeline-backfill-scheduling)
14. [Storage schema](#14-storage-schema)
15. [UI and serverless deployment](#15-ui-and-serverless-deployment)
16. [Configuration reference](#16-configuration-reference)
17. [Build roadmap with acceptance tests and agent prompts](#17-build-roadmap-with-acceptance-tests-and-agent-prompts)
18. [Pitfalls checklist](#18-pitfalls-checklist)
- [Appendix A — Reference kernels (tested)](#appendix-a--reference-kernels-tested)
- [Appendix B — Starter SIC → industry map](#appendix-b--starter-sic--industry-map)
- [Appendix C — Fundamentals concept map](#appendix-c--fundamentals-concept-map)
- [Appendix D — References](#appendix-d--references)

---

## 0. How to use this blueprint in VS Code

Save this file as `docs/BLUEPRINT.md` in a new private repository. Then create an agent-instructions file at the repo root — `AGENTS.md`, `CLAUDE.md`, or `.github/copilot-instructions.md`, depending on which assistant you drive — containing the rules in §0.1 plus the line *"The source of truth is docs/BLUEPRINT.md; read the sections named in each task before writing code."*

Work strictly phase by phase (§17). Each phase is sized for one or two agent sessions, lists the blueprint sections to load into context, and ends with acceptance tests; do not start a phase until the previous phase's tests pass. VS Code's built-in Markdown preview (`Ctrl/Cmd+Shift+V`) renders the LaTeX in this file.

Provenance tags used throughout:

| Tag | Meaning |
|---|---|
| **[USE4]** | Stated in the USE4 Methodology Notes (Menchero, Orr, Wang, Aug 2011) — verified against the PDF while writing this blueprint |
| **[LAB]** | A judgment call adopted from `jeves1202/use4-learning-lab`, where the USE4 PDF is silent |
| **[CNE5]** | Convention from the Barra China model family, widely used in open-source replications, not in the USE4 notes |
| **[OURS]** | A decision specific to this build (S&P 500 scope, budget data) |
| **[VERIFY]** | Vendor detail (pricing, fields, timing) to confirm against current docs before relying on it |

### 0.1 Agent rules (paste into `AGENTS.md`)

```text
1. Point-in-time (PIT) is sacred. A value used at date t must have been knowable by the close of t.
   Fundamentals become available on the first trading day AFTER their filing date.
2. Kernels are pure functions in eqrisk/kernels/: numpy in, numpy out, no I/O, no pandas/polars,
   no globals, no randomness without an explicit np.random.Generator argument.
3. Every kernel ships with known-answer tests (synthetic truth, fixed seed) BEFORE it is wired into
   a pipeline. Appendix A of the blueprint is the reference implementation and test battery.
4. No magic numbers in code. Every parameter lives in configs/*.yaml and is validated by pydantic.
   Every run writes a manifest with the config hash and git SHA.
5. Raw data is immutable. Every transform is idempotent and re-runnable for a single date.
6. Never commit secrets or data. Keys come from .env via pydantic-settings. data/ is git-ignored.
7. Never silently drop rows. Every exclusion is counted and logged with a reason code.
8. polars for panels, numpy/scipy for linear algebra, DuckDB for queries over Parquet.
9. Type hints everywhere; ruff + mypy --strict on eqrisk/kernels; pytest must pass before commit.
10. If the blueprint is ambiguous, stop and propose options; record the decision in docs/DECISIONS.md.
```

---

## 1. Scope, success criteria, key decisions

### 1.1 Goals

The system estimates a daily-updated, one-month-horizon fundamental factor risk model for US large caps, with coverage equal to point-in-time S&P 500 membership. It produces exposures, factor returns, factor covariance, and specific risk; runs unattended; validates itself; and exposes a single `RiskModelSnapshot` object that the UI, notebooks, and optimizers all consume.

### 1.2 Non-goals

Alpha research, execution, intraday risk, non-US markets, multiple-industry (segment-weighted) exposures, analyst-forecast descriptors, and numerically matching MSCI's commercial model are out of scope for v1. §17 Phase 12 lists stretch goals.

### 1.3 Success criteria (checked by the validation suite, "in shape" rather than exact)

| Area | Criterion |
|---|---|
| Operations | Incremental daily run < 10 min on a laptop; unattended for 10 consecutive sessions; reruns are bit-identical |
| Coverage | ≥ 99% of S&P 500 constituents have complete exposures and specific risk every day |
| Regression | Constraint residual $\lvert\sum_i w_i f_i\rvert < 10^{-10}$; daily corr(country factor, cap-weighted ESTU excess return) ≥ 0.99 (USE4 reports ≈ 0.999) |
| Factor risk | Every $F_t$ symmetric PSD; mean per-factor bias statistic in [0.85, 1.15]; eigenfactor "smile" flattened after adjustment |
| Specific risk | Cap-weighted bias ≈ 1 overall (in [0.9, 1.1]) and roughly flat across size deciles (each in [0.8, 1.2]) |
| Test portfolios | 12-month MRAD close to the ideal 0.17 for normal returns [USE4 App. A]; fat tails push it toward ~0.19–0.22 |

### 1.4 Key decisions (ADR summary)

| # | Decision | Choice | Why | Revisit when |
|---|---|---|---|---|
| D1 | Horizon / cadence | Daily update, 1-month horizon, USE4S parameters; USE4L preset in config | USE4S is the monthly-horizon variant [USE4 §1.1] | You need more stable forecasts → switch preset |
| D2 | Universe | Coverage = PIT S&P 500; ESTU = coverage minus quality filters | Your scope; ESTU-expansion hook kept | Style factor t-stats are weak or industries thin |
| D3 | Fundamentals | **Recommended:** Sharadar SF1 (PIT, cheap). **Fallback:** SEC EDGAR (free, more engineering) | Saves weeks of XBRL normalization | Budget or license constraints |
| D4 | Industries | 20 starter industries from SIC + override file, single membership | GICS is licensed; 500 names cannot support USE4's 60 industries | You license GICS |
| D5 | Styles | USE4's 12 styles; analyst descriptors dropped, remaining USE4 weights renormalized | No budget consensus data | You add estimates data |
| D6 | Factor covariance | USE4S Table 4.1: vol HL 84 / NW 5, corr HL 504 / NW 2, VRA HL 42; eigen-adjust with *a* = 1.0 | Verified in the notes [USE4] | Bias battery drifts |
| D7 | Specific risk | USE4 Table 5.1: HL 84, NW 5 lags at HL 252, *q* = 0.1, VRA HL 42; CNE5-style blending γ | Verified in the notes [USE4] | Decile bias not flat |
| D8 | Storage | Parquet (partitioned) + DuckDB catalog | Local-first, fast, serverless-friendly | Multi-user access |
| D9 | UI | Streamlit workbench locally + static viewer export for serverless hosting | Fast iteration; true no-server deployment | You want one front end only |
| D10 | Optimization | Riskfolio adapter (injected Σ) + native factor-form cvxpy for ex-ante TE | Riskfolio's TE constraint is historical, not ex-ante | — |
| D11 | Naming | Neutral name ("EQRisk"); no "Barra"/"USE4" in product naming | Those are MSCI trademarks | — |

---

## 2. Reference repositories — what we take and what we don't

All three repositories were cloned and read while writing this blueprint (HEAD as of 2026-09-11).

### 2.1 `jeves1202/use4-learning-lab` — end-to-end pipeline and validation contracts

It is a spec-only course: markdown notebooks (`*_spec.ipynb`) plus textbook PDFs for each stage, built on Sharadar data, with the build notebooks deliberately withheld. Its stage order `00 cleaning → 01 ESTU → 01.5 daily panel → 02 styles → 03 industries → 04 country → 05 CSR (monthly + daily) → 06 fcov → 07 specific risk → 08 risk decomposition` is the backbone of §4–§10 here.

What we take: the pipeline ordering and dependency rules (`beta → {resvol, nlb}`, `size → nls`); the principle of pure kernels validated by known-answer tests before orchestration; its explicit PASS/FAIL validation contracts; its catalogue of decisions the USE4 PDF leaves open (NW bounds for specific risk, E0 matching, bias winsorization, history caps); and its warning that a 3σ trim with a cap-weighted mean clips mega-caps on Size.

Where we deliberately differ: it runs a monthly production regression with month-end exposures held fixed and a daily sibling regression; we run daily exposures and daily regressions throughout, as USE4 does [USE4 §1.1]. It uses complete-case data with no imputation; we implement USE4's factor-level replacement algorithm [USE4 §2.2] because portfolio analytics need 100% coverage of the index, and we flag imputed values. It uses a correlation half-life of 252 and eigen scaling *a* = 1.2; we default to USE4 Table 4.1 (504) and *a* = 1.0, keeping its values as a config preset.

License: CC BY-NC 4.0. Read and learn from it, but do not copy its text or future code into this proprietary codebase. The methodology itself traces to MSCI's published notes.

### 2.2 `UePG-21/Barra-risk-model` — covariance adjustments

About 470 lines: `cov_ewa`, `FactorCovAdjuster` (Newey-West, eigenfactor, volatility regime), and `BiasStatsCalculator`. What we take: the structure of the three adjustments and the bias-statistic routines, as a cross-check when you validate your own kernels.

Code review findings that matter if you use it as an oracle:

1. **Newey-West** uses one half-life for both volatility and correlation, and demeans with an equal-weighted mean. USE4 splits the half-lives (84 vs 504) and the lag counts (5 vs 2) [USE4 Table 4.1].
2. **Eigen simulation** draws paths of the full sample length *T* and re-estimates with an equal-weighted estimator. With EWMA weighting the effective sample is much shorter (≈ 242 obs at HL 84), so this understates the bias. Its per-eigenvalue Python loop is also slow; Appendix A vectorizes it.
3. **Volatility regime adjustment** standardizes the entire return history by one volatility vector taken from the previous matrix and demeans the result. USE4 standardizes each day's factor return by the one-day forecast made at the start of that day, without demeaning [USE4 Eq. 4.3–4.4].
4. Its demo uses `coef = 1.4`. That value belongs to USE4's *scaled* adjustment (Eq. B8), which additionally fits a parabola to *v(k)* with zero weight on the first 15 eigenfactors. USE4 production uses the milder *simulated* adjustment, Eq. B7, which is *a* = 1 [USE4 §4.2, App. B].

License: the repository has no license file, which means all rights reserved by default. Do not vendor its code; Appendix A re-implements everything from the published math.

### 2.3 `dcajasn/Riskfolio-Lib` — cvxpy optimization framework

Version 7.3.0 at HEAD, BSD-3 license (safe as a dependency). What we take: the `Portfolio` optimization engine (MV and 20+ other risk measures), linear-constraint builders (`assets_constraints`, `factors_constraints`), and risk-contribution plots and reports.

Three caveats shape §12. First, its `model="FM"` path estimates loadings by time-series regression of asset returns on factor returns (stepwise or PCR), not from cross-sectional fundamental exposures — so we inject the structured covariance $\Sigma = XFX^\top + \Delta$ directly into `port.cov` and use `model="Classic"`. Second, its tracking-error constraint (`allowTE`, `TE`) is computed from historical return paths, so ex-ante tracking error needs the native cvxpy formulation. Third, the MV model factorizes the full N×N Σ with `sqrtm`; that works at N = 500, but the factor form is faster and better conditioned. The adapter in §12.1 was run against riskfolio-lib 7.3.0 and honored exposure bounds exactly.

### 2.4 Licensing and naming

"Barra" and "USE4" are MSCI trademarks, so the product is named neutrally. MSCI's notes carry a restrictive disclaimer; the underlying techniques (EWMA, Newey-West 1987, constrained WLS, eigen-adjusted covariance per Menchero-Wang-Orr's 2012 FAJ paper, Bayesian shrinkage) are published in the open literature. Build on personal hardware and accounts with personally licensed data. If this ever becomes commercial, get a legal review that covers vendor data licenses, the MSCI terms, and your employer's IP and outside-activity policies.

---

## 3. Architecture

### 3.1 Component and data-flow diagram

```text
 ┌──────────────────────────────── Sources ────────────────────────────────┐
 │ Databento EQUS.SUMMARY (ohlcv-1d, definition)   FRED (DTB3)              │
 │ Corporate actions: Databento Reference | Sharadar ACTIONS                │
 │ Fundamentals: Sharadar SF1 | SEC EDGAR     S&P 500 PIT membership        │
 │ Ken French daily factors (validation only)                               │
 └───────────────────────────────┬─────────────────────────────────────────┘
                                 │ ingest (idempotent, per date, retry/backoff)
                                 ▼
   data/raw/<source>/<dataset>/date=YYYY-MM-DD/*.parquet          (immutable)
                                 │ stage (clean, map IDs, PIT-align)
                                 ▼
   data/staged/  security_master · ticker_history · prices · returns · mcap
                 fundamentals_pit · membership · industry · rf
                                 │ model engine (orchestration + pure kernels)
                                 ▼
   descriptors ─► exposures X_t ─► CSR on day t+1 ─► f, u
                                         │              │
                                         ▼              ▼
                           F_t: EWMA→NW→Eigen→VRA   Δ_t: TS→STR→blend→shrink→VRA
                                 │
                                 ▼
   data/model/<model_id>/...  (Parquet)  +  catalog.duckdb  +  run_manifest  +  LATEST_GOOD
          │                              │                               │
          ▼                              ▼                               ▼
   Streamlit workbench          eqrisk export-site                Optimizer adapters
   (local, read-only)           static HTML + Parquet +           Riskfolio-Lib (Σ injected)
                                DuckDB-WASM (no server)           native cvxpy (factor form)
```

### 3.2 Layer contracts

Ingest adapters implement one interface — `fetch(start, end) -> polars.DataFrame` — and write only to `data/raw`. Staging reads raw and writes PIT tables keyed by `(date, sid)`. The model engine reads staged tables and writes model tables; kernels never touch disk. The UI and optimizers read only through `ModelStore`, which returns `RiskModelSnapshot` objects (§10). This separation means the UI can be swapped (Streamlit, static site, notebook) without touching the model, and the model can be re-run without touching ingestion.

### 3.3 Technology stack

| Concern | Choice |
|---|---|
| Language, env | Python 3.12, `uv` (lockfile, `uv run`) |
| Panels / queries | `polars`, `duckdb`, `pyarrow` (Parquet, zstd) |
| Math | `numpy`, `scipy`; optional `numba` only if a profiled kernel needs it |
| Config / secrets | `pydantic` v2 + `pydantic-settings` (`.env`) |
| CLI / logging | `typer`; `structlog` (JSON logs to `logs/`) |
| Calendar | `exchange_calendars` (XNYS sessions, early closes) |
| Data clients | `databento`, `httpx` + `tenacity` (FRED, EDGAR), `nasdaq-data-link` (if Sharadar) |
| Optimization | `cvxpy` + `clarabel`, `riskfolio-lib` 7.x |
| UI | `streamlit` + `plotly` (local); Bootstrap + Plotly.js + DuckDB-WASM (static) |
| Quality | `pytest`, `hypothesis`, `ruff`, `mypy`, `pre-commit` |

### 3.4 Repository layout

```text
eqrisk/
├── AGENTS.md                      # agent rules (§0.1)
├── pyproject.toml / uv.lock
├── .env.example                   # DATABENTO_API_KEY, FRED_API_KEY, NASDAQ_DATA_LINK_API_KEY, SEC_USER_AGENT
├── configs/
│   ├── model_us_lc.yaml           # §16 — the model definition
│   ├── sources.yaml               # endpoints, rate limits, dataset IDs
│   └── overrides/
│       ├── industry_map.csv       # SIC ranges → model industry (Appendix B)
│       ├── industry_overrides.csv # per-issuer exceptions
│       └── ticker_map.csv         # manual (ticker, date) → sid fixes
├── docs/  BLUEPRINT.md · DECISIONS.md · RUNBOOK.md
├── eqrisk/
│   ├── config.py  calendar.py  ids.py  store.py  manifest.py  cli.py
│   ├── sources/        databento_px.py  corp_actions.py  sharadar.py  edgar.py  fred.py
│   │                   sp500_membership.py  famafrench.py
│   ├── staging/        security_master.py  returns.py  mcap.py  fundamentals_pit.py
│   │                   industry.py  universe.py  quality.py
│   ├── kernels/        reference.py                  # Appendix A.1 verbatim; optionally split later into
│   │                   ewma · newey_west · eigen · vra · csr · xsection · specific · descriptors · bias
│   ├── model/          descriptors.py  exposures.py  regression.py  factor_cov.py
│   │                   specific_risk.py  snapshot.py
│   ├── analytics/      risk.py  attribution.py
│   ├── optimize/       riskfolio_adapter.py  factor_form.py
│   ├── validation/     backtest.py  portfolios.py  reports.py  gates.py
│   └── pipeline/       daily.py  backfill.py  export_site.py
├── app/                streamlit_app.py  pages/01_Status.py ... pages/08_Optimizer.py
├── site_template/      index.html  app.js  styles.css           # static viewer
├── tests/              kernels/  staging/  model/  golden/
├── notebooks/          (exploration only; never imported by eqrisk/)
└── data/               raw/  staged/  model/  catalog.duckdb     # git-ignored
```

---

## 4. Data layer

### 4.1 Source matrix

| Need | Primary | Alternative | Notes |
|---|---|---|---|
| Daily OHLCV (unadjusted) and consolidated volume | Databento `EQUS.SUMMARY`, schema `ohlcv-1d` | Sharadar `SEP`; Databento `XNAS.ITCH` `ohlcv-1d` (Nasdaq-only volume) | `EQUS.SUMMARY` offers only `ohlcv-1d`, `statistics`, `definition`. Raw symbols use Nasdaq convention (`BRK.B`). The `ohlcv-1d` record is the final ~20:15 ET summary and includes post-market volume. Check history start with `client.metadata.get_dataset_range("EQUS.SUMMARY")` and cost with `metadata.get_cost` before backfilling. **[VERIFY]** plan entitlements |
| Instrument metadata | Databento `definition` schema | Sharadar `TICKERS` | Security type filter (common stock vs ETF/unit/warrant) |
| Splits, dividends, adjustment factors | Databento Reference API (`corporate_actions`, `adjustment_factors`, `security_master`) | Sharadar `ACTIONS`; an EOD vendor's split/dividend endpoints | Databento reference pricing was announced from $225/month (Oct 2024) **[VERIFY]**. Required for total returns |
| Fundamentals, PIT | Sharadar `SF1` (ARQ/ART/ARY dimensions with `datekey`) | SEC EDGAR `companyfacts` (free) | use4-learning-lab cites ~$49/month for its Sharadar tier **[VERIFY]** price and license tier |
| Shares outstanding | Sharadar `SF1.sharesbas` / `DAILY.marketcap` | EDGAR `dei:EntityCommonStockSharesOutstanding`; Databento security master **[VERIFY fields]** | Must be split-adjusted between the share count's as-of date and *t* |
| Industry | SIC code (Sharadar `TICKERS.siccode` or EDGAR `submissions.sic`) → custom map | GICS if licensed | Appendix B |
| Risk-free rate | FRED `DTB3` | FRED `DGS3MO` | Free API key |
| S&P 500 PIT membership | `fja05680/sp500` (MIT, updated 2026-09-07) | Sharadar `SP500` table | Change-date snapshots since 1996 |
| Validation benchmarks | Ken French daily factors (Dartmouth) | — | Sanity checks only; never an input |

**Data tiers.** *Tier 0 (free-first)*: Databento + EDGAR + FRED + `fja05680` — viable for prototyping, but without a corporate-actions feed your total returns are approximate. *Tier 1 (recommended)*: Databento for daily prices and volume, Sharadar for fundamentals, corporate actions, deep history, tickers/SIC, and a second membership source; FRED; `fja05680` as a cross-check. *Tier 2 (single vendor)*: Databento plus Databento Reference plus EDGAR fundamentals.

Every source sits behind an adapter so that swapping tiers is a config change: `sources.prices: databento`, `sources.corp_actions: sharadar | databento_ref`, `sources.fundamentals: sharadar | edgar`.

### 4.2 Security master and identifiers

Use an internal integer `sid` for one listed share class and an `issuer_id` (CIK, or Sharadar's company key) for the company. Maintain `ticker_history(sid, ticker, start_date, end_date)` and always resolve `(ticker, date) → sid`, never ticker alone: symbols get reused after delistings and change on renames. Databento's `instrument_id` can change over time and must not be used as a key.

Normalize symbols at ingest to the Nasdaq style (`BRK.B`); SEC uses `BRK-B`, others `BRK/B`. Keep unresolvable symbols in an exception report and fix them via `configs/overrides/ticker_map.csv`.

**Share classes.** For issuers with several listed classes in the index (GOOGL/GOOG, FOXA/FOX, NWSA/NWS), fundamentals live at the issuer level, and price ratios (BTOP, EYLD, LEV) and SIZE use issuer-level market cap summed across listed classes. Only the primary class (larger market cap) enters the ESTU; the secondary class is coverage-only, gets issuer-level exposures and its own specific risk, and carries a `linked_sid` flag, because two near-identical assets would violate the diagonal-Δ assumption (§9.6).

### 4.3 Prices, corporate actions, total returns

For each sid and session *t*, with unadjusted close $P_t$, cash dividend $D_t$ going ex on *t* (per post-split share), and split ratio $k_t$ new shares per old share effective on *t* (1 if none):

$$
r_t = \frac{k_t\,(P_t + D_t)}{P_{t-1}} - 1, \qquad r^e_t = r_t - r_{f,t}
$$

Equivalently, build a cumulative adjustment factor from the vendor's adjustment-factor feed and use $r_t = AP_t / AP_{t-1} - 1$ on back-adjusted prices; store both the unadjusted close and the adjustment factor so any adjusted series can be rebuilt.

Use **simple** excess returns in the cross-sectional regression and specific risk, because they aggregate exactly across portfolios; use log returns only where a descriptor definition calls for them (RSTR, CMRA) **[OURS]**. Special dividends count in total return but are excluded from the dividend-yield descriptor. A missing price produces a missing return, never zero; the name drops out of that day's regression and is counted.

Price QA, run daily on staged data: non-positive prices; runs of ≥ 5 identical closes; |r| > 50% with no corporate action on file (quarantine the return, add it to a review list); and, when a second price source exists, a close-to-close reconciliation.

### 4.4 Shares outstanding and market cap

$\text{mcap}_{s,t} = P_t \times SO_{s,t}$, where $SO$ is the latest share count known at *t* (PIT), multiplied by every split ratio with ex-date in $(\text{asof}_{SO}, t]$. USE4 regression weights use **total** market capitalization [USE4 §3.1], so no free-float adjustment is needed for weights. Sanity check: flag any day where $\lvert \Delta \ln \text{mcap} - \ln(1+r)\rvert > 5\%$, which usually indicates a share-count jump or a missed split.

### 4.5 Fundamentals (point-in-time)

**Availability rule.** A fundamental record is usable from `available_date = next_trading_day(filed_date)`. Sharadar's `datekey` is the filing date; EDGAR exposes `filed` per fact and an acceptance timestamp in the submissions API.

**Vintages.** Keep every vintage. For each `(issuer, item, period_end)`, the value at date *t* is the one with the latest `available_date ≤ t`, so restatements flow in only once they are public. Discard items whose `period_end` is more than 15 months before *t* (config `fundamentals.max_staleness_months`).

**Flows vs stocks.** Balance-sheet items use the latest instant value. Income and cash-flow items use trailing-twelve-month (TTM) values: Sharadar's `ART` dimension directly; on EDGAR, sum the last four discrete quarters, deriving Q4 as FY minus Q1–Q3 using ~90-day duration facts (`start`/`end`), because 10-Ks report annual values. Growth descriptors use the last five fiscal years (`ARY`, or EDGAR FY facts) and must be per-share and split-adjusted.

**Financials.** Banks and insurers often lack standard revenue tags; use the fallback chains in Appendix C, and accept that the industry factors absorb level differences in LEV and BTOP.

### 4.6 Risk-free rate (FRED)

Pull `DTB3` (3-month T-bill, percent, discount basis) from `https://api.stlouisfed.org/fred/series/observations`. The daily rate applied to the session interval from *t−1* to *t* is

$$ r_{f,t} = \frac{y_{t-1}}{100} \cdot \frac{\Delta\text{calendar days}}{360}. $$

The H.15 release lags by roughly a business day, so forward-fill the latest available value and record the fill in the manifest. The approximation error is immaterial for risk modeling.

### 4.7 S&P 500 point-in-time membership

`S&P 500 Historical Components & Changes (Updated).csv` has one row per change date with a comma-separated ticker list. Symbols with a `-YYYYMM` suffix are tickers that later ended or changed (the suffix marks when). Expand to daily membership by forward-filling snapshots between change dates, strip the suffix, and resolve `(ticker, date) → sid` through `ticker_history`. Gate daily counts to 495–510 and reconcile the latest snapshot to the current constituent list. For the regression on day *t*, membership is taken as of the close of *t−1*, consistent with the exposures.

### 4.8 Industry classification

The starter scheme maps SIC codes to 20 model industries sized for a ~500-name universe (Appendix B), plus `industry_overrides.csv` for well-known mismatches between SIC and how investors group companies (for example, Alphabet and Meta into Communication Services, payment processors into Financial Services, cruise lines into Consumer Services, midstream pipelines into Energy, life-science tools into Pharma/Biotech).

**Thin-industry rule** [USE4 §2.4 criterion (d), thresholds OURS]. With regression weights $v_n \propto \sqrt{\text{mcap}_n}$, compute each industry's effective size $N^{\text{eff}}_i = (\sum_{n\in i} v_n)^2 / \sum_{n \in i} v_n^2$. If $N^{\text{eff}}_i < 5$ or the raw count is below 8 on more than 20 of the last 60 sessions, merge the industry into its configured parent. Review the scheme monthly, change it rarely, and version it (`industry_scheme_version` in every manifest).

Single-industry membership only: USE4's multiple-industry exposures from Assets and Sales segments [USE4 §2.5] require segment data that EDGAR's `companyfacts` API does not expose. SIC is a current attribute with no reclassification history; accept this and document it.

### 4.9 History budget

| Component | Lookback |
|---|---|
| Momentum (RSTR) | 504 + 21 lag = 525 sessions |
| Beta, HSIGMA, DASTD, CMRA, STOA | 252 sessions |
| Growth (EGRO/SGRO) | 5 fiscal years of filings |
| Factor covariance | ≥ 252 sessions of factor returns before the first forecast; treat forecasts as provisional until ~756 (correlation HL 504) |
| Specific risk | 252 sessions of specific returns |

Prices must therefore start at least two years before the first exposure date, and the first trustworthy covariance forecast arrives about three years after the price history starts. If `EQUS.SUMMARY` history is shorter than you need, backfill older prices from the secondary source and reconcile closes and returns on the overlap before stitching.

---

## 5. Universes and weights

**Coverage universe** $C_t$: S&P 500 members at the close of *t* (plus an optional personal watchlist). Every coverage name receives exposures and specific risk.

**Estimation universe** $E_t \subseteq C_t$: coverage minus secondary share classes, names with no industry, names with a flagged price error that day, names with missing market cap, and names with fewer than 21 sessions of return history (new spin-offs start as coverage-only and receive structural specific risk). ESTU drives the regression and every standardization moment.

**Weights.**

$$
v_n = \frac{\sqrt{\text{mcap}_n}}{\sum_{m \in E}\sqrt{\text{mcap}_m}} \;\;\text{(regression)}, \qquad
w_i = \frac{\sum_{n \in E \cap i}\text{mcap}_n}{\sum_{n \in E}\text{mcap}_n} \;\;\text{(industry constraint)}.
$$

Standardization uses cap weights for means and equal weights for standard deviations [USE4 Eq. 2.4].

**ESTU-expansion hook** **[OURS]**. Config `estu.mode: sp500 | top_n` lets you later regress on, say, the top 1,000 US common stocks by market cap. USE4's ESTU is roughly MSCI USA IMI (~3,000 names), so with an S&P-only ESTU the SIZE, NLS, and LIQUIDITY factors are *large-cap* pure factors — interpret them accordingly.

---

## 6. Factor structure and exposures

### 6.1 Factors

Country (1, exposure ≡ 1) + industries (20 in the starter scheme, one-hot) + 12 styles, for K = 33 (fewer if the thin-industry rule merges any):
`BETA, MOMENTUM, SIZE, EARNINGS_YIELD, RESIDUAL_VOLATILITY, GROWTH, DIVIDEND_YIELD, BOOK_TO_PRICE, LEVERAGE, LIQUIDITY, NONLINEAR_SIZE, NONLINEAR_BETA`.

### 6.2 Descriptors

USE4 weights below are as quoted from the USE4 Empirical Notes Appendix A in use4-learning-lab's specs. Where a descriptor needs analyst data, it is dropped and the remaining USE4 weights are renormalized — one rule applied consistently **[OURS]**.

| Style | USE4 descriptors (weights) | Implemented weights | Window / half-life | Post-processing |
|---|---|---|---|---|
| BETA | HBETA (1.0) | 1.0 | 252 d / HL 63 | — |
| MOMENTUM | RSTR (1.0) | 1.0 | 504 d, lag 21, HL 126 | — |
| SIZE | LNCAP (1.0) | 1.0 | spot | no 3σ clip [LAB] |
| EARNINGS_YIELD | EPFWD .75, CETOP .15, ETOP .10 | CETOP .60, ETOP .40 | TTM | — |
| RESIDUAL_VOLATILITY | DASTD .75, CMRA .15, HSIGMA .10 | same | 252 d/HL 42; 12 m; 252 d/HL 63 | ⟂ BETA |
| GROWTH | EGRLF .70, EGRO .20, SGRO .10 | EGRO .667, SGRO .333 (preset `lab`: .90/.10) | 5 fiscal years | — |
| DIVIDEND_YIELD | DTOP (1.0) | 1.0 | TTM dividends | exclude specials |
| BOOK_TO_PRICE | BTOP (1.0) | 1.0 | latest balance sheet | — |
| LEVERAGE | MLEV .75, DTOA .15, BLEV .10 | same | latest balance sheet | — |
| LIQUIDITY | STOM .35, STOQ .35, STOA .30 | same | 21 d, 3 m, 12 m | optional ⟂ SIZE (off) [OURS] |
| NONLINEAR_SIZE | cube of SIZE | 1.0 | — | ⟂ SIZE, trim, standardize |
| NONLINEAR_BETA | cube of BETA | 1.0 | — | ⟂ BETA, trim, standardize |

**Definitions.** All time-series descriptors use excess returns and trailing windows ending at *t*. EWMA weights are normalized, $w_\tau \propto 2^{-(t-\tau)/h}$.

HBETA and HSIGMA come from the weighted market-model regression over the last 252 sessions with half-life 63, where $R_M$ is the cap-weighted ESTU return (prior-day caps):

$$ r^e_{n,\tau} = \alpha_n + \beta_n R^e_{M,\tau} + e_{n,\tau}, \qquad \text{HBETA}_n = \hat\beta_n,\quad \text{HSIGMA}_n = \sqrt{\textstyle\sum_\tau w_\tau \hat e_{n,\tau}^2}. $$

$$ \text{RSTR}_{n,t} = \sum_{d=L+1}^{L+T} w_d\,\big[\ln(1+r_{n,t-d}) - \ln(1+r_{f,t-d})\big], \quad T=504,\ L=21,\ w_d \propto 2^{-d/126}. $$

$$ \text{LNCAP} = \ln(\text{issuer mcap}), \qquad \text{DASTD} = \sqrt{\textstyle\sum_\tau w_\tau (r^e_\tau - \bar r^e)^2}\ \ (252\text{ d, HL }42). $$

For CMRA, let $Z(T) = \sum_{\tau=1}^{T}[\ln(1+r_\tau) - \ln(1+r_{f\tau})]$ over consecutive 21-session "months" $T = 1,\dots,12$; then $\text{CMRA} = \ln(1+Z_{\max}) - \ln(1+Z_{\min})$, guarding $Z > -1$.

$$ \text{STOM} = \ln\!\Big(\sum_{\tau=t-20}^{t}\frac{V_\tau}{SO_\tau}\Big),\quad
\text{STOQ} = \ln\!\Big(\tfrac13\sum_{j=1}^{3} e^{\text{STOM}_j}\Big),\quad
\text{STOA} = \ln\!\Big(\tfrac1{12}\sum_{j=1}^{12} e^{\text{STOM}_j}\Big), $$

with $\text{STOM}_j$ over non-overlapping 21-session blocks; require at least one valid block.

Fundamental ratios use issuer-level values and issuer mcap: $\text{ETOP} = \text{NI}^{TTM}/\text{mcap}$; $\text{CETOP} = \text{CFO}^{TTM}/\text{mcap}$ (cash earnings as operating cash flow **[OURS]**; alternative NI + D&A); $\text{DTOP} = \text{DPS}^{TTM}/P_t$; $\text{BTOP} = \text{BE}/\text{mcap}$. For leverage, $\text{MLEV} = (\text{ME}+\text{PE}+\text{LD})/\text{ME}$, $\text{DTOA} = \text{TD}/\text{TA}$, $\text{BLEV} = (\text{BE}+\text{PE}+\text{LD})/\text{BE}$ (missing if BE ≤ 0). For growth, $\text{EGRO} = \hat\beta/\lvert\overline{\text{EPS}}\rvert$ from OLS of annual EPS on $t = 1..5$ (require ≥ 3 years; the absolute value avoids sign flips **[OURS]**), and SGRO likewise with sales per share.

NLSIZE: cube the standardized SIZE exposure, take the residual of a $\sqrt{\text{mcap}}$-weighted regression on $[1, \text{SIZE}]$ over ESTU, trim, standardize. NLBETA: same procedure with BETA.

### 6.3 Exposure construction (per date, per style)

1. **Raw descriptors** for every coverage name.
2. **Outliers** [USE4 §2.2 three-group scheme; thresholds OURS]: robust z-scores (median, 1.4826·MAD on ESTU); $\lvert z\rvert > 10$ → set missing and log as a suspected data error; then clip the rest to the equal-weighted mean ± 3σ of ESTU. Do not clip SIZE (use ±4σ if you must) [LAB].
3. **Standardize descriptor**: $d_{nl} = (d^{\text{raw}}_{nl} - \mu^{\text{cap}}_l)/\sigma^{\text{eq}}_l$ on ESTU [USE4 Eq. 2.4].
4. **Combine**: $X_{nk} = \sum_{l \in k} w_l d_{nl}$, renormalizing weights over each stock's available descriptors [USE4 §2.2: use non-missing descriptors; replace only when all are missing].
5. **Re-standardize** the style [USE4 §2.3].
6. **Orthogonalize** where specified (RESVOL ⟂ BETA, NLS ⟂ SIZE, NLB ⟂ BETA) with $\sqrt{\text{mcap}}$-weighted regression fitted on ESTU and applied to all names; trim; re-standardize.
7. **Replace missing** [USE4 §2.2]: regress the style on industry dummies and SIZE over ESTU names with data ($\sqrt{\text{mcap}}$ WLS), predict missing values, set `imputed = true`; fall back to 0 (the cap-weighted mean).
8. **Final re-standardization and assertions**: ESTU cap-weighted mean $< 10^{-8}$ in absolute value and equal-weighted std within $10^{-6}$ of 1.

Industry exposures are one-hot; the country exposure is 1.

### 6.4 Exposure QA

**Factor stability coefficient** [USE4 Eq. 2.1]: the regression-weighted cross-sectional correlation of $X_{\cdot k,t}$ with $X_{\cdot k,t-21}$; USE4's rule of thumb is ≥ 0.90 desirable and < 0.80 too unstable. **Variance inflation factor** [USE4 Eq. 2.2–2.3]: regress each style on all other factors, $\text{VIF}_k = 1/(1-R_k^2)$; warn above 5 and fail above 10 **[OURS]**. Track per-style coverage and imputation rates (warn above 5%). Spot checks that catch sign errors fast: mega-caps have the highest SIZE, utilities and staples have low BETA, banks have high BTOP and LEV, and recent winners have high MOMENTUM.

---

## 7. Factor returns — daily constrained WLS

### 7.1 Timing convention

Exposures $X_{t-1}$, weights $v_{t-1}$, and constraint weights $w_{t-1}$ are computed after the close of *t−1*. The return $r^e_t$ runs from close *t−1* to close *t*. The regression on day *t* therefore uses only information known before the returns it explains. It outputs factor returns $f_t$ and specific returns $u_t = r^e_t - X_{t-1} f_t$ for every coverage name with a return (in-sample for ESTU, out-of-sample for the rest).

### 7.2 Model and solution [USE4 Eq. 3.1–3.3]

$$
r_n = f_c + \sum_i X_{ni} f_i + \sum_s X_{ns} f_s + u_n,
\qquad \min_f \sum_{n\in E} v_n\,u_n^2 \quad \text{s.t.}\quad \sum_i w_i f_i = 0 .
$$

The industry dummies sum to the country column, so the constraint identifies the system. Implement it exactly with a restriction matrix [LAB]: pick the industry $e$ with the largest cap weight (best conditioning); let $f = R\,g$, where $R \in \mathbb{R}^{K \times (K-1)}$ is the identity on every free factor and row $e$ holds $-w_i/w_e$ for each other industry $i$. Then

$$
g = (R^\top X^\top V X R)^{-1} R^\top X^\top V r, \qquad
f = R\,g = \Omega\, r, \qquad
\Omega = R\,(R^\top X^\top V X R)^{-1} R^\top X^\top V .
$$

The rows of $\Omega$ are the pure factor portfolios [USE4 §3.1]: the country row is approximately the cap-weighted ESTU; each industry row is 100% long the industry and 100% short the country portfolio with zero style exposure; each style row is dollar-neutral with unit exposure to its style and zero to everything else. Store $\Omega$'s factor-level summaries (gross, net, top names) for the UI.

Industries with no ESTU members on a given day are dropped from the design and from the constraint, and their factor return is recorded as null. Skip the day if fewer than 100 names are available (config).

### 7.3 Robustness and diagnostics

Clip only the *regression input* at the cross-sectional median ± 8 × 1.4826·MAD, so the specific return computed from the raw return still carries genuine idiosyncratic events **[OURS]**. Record per day: sample size, weighted $R^2$ (against the weighted mean), condition number of $\sqrt{V} X R$, constraint residual, each factor's return standardized by its trailing volatility (flag above 6), and $f_c$ minus the cap-weighted ESTU excess return. Monthly factor returns for validation are sums of daily factor returns **[OURS]**.

---

## 8. Factor covariance — four layers

Input: the daily factor-return panel $\Phi$ ($T \times K$, oldest row first) through date *t*, capped at the last `history_cap` = 2,520 sessions (five correlation half-lives). No forecast is produced before 252 sessions exist; forecasts before 756 sessions are flagged `provisional`.

### 8.1 Layers 1–2: EWMA with split half-lives, Newey-West, horizon [USE4 §4.1, Table 4.1]

Define the EWMA cross-moment at half-life $h$ and lag $l$, about zero, with weights aligned to the later observation (the first $l$ weights drop out without renormalization) [LAB]:

$$ C^{(h)}_l = \sum_{\tau} w^{(h)}_\tau\, \phi_{\tau-l}\,\phi_\tau^\top, \qquad
V^{(h,L)} = C^{(h)}_0 + \sum_{l=1}^{L}\Big(1 - \frac{l}{L+1}\Big)\big(C^{(h)}_l + C^{(h)\top}_l\big). $$

| Parameter | USE4S (default) | USE4L (preset) |
|---|---|---|
| Factor volatility half-life / NW lags | 84 / 5 | 252 / 5 |
| Factor correlation half-life / NW lags | 504 / 2 | 504 / 2 |
| Factor VRA half-life | 42 | 168 |

Take volatilities from $V^{vol} = V^{(84,5)}$ and correlations from $V^{cor} = V^{(504,2)}$, recombine as $F^d_0 = D_\sigma\,\rho\,D_\sigma$ [USE4 Eq. 4.1], symmetrize, and clip eigenvalues at $10^{-14}$ (recombining different half-lives is not guaranteed PSD). Scale to the one-month horizon: $F_0 = 21\,F^d_0$.

USE4 handles missing factor returns with the EM algorithm [USE4 §4.1]. For v1, zero-fill rare single-day gaps and handle a newly introduced factor with pairwise-available moments plus the PSD clip; add EM if you ever restructure industries mid-history.

### 8.2 Layer 3: eigenfactor risk adjustment [USE4 §4.2, Appendix B]

With $F_0 = U_0 D_0 U_0^\top$, for simulations $m = 1..M$: draw eigen-space returns $b_m$ ($T_{sim} \times K$) with column $k \sim N(0, D_0(k))$; rotate $f_m = b_m U_0^\top$; re-estimate $F_m$; diagonalize $F_m = U_m D_m U_m^\top$; compute the true variances of the simulated eigenfactors $\tilde D_m = U_m^\top F_0 U_m$. Then

$$ v(k) = \sqrt{\frac{1}{M}\sum_{m=1}^{M}\frac{\tilde D_m(k)}{D_m(k)}}, \qquad
\gamma(k) = a\,[v(k) - 1] + 1, \qquad
\tilde F_0 = U_0\,\mathrm{diag}\!\big(\gamma^2 D_0\big)\,U_0^\top . $$

| Choice | Default | Alternatives |
|---|---|---|
| Scaling *a* | **1.0** — the "simulated adjustment" (Eq. B7) that USE4 uses in production because the scaled version slightly over-inflates style-factor volatilities | 1.2 [LAB]; 1.4 applied to a parabolic fit of *v(k)* with zero weight on the first 15 eigenfactors (Eq. B8, the "scaled adjustment") |
| Estimator applied to simulated paths | `same` — the full Layer 1–2 estimator (split EWMA + NW) on paths of the actual window length, which is what USE4 means by "share the same estimator" | `equal_weight_neff` [LAB]: equal-weighted second moment with $T_{sim} = N_{eff}(\text{HL}_{vol}) \approx 242$; `equal_weight_full` (UePG-21 behavior, understates bias) |
| Simulations *M* | 1,000 daily in production | Backfill: recompute *v(k)* weekly and reuse — USE4 reports the simulated bias is very stable over time |
| Seed | `np.random.default_rng(hash((model_id, date)))` | Reruns must be bit-identical |

Measured in the sandbox with K = 33: `equal_weight_neff` with M = 1,000 took 0.8 s; `same` with a 1,000-day window took 0.33 s per 100 simulations (≈ 3.3 s for 1,000). Store eigenvalues, *v(k)*, *γ(k)*, and both $F_0$ and $\tilde F_0$ for the eigenfactor bias battery.

### 8.3 Layer 4: volatility regime adjustment [USE4 §4.3]

$$ B^F_t = \sqrt{\frac{1}{K}\sum_k\Big(\frac{f_{kt}}{\sigma_{kt}}\Big)^2}, \qquad
\lambda_{F,t} = \sqrt{\textstyle\sum_{\tau \le t} w^{(42)}_\tau\,(B^F_\tau)^2}, \qquad
F_t = \lambda_{F,t}^2\,\tilde F_{0,t}. $$

Here $\sigma_{kt}$ is the one-day volatility forecast made at the close of *t−1*: by default the square root of the diagonal of the lag-0 EWMA at the volatility half-life, with no NW and no horizon scaling (config `vra.sigma_source: lag0_ewma | final_daily`). Winsorize $f/\sigma$ at ±10 **[OURS]**. Compute λ with the causal recursion $s_t = \alpha B_t^2 + (1-\alpha)s_{t-1}$, $\alpha = 1 - 2^{-1/42}$, seeded at the first value; λ = 1 before any forecast exists. VRA rescales every factor volatility by one number and leaves correlations unchanged [USE4 Eq. 4.5].

Outputs per date: $F_t$ (monthly), $\sqrt{\mathrm{diag}\,F_t}$ (monthly; ×√12 for annualized display), $\lambda_{F,t}$, factor cross-sectional volatility $\sqrt{\tfrac1K\sum_k f_{kt}^2}$ [USE4 Eq. 4.6].

---

## 9. Specific risk — five layers

Inputs: daily specific returns $u_{n,t}$ from §7 for all coverage names, exposures, and market caps. Parameters from USE4 Table 5.1:

| Parameter | USE4S (default) | USE4L (preset) |
|---|---|---|
| Specific volatility half-life | 84 | 252 |
| Newey-West autocorrelation lags / half-life | 5 / 252 | 5 / 252 |
| Bayesian shrinkage *q* | 0.1 | 0.1 |
| Specific VRA half-life (= factor VRA half-life) | 42 | 168 |

### 9.1 Layer 1: time series [USE4 Eq. 5.2]

Per stock, over its last 252 valid specific returns (observation-sequence decay [LAB]):

$$ \big(\sigma^{TS}_n\big)^2 = 21\cdot C^{NW}_n \sum_\tau w^{(84)}_\tau u_{n\tau}^2,
\qquad C^{NW}_n = \frac{V^{(252,5)}_n}{C^{(252)}_{0,n}} \in [0.25,\,4]\ \text{[LAB bound]}. $$

The NW multiplier uses its own half-life (252), separate from the volatility half-life, as in USE4. The bound prevents bid-ask bounce (negative autocorrelation) from collapsing the estimate. Floor $\sigma^{TS}$ at 1% per month [LAB].

### 9.2 Layer 2: structural model [USE4 Eq. 5.3–5.4]

On names with $\gamma_n \ge 0.99$, run a $\sqrt{\text{mcap}}$-weighted regression of $\ln\sigma^{TS}_n$ on industry dummies and style exposures (the dummies act as the intercept, so drop the country column). Predict for every coverage name:

$$ \sigma^{STR}_n = E_0 \exp\Big(\sum_k X_{nk}\hat b_k\Big), \qquad E_0 = \frac{\sum_n v_n e^{\hat\varepsilon_n}}{\sum_n v_n}\ \ (\text{smearing estimator, slightly} > 1). $$

USE4 says only that $E_0$ is slightly greater than 1 and removes exponentiation bias; the weighted smearing estimator is our choice, and use4-learning-lab's cap-weighted level matching is an equivalent alternative. If fewer than 60 names qualify, drop the industry dummies and use intercept + styles.

### 9.3 Layer 3: blend [USE4 Eq. 5.5]

$\hat\sigma_n = \gamma_n\sigma^{TS}_n + (1-\gamma_n)\sigma^{STR}_n$. USE4 describes γ only qualitatively (1 when few returns are missing and returns are not excessively fat-tailed). Use the CNE5-style formula [CNE5]: with $h$ = number of valid specific returns in the last 252 sessions, $\tilde\sigma = \text{IQR}/1.35$, $\sigma_{eq}$ = sample standard deviation, and $Z = \lvert(\sigma_{eq} - \tilde\sigma)/\tilde\sigma\rvert$,

$$ \gamma_n = \min\!\Big(1, \max\!\big(0, \tfrac{h-60}{120}\big)\Big)\cdot \min\!\big(1, \max(0, e^{1-Z})\big). $$

use4-learning-lab's depth × recency γ is a config alternative. For S&P 500 names, γ < 1 mainly for spin-offs and recent listings.

### 9.4 Layer 4: Bayesian shrinkage [USE4 Eq. 5.6–5.9]

Assign coverage names to 10 market-cap deciles $s_n$. With the cap-weighted decile mean $\bar\sigma(s) = \sum_{n \in s} w_n \hat\sigma_n$ and dispersion $\Delta(s) = \sqrt{\tfrac{1}{N(s)}\sum_{n\in s}(\hat\sigma_n - \bar\sigma(s))^2}$,

$$ v_n = \frac{q\,\lvert\hat\sigma_n - \bar\sigma(s_n)\rvert}{\Delta(s_n) + q\,\lvert\hat\sigma_n - \bar\sigma(s_n)\rvert}, \qquad
\sigma^{SH}_n = v_n\,\bar\sigma(s_n) + (1 - v_n)\,\hat\sigma_n, \qquad q = 0.1. $$

use4-learning-lab found *q* = 1.0 necessary to flatten its broad-universe decile bias; in a large-cap universe start at USE4's 0.1 and calibrate on the by-decile bias statistic.

### 9.5 Layer 5: volatility regime adjustment [USE4 Eq. 5.10–5.12]

$$ B^S_t = \sqrt{\sum_{n\in E} w_{nt}\Big(\frac{u_{nt}}{\sigma_{nt}}\Big)^2}, \qquad
\lambda_{S,t} = \sqrt{\textstyle\sum_{\tau\le t} w^{(42)}_\tau (B^S_\tau)^2}, \qquad
\sigma_n = \lambda_{S,t}\,\sigma^{SH}_n, $$

with cap weights $w_{nt}$ over ESTU, and $\sigma_{nt}$ the one-day forecast from the close of *t−1* with the serial-correlation adjustment removed, $\sigma^{SH}_n / \sqrt{21\,C^{NW}_n}$ [USE4 §5.3]. Winsorize the standardized returns at ±10 [LAB]. The deliverable is $\Delta_t = \mathrm{diag}(\sigma_n^2)$ at the monthly horizon.

### 9.6 Linked share classes

Specific returns of GOOGL and GOOG are nearly perfectly correlated, so a diagonal Δ understates the risk of long-one/short-the-other positions. In v1, keep only one class per issuer in optimizations; stretch goal: a 2×2 linked-specific block (§17 Phase 12).

---

## 10. Risk analytics API

```python
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
    def asset_cov(self) -> np.ndarray: ...          # X F X' + diag(spec_var)
```

All risk quantities are monthly; multiply volatilities by √12 for annualized display. For holdings $h$ (and benchmark $h_b$, active $h_a = h - h_b$):

| Quantity | Formula |
|---|---|
| Factor exposures | $x = X^\top h$ |
| Total variance | $\sigma_p^2 = x^\top F x + h^\top \Delta h$ |
| Factor variance contribution (Euler) | $x_k (F x)_k$; sums over groups give country / industry / style totals |
| Specific variance | $\sum_n h_n^2\sigma_n^2$ |
| x-σ-ρ risk contribution | $x_k\,\sigma_k\,\rho_{k,p}$ with $\rho_{k,p} = (Fx)_k / (\sigma_k\sigma_p)$ |
| Marginal contribution | $\text{MCTR} = (X F x + \Delta h)/\sigma_p$; asset contribution $h_n\,\text{MCTR}_n$ sums to $\sigma_p$ |
| Predicted beta vs benchmark | $\beta_n = (X F x_b + \Delta h_b)_n / \sigma_b^2$ |

`analytics.risk.portfolio_risk(snapshot, h, h_b=None) -> RiskReport` returns all of the above; the §12 test confirmed the Euler contributions and $\sum_n h_n \text{MCTR}_n$ add up exactly.

---

## 11. Validation and monitoring

### 11.1 Kernel tests

The known-answer battery in Appendix A must pass before any kernel is wired into a pipeline (it passed in full while this blueprint was written). Its 24 tests cover EWMA weights and effective observations; recovery of a known covariance; exact vol/correlation recombination; Newey-West lifting an AR(1) toward its long-run variance and the NW floor under bid-ask bounce; the eigenfactor smile ($v_{\min} > 1 > v_{\max}$), PSD output, and seeded determinism; VRA fixed points (λ → 1 for bias² ≡ 1, → 2 for bias² ≡ 4); standardization moments and orthogonality; exact factor-return recovery, constraint satisfaction, and unit-exposure dollar-neutral pure style portfolios; country-factor tracking of the cap-weighted return; γ behavior; shrinkage bounds and monotonicity in *q*; Euler and x-σ-ρ additivity; the bias statistic under truth; MRAD ≈ 0.17 under normality; QLIKE minimized at the true scale; and a rolling beta that matches direct WLS.

### 11.2 Model backtest (strictly PIT)

For any portfolio with forecast volatility $\sigma_t$ made at the close of *t* and realized return $R_{t\to t+21}$, the standardized return is $b_t = R/\sigma_t$. The **bias statistic** is the standard deviation of $b$ [USE4 Eq. A2], with 95% confidence interval $1 \pm \sqrt{2/T}$ [USE4 Eq. A3]. Also compute rolling 12-month bias statistics and **MRAD**, the mean rolling absolute deviation from 1 [USE4 Eq. A4–A6], whose ideal value is about 0.17 for normal returns; and the **QLIKE** loss $\overline{b^2 - \ln b^2}$, which is robust to fat tails.

Portfolio families: (a) pure factor portfolios (rows of Ω) for per-factor bias; (b) eigenfactor portfolios before and after Layer 3 for the smile test; (c) 100 random-alpha minimum-risk factor portfolios, replicating USE4 Figs. 4.2 and 4.5; (d) S&P 500 cap-weighted and equal-weighted; (e) industry portfolios; (f) random 50-name long-only portfolios; (g) specific returns grouped by size decile and by forecast-volatility decile, replicating USE4 Figs. 5.1 and 5.2 — including the "time-series only vs full stack" comparison that shows Layers 2–5 working.

### 11.3 External sanity checks

Correlate daily factor returns with the Ken French daily factors: COUNTRY vs Mkt-RF (≥ 0.95), SIZE vs SMB (strongly negative, since SIZE is long large caps), BOOK_TO_PRICE vs HML (positive), MOMENTUM vs Mom (positive); EARNINGS_YIELD and LEVERAGE vs RMW/CMA are informational. In a VRA plot, $\lambda_F$ should jump in stress episodes such as Feb–Mar 2020 and fall below 1 in the calm that follows, the same qualitative pattern USE4 shows around 2008 [USE4 Fig. 4.6].

### 11.4 Daily QA gates

| Gate | Rule | Level |
|---|---|---|
| Data freshness | Prices for *t* for ≥ 99% of coverage | FAIL |
| Universe | 495 ≤ coverage ≤ 510 | WARN |
| Exposure moments | Cap-weighted mean < 1e-8, EW std within 1e-6 of 1, per style | FAIL |
| Imputation | Imputed share ≤ 5% per style | WARN |
| Regression | Constraint residual < 1e-10; condition number < 1e6; n ≥ 400 | FAIL |
| Fit | 0 ≤ R² ≤ 0.95 | WARN |
| Factor shock | $\lvert f_k\rvert/\sigma_k > 6$ (list factors) | WARN |
| Country tracking | $\lvert f_c - R^e_E\rvert$ < 25 bp | WARN |
| Factor covariance | Symmetric; min eigenvalue > 0 | FAIL |
| VRA | $\lambda_F, \lambda_S \in [0.5, 2.5]$ | WARN |
| Specific risk | 100% of coverage finite and positive; ESTU median annualized in [10%, 45%] | FAIL / WARN |
| Stability | Monthly stability coefficient ≥ 0.80 (fundamental styles ≥ 0.95) | WARN |

On any FAIL, write outputs under the run's ID with status `QUARANTINED`, leave `LATEST_GOOD` pointing at the previous good date, and surface the failure in the UI and notification.

---

## 12. Optimization integration (Riskfolio-Lib + cvxpy)

Both code paths below were executed while writing this blueprint: the Riskfolio adapter on riskfolio-lib 7.3.0, and the factor-form optimizer on cvxpy 1.9 with the Clarabel solver.

### 12.1 Riskfolio-Lib adapter (injected covariance)

```python
# eqrisk/optimize/riskfolio_adapter.py
import numpy as np
import pandas as pd
import riskfolio as rp

def to_riskfolio(X: pd.DataFrame, F: pd.DataFrame, spec_var: pd.Series,
                 mu: pd.Series | None = None,
                 returns_hist: pd.DataFrame | None = None) -> rp.Portfolio:
    """Inject Sigma = X F X' + Delta into a Riskfolio Portfolio (use model='Classic')."""
    Sigma = X @ F @ X.T + np.diag(spec_var.loc[X.index])
    Sigma = pd.DataFrame(Sigma.values, index=X.index, columns=X.index)
    if returns_hist is None:
        # Riskfolio needs a returns frame for asset names and shapes; the MV risk
        # measure itself reads port.cov. Pass real recent returns when you use
        # scenario-based risk measures (CVaR, CDaR, ...).
        returns_hist = pd.DataFrame(np.random.default_rng(0).multivariate_normal(
            np.zeros(len(X)), Sigma.values / 21, size=252), columns=X.index)
    port = rp.Portfolio(returns=returns_hist)
    port.mu = (mu if mu is not None else pd.Series(0.0, index=X.index)).to_frame().T
    port.cov = Sigma
    return port

def exposure_bounds(X: pd.DataFrame, bounds: dict[str, tuple[float, float]],
                    w_bench: pd.Series | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rows of A w <= b encoding lo <= x_k(w) - x_k(bench) <= hi (active exposure bounds)."""
    xb = X.T @ w_bench if w_bench is not None else pd.Series(0.0, index=X.columns)
    A, b = [], []
    for k, (lo, hi) in bounds.items():
        A.append(X[k].values);  b.append(hi + xb[k])
        A.append(-X[k].values); b.append(-(lo + xb[k]))
    return np.array(A), np.array(b).reshape(-1, 1)

# usage
port = to_riskfolio(X, F, spec_var)
port.ainequality, port.binequality = exposure_bounds(
    X, {"SIZE": (-0.1, 0.1), "BETA": (-0.1, 0.1), "MOMENTUM": (0.2, 0.5)})
w = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)
```

Riskfolio's convention is $A w \le b$ (`A @ w - B <= 0` in its source), and `rp.factors_constraints(constraints_df, loadings=X)` builds the same rows for *absolute* exposure limits from a spreadsheet-style table. To combine with asset-class limits from `rp.assets_constraints`, stack the matrices with `np.vstack`. For ex-ante tracking-error limits, use §12.2 — Riskfolio's `allowTE`/`TE` measure tracking error on historical return paths.

### 12.2 Native factor-form optimizer (ex-ante active risk)

```python
# eqrisk/optimize/factor_form.py
import numpy as np
import cvxpy as cp

def optimize_active(X, F, spec_var, w_b, alpha=None, te_max_ann=None,
                    style_idx=None, style_bound=0.10, ind_idx=None, ind_bound=0.02,
                    w_max=0.05, w_prev=None, turnover_max=None, risk_aversion=10.0):
    """F and spec_var at the monthly horizon; te_max_ann is annualized."""
    d, U = np.linalg.eigh(F)
    L = U * np.sqrt(np.maximum(d, 0.0))                 # F = L L'
    w = cp.Variable(X.shape[0])
    a = w - w_b
    y = X.T @ a                                         # active factor exposures (K)
    active_var = cp.sum_squares(L.T @ y) + cp.sum_squares(cp.multiply(np.sqrt(spec_var), a))
    cons = [cp.sum(w) == 1, w >= 0, w <= w_max]
    if style_idx is not None:
        cons += [cp.abs(y[style_idx]) <= style_bound]
    if ind_idx is not None:
        cons += [cp.abs(y[ind_idx]) <= ind_bound]
    if te_max_ann is not None:
        cons += [active_var <= te_max_ann**2 / 12.0]
    if turnover_max is not None and w_prev is not None:
        cons += [cp.norm1(w - w_prev) <= turnover_max]
    obj = (cp.Minimize(active_var) if alpha is None
           else cp.Maximize(alpha @ w - risk_aversion * active_var))
    prob = cp.Problem(obj, cons)
    prob.solve(solver=cp.CLARABEL)
    return w.value, prob.status
```

In the sandbox test (N = 500, K = 33, random alpha, 3% annual TE cap, style and industry active bounds) it solved to optimality in 0.2 s. With $w_b = 0$ the same function gives total-risk minimum variance; that solution matched a dense-Σ `quad_form` solution to within 6e-5 in every weight (solver tolerance), which is the cross-check to keep in the test suite.

### 12.3 Where each path fits

Use the native factor form for anything benchmark-relative (tracking error, active exposure bands, turnover) and as the reference implementation. Use Riskfolio when you want its broader toolkit: alternative risk measures on scenario returns, risk parity (`rp_optimization`), efficient frontiers, and its plots and Excel reports. Appendix A.3 keeps a test asserting both give the same minimum-variance portfolio under identical exposure constraints; in the sandbox they agreed to within 1e-3 in every weight.

---

## 13. Daily pipeline, backfill, scheduling

### 13.1 CLI

```text
eqrisk init                                   # create folders + catalog, validate configs, check keys
eqrisk doctor                                 # connectivity, entitlements, dataset ranges, cost estimates
eqrisk ingest   --date 2026-09-10             # pull every source for one date (idempotent)
eqrisk backfill --start 2016-01-01 --end 2026-09-10 --stage ingest|stage|model|all
eqrisk run-daily [--date D]                   # catch up every unprocessed session through D
eqrisk validate --start 2020-01-01 --end 2026-09-10   # bias battery → reports/
eqrisk snapshot --date D --out snap.npz       # RiskModelSnapshot for notebooks
eqrisk export-site --date D --years 5         # static viewer → site/
eqrisk ui                                     # Streamlit workbench
eqrisk compact --month 2026-08                # merge daily Parquet files into monthly files
```

### 13.2 Daily DAG for session D

1. **Calendar**: exit cleanly if D is not an XNYS session.
2. **Wait for data**: poll `metadata.get_dataset_range("EQUS.SUMMARY")` until its end covers D. The final summary is disseminated around 20:15 ET **[VERIFY]** when historical availability lands for your plan; on timeout, exit and let the next scheduled run catch up.
3. **Ingest**: prices for coverage plus pending index adds; corporate actions updated since the last watermark; membership diff; FRED for the last 10 days; fundamentals filed since the last watermark (EDGAR: check each CIK's submissions for new 10-K/10-Q at ≤ 10 requests/second with a declared User-Agent; Sharadar: pull by `lastupdated`).
4. **Stage**: returns and market caps for D; fundamentals as of D; industries; ESTU for D.
5. **Model**: regression for D using $X_{D-1}$ → $f_D, u_D$; descriptors and exposures $X_D$; factor covariance $F_D$ (Layers 1–4); specific risk $\Delta_D$ (Layers 1–5).
6. **Gates** (§11.4) → status; advance `LATEST_GOOD` only on pass.
7. **Export** the static viewer if configured.
8. **Notify**: a one-paragraph summary (status, λ values, largest factor moves, gate warnings) via desktop notification or email.

`run-daily` has catch-up semantics: it processes every session after the last completed one, so a sleeping laptop or a vendor delay never leaves a gap.

### 13.3 Scheduling

Run once every morning at 06:30 local (ET) and let catch-up handle the rest. Optionally add a 21:30 ET attempt for same-evening results.

```bash
# Linux/macOS cron (machine clock on ET)
30 6 * * * cd ~/eqrisk && .venv/bin/eqrisk run-daily >> logs/cron.log 2>&1
```

```text
:: Windows Task Scheduler
schtasks /Create /SC DAILY /ST 06:30 /TN "EQRisk Daily" /TR "C:\eqrisk\.venv\Scripts\eqrisk.exe run-daily"
```

On macOS prefer a `launchd` agent (`~/Library/LaunchAgents/com.eqrisk.daily.plist` with `StartCalendarInterval` Hour 6 Minute 30), because launchd runs a missed job after the machine wakes. A serverless alternative is a GitHub Actions cron in a private repository with API keys in repository secrets and the data store in Cloudflare R2 or S3; check each vendor's license before storing its data in the cloud.

### 13.4 Backfill strategy

Run stage-wise: ingest everything, stage everything, then run the model sequentially by date, because EWMA, VRA, and specific-risk states are causal. Descriptor computation across dates is embarrassingly parallel once staged. Compute eigen *v(k)* weekly during backfill. Rough budget for ten years of an S&P 500 model on a laptop: ingest is bounded by vendor rate limits, descriptors take minutes, regressions take seconds, and the covariance with weekly eigen simulation is under an hour.

---

## 14. Storage schema

Parquet with zstd compression; partition model tables by `year=YYYY/` and compact daily files monthly. `catalog.duckdb` defines views over every Parquet tree, so the UI and notebooks query with SQL.

| Table | Key | Columns |
|---|---|---|
| `security_master` | sid | issuer_id, cik, name, share_class, primary_class, sic, industry, first_date, last_date |
| `ticker_history` | sid, start_date | ticker, end_date, source |
| `prices` | date, sid | close_unadj, volume, adj_factor, ret, ret_excess, price_flag |
| `mcap` | date, sid | shares_out, shares_asof, mcap_sid, mcap_issuer |
| `fundamentals_pit` | issuer_id, item, period_end, available_date | value, fiscal_year, fiscal_period, source, vintage |
| `membership` | date, sid | in_sp500 |
| `universe` | date, sid | in_coverage, in_estu, exclusion_reason, v_reg, industry |
| `descriptors` | date, sid, descriptor | raw, clipped, standardized, is_missing |
| `exposures` | date, sid, factor | value, imputed |
| `factor_returns` | date, factor | f, t_stat, f_over_sigma |
| `regression_stats` | date | n, r2_w, cond, constraint_resid, country_minus_mkt |
| `specific_returns` | date, sid | u, in_estu |
| `factor_cov` | date, factor_i, factor_j | cov_final, cov_pre_eigen (upper triangle; consumers mirror) |
| `factor_risk_diag` | date, factor | vol_final, vol_pre_eigen, lambda_F |
| `eigen_diag` | date, k | eigenvalue, v_k, gamma_k |
| `specific_risk` | date, sid | sigma_ts, c_nw, gamma, sigma_str, sigma_blend, sigma_sh, lambda_S, sigma_final, size_decile |
| `run_manifest` | run_id | as_of, started_at, finished_at, status, config_hash, git_sha, industry_scheme_version, watermarks (JSON), gates (JSON) |
| `latest_good` | model_id | as_of, run_id |

---

## 15. UI and serverless deployment

### 15.1 Local workbench (Streamlit, multipage)

| Page | Content |
|---|---|
| 1 Status | Last run, gate results, data watermarks, λ_F and λ_S, run history, quarantine banner |
| 2 Factor returns | Cumulative returns by group, daily table with t-stats, R² series, pure-factor portfolio composition |
| 3 Factor risk | Annualized factor volatilities over time, correlation heatmap, eigen diagnostics *v(k)*/*γ(k)*, VRA vs cross-sectional volatility |
| 4 Exposures | Ticker search → exposure profile and history; per-style cross-sectional distributions; industry table with $N^{\text{eff}}$ |
| 5 Specific risk | Distribution; TS vs structural vs blended vs final; γ; bias by size decile |
| 6 Portfolio analyzer | Upload holdings CSV (ticker, weight, optional benchmark weight) → total and active risk, group decomposition, x-σ-ρ table, MCTR, top contributors, predicted beta |
| 7 Validation | Bias statistics, rolling MRAD, eigenfactor smile before/after, decile plots, Ken French correlations |
| 8 Optimizer | Riskfolio and factor-form runs with exposure bands, TE cap, turnover limit |

Performance rules: the UI is read-only through `ModelStore` (DuckDB over Parquet); cache per `as_of` with `st.cache_data`; never run model kernels in the UI except on-demand portfolio analytics and optimization; precompute heavy series (cumulative returns, rolling bias) in the pipeline. Streamlit reruns get sluggish on large frames, so keep payloads small and aggregated.

### 15.2 Static viewer (no server)

`eqrisk export-site` renders `site/` from `site_template/`: `index.html` (Bootstrap), `app.js`, Plotly.js, DuckDB-WASM, and `data/` holding the latest snapshot (X, F, specific variance, metadata — under 200 KB for 500 names × 33 factors) plus history files (five years of daily factor returns, factor volatilities, λ series, bias summaries — roughly 1 MB in Parquet). The viewer mirrors workbench pages 1–7. The portfolio analyzer runs entirely in the browser: with N ≈ 500 and K ≈ 33, computing $x = X^\top h$, the variance decomposition, and MCTR in JavaScript is instantaneous.

Hosting: Cloudflare Pages behind Cloudflare Access (email-gated) or S3 + CloudFront with signed cookies. Avoid public GitHub Pages: displaying licensed vendor data publicly can breach terms, and even derived outputs deserve a license check.

Alternative: **stlite** (Streamlit compiled to WebAssembly via Pyodide) can run the Streamlit pages in the browser with no server. Expect a heavy first load and missing native packages (no vendor clients; treat the optimizer as unavailable), so use it as a viewer only. If you would rather maintain a single front end, skip Streamlit and build the static viewer first; the `ModelStore` contract keeps either choice cheap.

---

## 16. Configuration reference

```yaml
# configs/model_us_lc.yaml
model_id: us_lc_v1
preset: use4s                     # use4s | use4l | lab   (overrides the blocks below)
calendar: XNYS
horizon_days: 21

sources:
  prices: databento               # dataset EQUS.SUMMARY, schema ohlcv-1d
  corp_actions: sharadar          # sharadar | databento_ref
  fundamentals: sharadar          # sharadar | edgar
  membership: fja05680            # fja05680 | sharadar
  risk_free: {provider: fred, series: DTB3, daycount: act360}

universe:
  coverage: sp500
  estu:
    mode: sp500                   # sp500 | top_n
    top_n: 1000
    min_history_days: 21
    exclude_secondary_share_class: true
  regression_weight: sqrt_total_mcap

industries:
  scheme_version: sic19_v1
  map_file: configs/overrides/industry_map.csv
  overrides_file: configs/overrides/industry_overrides.csv
  thin_rule: {min_neff: 5, min_count: 8, lookback_days: 60, max_breach_days: 20}

descriptors:
  outliers: {error_robust_z: 10.0, clip_sigma: 3.0, size_clip_sigma: null}
  beta:     {window: 252, half_life: 63, min_obs: 63}
  momentum: {window: 504, lag: 21, half_life: 126, min_obs: 252}
  resvol:   {weights: {DASTD: 0.75, CMRA: 0.15, HSIGMA: 0.10},
             dastd: {window: 252, half_life: 42}, cmra_months: 12,
             orthogonalize_to: [BETA]}
  earnings_yield: {weights: {CETOP: 0.60, ETOP: 0.40}, cash_earnings: cfo}
  growth:   {weights: {EGRO: 0.667, SGRO: 0.333}, years: 5, min_years: 3}
  leverage: {weights: {MLEV: 0.75, DTOA: 0.15, BLEV: 0.10}}
  liquidity: {weights: {STOM: 0.35, STOQ: 0.35, STOA: 0.30}, block: 21,
              orthogonalize_to: []}
  dividend_yield: {exclude_special: true}
  nonlinear_size: {orthogonalize_to: [SIZE]}
  nonlinear_beta: {orthogonalize_to: [BETA]}
  missing_fill: {method: regression, regressors: [industry, SIZE]}
  fundamentals: {availability_lag_sessions: 1, max_staleness_months: 15}

regression:
  constraint: cap_weighted_industry_sum_zero
  min_names: 100
  input_clip_mad: 8.0

factor_cov:
  vol:  {half_life: 84,  nw_lags: 5}
  corr: {half_life: 504, nw_lags: 2}
  history_cap_days: 2520
  min_history_days: 252
  provisional_until_days: 756
  eigen: {enabled: true, a: 1.0, n_sims: 1000, estimator: same,
          backfill_refresh: weekly, seed: date_hash}
  vra: {enabled: true, half_life: 42, sigma_source: lag0_ewma, z_cap: 10.0}

specific_risk:
  ts: {half_life: 84, nw_lags: 5, nw_half_life: 252, window: 252,
       c_nw_bounds: [0.25, 4.0], min_sigma_monthly: 0.01}
  gamma: {method: cne5, h_min: 60, h_ramp: 120}
  structural: {min_fit_names: 60, gamma_fit_threshold: 0.99, e0: smearing}
  shrinkage: {groups: size_decile, q: 0.1}
  vra: {half_life: 42, z_cap: 10.0}

gates: {universe_min: 495, universe_max: 510, imputed_max: 0.05,
        country_track_bp: 25, lambda_bounds: [0.5, 2.5], spec_median_ann: [0.10, 0.45]}

outputs:
  root: data/model
  export_site: {enabled: false, years: 5, out: site}
```

Presets change only the blocks that differ: `use4l` sets volatility half-lives to 252 and VRA half-lives to 168; `lab` sets the correlation half-life to 252, NW correlation lags to 5, eigen *a* = 1.2 with `estimator: equal_weight_neff`, shrinkage *q* = 1.0, and growth weights 0.90/0.10.

---

## 17. Build roadmap with acceptance tests and agent prompts

Twelve phases, each sized for one or two agent sessions. Paste the phase's prompt into your assistant, let it read the listed sections, and do not start the next phase until every acceptance test passes. Commit at the end of each phase with the phase number in the message.

**Development slice.** Build phases 3–7 on prices from 2016-01-04 and a model window of 2018–2021. That is enough history for momentum and the first covariance forecasts, and it contains the Feb–Mar 2020 shock that the regime-adjustment tests need. Run the full ten-year backfill only after Phase 10.

### Phase 1 — Scaffold, config, kernels

**Load:** §0.1, §3, §16, Appendix A. **Output:** repo skeleton, `config.py`, `calendar.py`, `manifest.py`, `store.py` (stub), `eqrisk/kernels/reference.py`, CLI `init`.

Acceptance: `uv run pytest tests/kernels` passes all 24 known-answer tests; `mypy --strict eqrisk/kernels` is clean; `eqrisk init` creates the folder tree and an empty `catalog.duckdb`; loading `configs/model_us_lc.yaml` with each preset validates, and an unknown key fails validation.

```text
Read docs/BLUEPRINT.md sections 0.1, 3, 16 and Appendix A. Create the repository skeleton from §3.4
with uv (Python 3.12). Save Appendix A.1 verbatim as eqrisk/kernels/reference.py and re-export its
functions from eqrisk/kernels/__init__.py. Save Appendix A.2 as tests/kernels/test_reference_kernels.py,
changing only the import line to `from eqrisk.kernels import ...`. Implement pydantic v2 models for
every block of the §16 YAML (extra="forbid"), preset overlays (use4s, use4l, lab), pydantic-settings
for .env keys, an XNYS session calendar via exchange_calendars, and a run-manifest writer
(config hash + git SHA). Add a typer CLI with `eqrisk init`. Do not write any data-source code yet.
Finish by running pytest and mypy and pasting the output.
```

### Phase 2 — Ingest adapters

**Load:** §4.1–§4.7, §13.1. **Output:** `sources/*.py`, CLI `doctor`, `ingest`, `backfill --stage ingest`.

Acceptance: `eqrisk doctor` prints each dataset's available range, entitlement status, and a Databento cost estimate (`metadata.get_cost`) for the planned backfill; ingesting the same date twice produces byte-identical raw files; a one-month pull for 20 symbols, including one delisted and one renamed ticker, lands in `data/raw/` with row counts logged; FRED and membership pulls are cached and incremental; network failures retry with backoff and never write partial files.

```text
Read docs/BLUEPRINT.md sections 4.1-4.7 and 13.1. Implement source adapters behind one interface,
fetch(start, end) -> polars.DataFrame, writing only to data/raw/<source>/<dataset>/date=YYYY-MM-DD/.
Adapters: Databento EQUS.SUMMARY (ohlcv-1d and definition, stype_in raw_symbol), corporate actions
(Sharadar ACTIONS or Databento reference, chosen by config), fundamentals (Sharadar SF1 AR* dimensions
only, or SEC EDGAR companyfacts with a declared User-Agent and <= 10 requests/second), FRED DTB3,
fja05680 S&P 500 history CSV, and Ken French daily factors. Use httpx + tenacity, atomic writes
(temp file then rename), and structlog. Add `eqrisk doctor` (ranges, entitlements, cost estimate
before any backfill) and `eqrisk ingest --date`. Write tests with recorded fixtures; no live network
calls in the unit tests.
```

### Phase 3 — Security master and staging

**Load:** §4.2–§4.9, §5, Appendix B, Appendix C. **Output:** `staging/*.py`, `configs/overrides/*.csv`.

Acceptance (golden tests):

| Check | Expectation |
|---|---|
| Splits: AAPL 4:1 (2020-08-31), TSLA 5:1 (2020-08-31) and 3:1 (2022-08-25), AMZN 20:1 (2022-06-06), GOOGL 20:1 (2022-07-18), NVDA 10:1 (2024-06-10) | No split-day return beyond ±20%; market cap continuous within the §4.4 5% check |
| Ticker change FB → META (2022-06-09) | One sid across the change; one issuer |
| Share classes GOOGL/GOOG | One issuer, primary class in ESTU, secondary coverage-only with `linked_sid` |
| Membership | Daily coverage count in 495–510 for every session; TSLA enters coverage on 2020-12-21 |
| Calendar | No rows on 2025-01-09 (NYSE closed) or on the observed Juneteenth holiday from 2022 |
| PIT | No fundamental value is used before its `available_date`; a synthetic restatement changes values only from its own filing date forward |
| Industries | Every coverage name mapped; no industry with $N^{\text{eff}} < 5$; the exception report is empty |

```text
Read docs/BLUEPRINT.md sections 4.2-4.9 and 5, plus Appendices B and C. Build the staging layer:
security_master and ticker_history with (ticker, date) -> sid resolution; total returns from
unadjusted closes plus splits and dividends (§4.3), keeping close_unadj and adj_factor; PIT market
cap with split-adjusted share counts (§4.4); PIT fundamentals with vintages and TTM construction
(§4.5, Appendix C); FRED risk-free (§4.6); daily membership expansion (§4.7); the SIC map and
override files from Appendix B with the N_eff thin-industry rule; coverage and ESTU (§5).
Implement the golden tests in the Phase 3 table of §17 as pytest cases against staged data from
the development slice. Every excluded row must carry a reason code.
```

### Phase 4 — Descriptors and exposures

**Load:** §6, Appendix A (`rolling_ewma_beta`, `standardize`, `trim_sigma`, `orthogonalize`). **Output:** `model/descriptors.py`, `model/exposures.py`.

Acceptance: for every date in the slice, each style's ESTU cap-weighted mean is below 1e-8 and equal-weighted standard deviation within 1e-6 of 1; coverage with non-imputed exposures ≥ 99% for price-based styles and ≥ 95% for fundamental styles; median monthly stability coefficient ≥ 0.90 (fundamental styles ≥ 0.95); all VIFs < 10; spot checks: corr(SIZE, log issuer mcap) > 0.98, utilities' mean BETA < 0, banks' mean BTOP > 0, the top SIZE decile holds the largest names; RESVOL and NLBETA are uncorrelated with BETA (|ρ| < 1e-8, √mcap-weighted on ESTU).

```text
Read docs/BLUEPRINT.md section 6 and Appendix A. Implement every descriptor in the §6.2 table and
the 8-step exposure construction of §6.3, using polars for panels and the Appendix A kernels for
math. Respect the dependency order beta -> {resvol, nlbeta} and size -> nlsize. Implement USE4's
missing-value replacement (industry + SIZE regression, imputed flag). Write exposures to the §14
schema. Add the §6.4 QA metrics and the Phase 4 acceptance tests from §17. Show a table of
per-style coverage, imputation rate, stability, and VIF for the last date of the slice.
```

### Phase 5 — Factor returns

**Load:** §7, Appendix A (`constrained_wls`, `industry_neff`). **Output:** `model/regression.py`.

Acceptance: constraint residual < 1e-10 every day; daily correlation between the country factor and the cap-weighted ESTU excess return ≥ 0.99; for pure style portfolios, own exposure 1 and country exposure 0 to 1e-10; correlation signs against Ken French (COUNTRY vs Mkt-RF ≥ 0.95, SIZE vs SMB < 0, BOOK_TO_PRICE vs HML > 0, MOMENTUM vs Mom > 0); average daily weighted R² in a plausible 0.20–0.60 range for large caps **[OURS]**; exposures used on day *t* are dated *t−1* (a test that shifts them by one day must fail).

```text
Read docs/BLUEPRINT.md section 7 and Appendix A. Implement the daily constrained WLS: exposures,
weights, and membership from t-1; returns for t; the restriction-matrix solution eliminating the
heaviest industry; dropping empty industries for that day; MAD clipping of the regression input
only; specific returns for all coverage names; and the §7.3 diagnostics. Persist factor_returns,
regression_stats, specific_returns, and a compact summary of Omega. Implement the Phase 5
acceptance tests from §17, including the Ken French correlation report.
```

### Phase 6 — Factor covariance

**Load:** §8, Appendix A (EWMA, Newey-West, eigen, VRA kernels). **Output:** `model/factor_cov.py`.

Acceptance: every $F_t$ symmetric with minimum eigenvalue > 0; two runs of the same date are bit-identical (seeded simulation); the eigenfactor bias statistics of the smallest ten eigenfactors fall toward 1 after Layer 3 (report before/after); $\lambda_F$ exceeds 1.5 during March 2020 and drifts below 1 in the following calm; per-factor bias statistics over 2020–2021 mostly inside [0.85, 1.15] with any exceptions listed; incremental daily update < 60 s including 1,000 simulations.

```text
Read docs/BLUEPRINT.md section 8 and Appendix A. Implement the four-layer factor covariance:
split-half-life EWMA with Newey-West (vol HL 84 / 5 lags, corr HL 504 / 2 lags from config),
vol-corr recombination with PSD clip, 21-day horizon, eigenfactor adjustment with the estimator
modes `same`, `equal_weight_neff`, `equal_weight_full` and a date-seeded Generator, then the
causal VRA using one-day lag-0 EWMA forecasts from t-1. Persist factor_cov (upper triangle),
factor_risk_diag, and eigen_diag. Implement the Phase 6 acceptance tests from §17 and plot
lambda_F over the slice.
```

### Phase 7 — Specific risk

**Load:** §9, Appendix A (specific-risk kernels). **Output:** `model/specific_risk.py`.

Acceptance: every coverage name has a finite, positive $\sigma_n$ every day; ESTU median annualized specific volatility in [10%, 45%]; cap-weighted bias statistic in [0.9, 1.1] overall and each size decile in [0.8, 1.2]; the full stack beats time-series-only on decile flatness (report both); $\lambda_S$ rises sharply in March 2020; newly listed names receive structural estimates with γ < 1.

```text
Read docs/BLUEPRINT.md section 9 and Appendix A. Implement the five-layer specific risk:
time-series EWMA with a bounded Newey-West multiplier, the sqrt-mcap WLS structural model on
names with gamma >= 0.99 with smearing retransformation, CNE5-style gamma blending, size-decile
Bayesian shrinkage (q from config), and the cap-weighted specific VRA using one-day forecasts with
the NW adjustment removed. Persist every intermediate column of the specific_risk table in §14.
Implement the Phase 7 acceptance tests from §17 and plot decile bias for TS-only vs full stack.
```

### Phase 8 — Snapshot, analytics, optimizers

**Load:** §10, §12, Appendix A.3. **Output:** `model/snapshot.py`, `analytics/risk.py`, `optimize/*.py`, CLI `snapshot`.

Acceptance: Euler contributions and $\sum_n h_n\,\text{MCTR}_n$ reproduce total risk to 1e-12; x-σ-ρ contributions sum to total risk; `asset_cov()` is PSD; the three tests in Appendix A.3 pass (factor form = dense Σ; tracking-error and exposure bands honored; Riskfolio and factor form agree); a snapshot round-trips through `.npz` unchanged.

```text
Read docs/BLUEPRINT.md sections 10 and 12 and Appendix A.3. Implement RiskModelSnapshot and
ModelStore (DuckDB views over the Parquet model tables, returning snapshots by date),
analytics.risk.portfolio_risk with every quantity in the §10 table and group aggregation, the
Riskfolio adapter and the factor-form optimizer exactly as written in §12, and `eqrisk snapshot`.
Save Appendix A.3 as tests/optimize/test_optimize.py and make it pass.
```

### Phase 9 — Validation suite

**Load:** §11.1–§11.3, §1.3. **Output:** `validation/*.py`, CLI `validate`, HTML/Markdown report in `reports/`.

Acceptance: the report shows every portfolio family in §11.2 with bias statistics, confidence bands, rolling MRAD, and QLIKE, computed on non-overlapping 21-session periods; eigenfactor smile before/after; size- and volatility-decile specific-risk bias; Ken French correlations; a summary table marking each §1.3 criterion PASS or FAIL.

```text
Read docs/BLUEPRINT.md sections 1.3 and 11.1-11.3. Implement the strictly point-in-time backtest:
forecasts at the close of t, realized returns over the next 21 sessions, standardized returns
sampled on non-overlapping periods. Build the portfolio families in §11.2, compute bias
statistics with 1 +/- sqrt(2/T) bands, rolling 12-period MRAD, and QLIKE, and write a report with
plots plus a PASS/FAIL table for every success criterion in §1.3. Add `eqrisk validate`.
```

### Phase 10 — Daily pipeline, gates, scheduling

**Load:** §11.4, §13, §14. **Output:** `pipeline/daily.py`, `pipeline/backfill.py`, `validation/gates.py`, CLI `run-daily`, `backfill`, `compact`, `docs/RUNBOOK.md`.

Acceptance: `run-daily` catches up a deliberate three-session gap in one invocation; a forced FAIL gate writes the run as `QUARANTINED` and leaves `LATEST_GOOD` unchanged; rerunning a completed date is bit-identical; an incremental day runs in < 10 minutes end to end; the scheduler fires on 10 consecutive sessions without manual intervention; the full ten-year backfill completes and passes Phase 9 on the full history.

```text
Read docs/BLUEPRINT.md sections 11.4, 13, and 14. Implement the daily DAG of §13.2 with catch-up
semantics, the vendor-readiness wait, the §11.4 gates with QUARANTINED / LATEST_GOOD handling,
run manifests, desktop or email notification, stage-wise backfill (§13.4), and monthly
compaction. Provide scheduler setup for my OS from §13.3 and write docs/RUNBOOK.md covering
failure recovery, re-running a date, and rotating API keys. Implement the Phase 10 acceptance
tests from §17.
```

### Phase 11 — UI and static viewer

**Load:** §10, §15. **Output:** `app/`, `site_template/`, `pipeline/export_site.py`, CLI `ui`, `export-site`.

Acceptance: all eight Streamlit pages load in under 2 s from cache for the latest date; the portfolio analyzer's numbers match `analytics.risk` to 1e-10 on a sample holdings file; `eqrisk export-site` produces a folder under 5 MB that works offline via `python -m http.server`, and its in-browser analyzer matches the Python analytics to 1e-8; no vendor raw data appears in the exported site.

```text
Read docs/BLUEPRINT.md sections 10 and 15. Build the Streamlit workbench with the eight pages in
the §15.1 table, reading only through ModelStore with st.cache_data per as_of date. Then build the
static viewer from §15.2 (Bootstrap, Plotly.js, DuckDB-WASM) and `eqrisk export-site`, including a
client-side portfolio analyzer that reproduces analytics.risk. Add tests comparing the JavaScript
analyzer's output (run via node) with Python on a fixture snapshot.
```

### Phase 12 — Stretch goals

Pick as needed, each behind a config flag with its own acceptance tests: ESTU expansion to the top 1,000 US common stocks (`estu.mode: top_n`), then compare style t-statistics and bias; USE4L preset run in parallel; EM estimation for missing factor returns; a linked specific-risk block for multi-class issuers (§9.6); multiple-industry exposures from segment data; analyst-based descriptors (EPFWD, EGRLF) if you license estimates; returns-based performance attribution (factor vs specific, by group); historical stress scenarios (replay factor returns from a chosen window against today's holdings); GICS industries if licensed; automated calibration of *q* and the eigen scale *a* against the bias battery.

---

## 18. Pitfalls checklist

| Area | Pitfall | Guard |
|---|---|---|
| PIT | Fundamentals aligned on period end or fiscal date instead of filing date | `available_date = next session after filing`; a test fails on any earlier use |
| PIT | Sharadar MR* dimensions carry later restatements into history | Use AR* dimensions only (ARQ, ARY, ART) |
| PIT | Survivorship: today's constituents used for history | Daily PIT membership; delisted names must be ingestible |
| PIT | Exposures and returns from the same day in the regression | Exposures from *t−1*; the shifted-exposure test must fail |
| IDs | Joining on ticker; ticker reuse and renames | Resolve `(ticker, date) → sid` only; never key on vendor `instrument_id` |
| IDs | Symbol formats differ (`BRK.B`, `BRK-B`, `BRK/B`) | Normalize at ingest; exception report for unresolved symbols |
| Returns | Price-only returns omit dividends | Total return per §4.3; reconcile to a second source when available |
| Returns | Missing price treated as a zero return | Missing stays missing; the name drops out of that day's regression |
| Returns | Split not applied to share count, so market cap jumps | Split-adjust shares from their as-of date; §4.4 continuity check |
| Rates | Annual yield used as a daily rate, or wrong day count | §4.6 formula with calendar-day accrual |
| EDGAR | 10-Q cash-flow values are year-to-date | Difference YTD values to get discrete quarters |
| EDGAR | `fy`/`fp` label the filing, not the period | Key facts by `start`/`end` dates |
| EDGAR | Revenue tags missing for banks and insurers | Appendix C fallback chains; industry factors absorb level effects |
| Exposures | 3σ clip around a cap-weighted mean truncates mega-caps on SIZE | No clip (or ±4σ) on SIZE |
| Exposures | Moments computed on coverage instead of ESTU; no re-standardization after combining | Standardize on ESTU at every step; final assertion in §6.3 |
| Exposures | Two share classes of one issuer both in ESTU | Primary class only in ESTU; issuer-level fundamentals and mcap |
| Regression | Empty industry left in the design matrix | Drop it for the day and from the constraint; record its return as null |
| Regression | Winsorizing the returns used for specific returns | Clip only the regression input; compute *u* from raw returns |
| Covariance | One half-life for volatility and correlation | Separate HL and lag counts (84/5 vs 504/2) |
| Covariance | Recombined matrix not PSD | Symmetrize and clip eigenvalues after recombination |
| Covariance | Eigen simulation on full-length equal-weighted paths understates bias | `same` estimator on actual-length paths (or `equal_weight_neff`) |
| Covariance | VRA standardizes history with today's volatility, or demeans | One-day forecasts from *t−1*, no demeaning, causal EWMA |
| Covariance | Horizon scaling applied twice (monthly σ fed into VRA) | VRA uses daily lag-0 forecasts; scale by 21 once |
| Units | Monthly and annual quantities mixed (TE caps, UI) | Store monthly; convert only at display and constraint entry (TE²/12) |
| Specific | Unbounded NW multiplier collapses risk for bid-ask-bounce names | Bound C_NW to [0.25, 4] and floor σ at 1% per month |
| Specific | Structural model fit on low-γ names; no retransformation factor | Fit on γ ≥ 0.99; apply E0 |
| Specific | Near-identical share classes treated as diversifying | Diagonal Δ assumption: one class per issuer in optimizations |
| Validation | Overlapping 21-day returns sampled daily treated as independent | Non-overlapping periods for bias stats and confidence intervals |
| Validation | Checking bias only in aggregate | Also by size decile, volatility decile, factor, and eigenfactor |
| Ops | Monte Carlo without a fixed seed makes reruns differ | Date-seeded Generator; bit-identical rerun test |
| Ops | Vendor revisions silently change history | Keep raw vintages; manifests record watermarks and config hash |
| Ops | Holidays and ad-hoc closures (e.g., 2025-01-09) | `exchange_calendars` XNYS sessions only |
| Licensing | Vendor data or derived outputs published on a public site | Private hosting behind authentication; check each license |
| Security | API keys committed to git | `.env` + pydantic-settings; `.env` in `.gitignore`; pre-commit secret scan |

---

## Appendix A — Reference kernels (tested)

These files were executed in a clean sandbox on 2026-09-11 (Python 3.12.3, numpy 2.5.3, scipy 1.17.1, cvxpy 1.9.2 with Clarabel 0.11.1, riskfolio-lib 7.3.0, pandas 3.0.5). Results: **24 of 24** kernel tests passed in 1.4 s; **3 of 3** optimizer cross-checks passed in about 21 s. The A.3 tests ran against the §12 code blocks extracted verbatim from this document; only their import paths were changed for the package layout.

Timing from the `slow` benchmark on that machine, K = 33: eigen simulation with `equal_weight_neff` (T = 242, M = 1,000) took 0.64 s; with the production estimator on 1,000-day paths, 100 simulations took 0.35 s.

Register the benchmark marker in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = ["slow: timing benchmarks"]
```

### A.1 `eqrisk/kernels/reference.py`

```python
"""EQRisk reference kernels for a USE4-style factor risk model.

Pure functions: numpy arrays in, numpy arrays out. No I/O, no pandas.

Conventions
-----------
* Time-series arrays are shaped (T, K): rows = dates, oldest first, newest last.
* Half-lives are in observations (trading days).
* Second moments are taken about zero (no demeaning); daily means are ~0.
* Kernels return daily-horizon quantities; callers apply the 21-day horizon.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

Estimator = Callable[[np.ndarray], np.ndarray]

# --------------------------------------------------------------------------- #
# EWMA / Newey-West (USE4 Sec. 4.1)                                           #
# --------------------------------------------------------------------------- #


def ewma_weights(T: int, half_life: float) -> np.ndarray:
    """Normalized exponential weights, newest observation last (largest)."""
    lam = 0.5 ** (1.0 / half_life)
    w = lam ** np.arange(T - 1, -1, -1, dtype=float)
    return w / w.sum()


def effective_obs(T: int, half_life: float) -> float:
    """Effective number of observations 1 / sum(w^2)."""
    w = ewma_weights(T, half_life)
    return float(1.0 / np.sum(w**2))


def ewma_lag_cov(R: np.ndarray, half_life: float, lag: int = 0) -> np.ndarray:
    """EWMA cross-moment C_lag = sum_t w_t r_{t-lag} r_t' (weights on the later obs).

    The first `lag` weights drop out without renormalization.
    Returns K x K (not symmetric for lag > 0).
    """
    T = R.shape[0]
    w = ewma_weights(T, half_life)
    if lag == 0:
        return (R * w[:, None]).T @ R
    lead, lagged = R[lag:], R[:-lag]
    return (lagged * w[lag:, None]).T @ lead


def newey_west_cov(R: np.ndarray, half_life: float, lags: int) -> np.ndarray:
    """Bartlett-weighted HAC covariance: C0 + sum_l (1 - l/(L+1)) (C_l + C_l')."""
    V = ewma_lag_cov(R, half_life, 0)
    for lag in range(1, lags + 1):
        C = ewma_lag_cov(R, half_life, lag)
        V = V + (1.0 - lag / (lags + 1.0)) * (C + C.T)
    return 0.5 * (V + V.T)


def psd_clip(A: np.ndarray, floor: float = 1e-14) -> np.ndarray:
    """Symmetrize and clip eigenvalues at `floor`."""
    A = 0.5 * (A + A.T)
    d, U = np.linalg.eigh(A)
    return (U * np.maximum(d, floor)) @ U.T


def combine_vol_corr(V_vol: np.ndarray, V_corr: np.ndarray) -> np.ndarray:
    """Volatilities from V_vol, correlations from V_corr: F = D_s C D_s (USE4 Eq. 4.1)."""
    s = np.sqrt(np.clip(np.diag(V_vol), 0.0, None))
    c = np.sqrt(np.clip(np.diag(V_corr), 1e-300, None))
    C = V_corr / np.outer(c, c)
    return psd_clip(np.outer(s, s) * C)


def factor_cov_nw(R: np.ndarray, hl_vol: float, lags_vol: int,
                  hl_corr: float, lags_corr: int) -> np.ndarray:
    """Daily-horizon factor covariance, split half-lives, NW-adjusted (Layers 1-2)."""
    return combine_vol_corr(newey_west_cov(R, hl_vol, lags_vol),
                            newey_west_cov(R, hl_corr, lags_corr))


# --------------------------------------------------------------------------- #
# Eigenfactor risk adjustment (USE4 Sec. 4.2, Appendix B)                     #
# --------------------------------------------------------------------------- #


def eigen_bias(F0: np.ndarray, T_sim: int, n_sims: int, rng: np.random.Generator,
               estimator: Estimator | None = None, batch: int = 100) -> np.ndarray:
    """Simulated volatility bias v(k) (Eq. B7), eigenvalues in ascending order.

    estimator: callable (T, K) -> (K, K). None = equal-weighted second moment.
    Pass the production estimator, e.g. lambda R: factor_cov_nw(R, 84, 5, 504, 2),
    with T_sim = actual window length to follow USE4's "same estimator" logic.
    """
    K = F0.shape[0]
    d0, U0 = np.linalg.eigh(F0)
    d0 = np.maximum(d0, 1e-14)
    acc = np.zeros(K)
    done = 0
    while done < n_sims:
        m = min(batch, n_sims - done)
        b = rng.standard_normal((m, T_sim, K)) * np.sqrt(d0)    # eigen-space returns
        f = b @ U0.T                                            # (m, T, K) factor returns
        if estimator is None:
            Fm = np.einsum("mtk,mtj->mkj", f, f) / T_sim
        else:
            Fm = np.stack([estimator(f[i]) for i in range(m)])
        dm, Um = np.linalg.eigh(Fm)                             # batched
        dm = np.maximum(dm, 1e-14)
        true_var = np.einsum("mki,kl,mli->mi", Um, F0, Um)       # diag(Um' F0 Um)
        acc += (true_var / dm).sum(axis=0)
        done += m
    return np.sqrt(acc / n_sims)


def eigen_adjust(F: np.ndarray, v: np.ndarray, a: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Apply gamma(k) = a (v(k) - 1) + 1 to the eigenvalues. a = 1 is Eq. B7."""
    d0, U0 = np.linalg.eigh(F)
    gamma = a * (v - 1.0) + 1.0
    F_eig = (U0 * (np.maximum(d0, 1e-14) * gamma**2)) @ U0.T
    return 0.5 * (F_eig + F_eig.T), gamma


# --------------------------------------------------------------------------- #
# Volatility regime adjustment (USE4 Eq. 4.3-4.5, 5.10-5.12)                  #
# --------------------------------------------------------------------------- #


def vra_lambda(bias_sq: np.ndarray, half_life: float) -> np.ndarray:
    """Causal lambda_t = sqrt(EWMA_{<=t}(B^2)); bias_sq is a 1-D daily series."""
    alpha = 1.0 - 0.5 ** (1.0 / half_life)
    out = np.empty(len(bias_sq), dtype=float)
    s = float(bias_sq[0])
    for t, b in enumerate(bias_sq):
        s = alpha * float(b) + (1.0 - alpha) * s if t else float(b)
        out[t] = np.sqrt(s)
    return out


def factor_bias_sq(f_t: np.ndarray, sigma_prev: np.ndarray, z_cap: float = 10.0) -> float:
    """(B^F_t)^2 = mean_k (f_kt / sigma_kt)^2; sigma = one-day forecast from t-1."""
    z = np.clip(f_t / sigma_prev, -z_cap, z_cap)
    return float(np.mean(z**2))


def specific_bias_sq(u_t: np.ndarray, sigma_prev: np.ndarray, capw: np.ndarray,
                     z_cap: float = 10.0) -> float:
    """(B^S_t)^2 = sum_n w_n (u_nt / sigma_nt)^2 over ESTU, cap weights."""
    z = np.clip(u_t / sigma_prev, -z_cap, z_cap)
    w = capw / capw.sum()
    return float(np.sum(w * z**2))


# --------------------------------------------------------------------------- #
# Cross-sectional machinery (USE4 Sec. 2-3)                                   #
# --------------------------------------------------------------------------- #


def standardize(x: np.ndarray, capw: np.ndarray, estu: np.ndarray) -> np.ndarray:
    """USE4 Eq. 2.4: cap-weighted mean, equal-weighted std, moments on ESTU."""
    ok = estu & np.isfinite(x)
    mu = np.average(x[ok], weights=capw[ok])
    sd = np.std(x[ok], ddof=0)
    return (x - mu) / sd


def trim_sigma(x: np.ndarray, estu: np.ndarray, k: float = 3.0) -> np.ndarray:
    """Clip to mean +/- k*std (equal-weighted moments on ESTU)."""
    ok = estu & np.isfinite(x)
    mu, sd = x[ok].mean(), x[ok].std()
    return np.clip(x, mu - k * sd, mu + k * sd)


def orthogonalize(y: np.ndarray, Z: np.ndarray, w: np.ndarray,
                  fit_mask: np.ndarray) -> np.ndarray:
    """Residual of WLS y ~ [1, Z] fitted on fit_mask with weights w, applied to all."""
    A = np.column_stack([np.ones(len(y)), Z])
    ok = fit_mask & np.isfinite(y) & np.all(np.isfinite(A), axis=1)
    sw = np.sqrt(w[ok])
    beta, *_ = np.linalg.lstsq(A[ok] * sw[:, None], y[ok] * sw, rcond=None)
    return y - A @ beta


def industry_neff(v: np.ndarray) -> float:
    """Effective number of names (sum v)^2 / sum v^2 for regression weights v."""
    return float(v.sum() ** 2 / np.sum(v**2))


def constrained_wls(r: np.ndarray, X: np.ndarray, v: np.ndarray, ind_cols: np.ndarray,
                    ind_capw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """USE4 Eq. 3.1-3.3 with the constraint sum_i w_i f_i = 0 over industry columns.

    r (N,) excess returns; X (N, K) = [country | industries | styles];
    v (N,) regression weights (e.g. sqrt mcap); ind_cols: industry column indices;
    ind_capw: industry cap weights summing to 1. All industries must be non-empty.
    Returns f (K,), u (N,), Omega (K, N) with f = Omega r (pure factor portfolios).
    """
    K = X.shape[1]
    j_e = int(np.argmax(ind_capw))
    e = int(ind_cols[j_e])                              # eliminate the heaviest industry
    free = [k for k in range(K) if k != e]
    pos = {k: j for j, k in enumerate(free)}
    R = np.zeros((K, K - 1))
    for k, j in pos.items():
        R[k, j] = 1.0
    for c, w_i in zip(ind_cols, ind_capw):
        if int(c) != e:
            R[e, pos[int(c)]] = -w_i / ind_capw[j_e]    # f_e = -sum_{i!=e} (w_i/w_e) f_i
    XR = X @ R
    V = v / v.sum()
    A = XR.T @ (XR * V[:, None])
    Omega = R @ np.linalg.solve(A, (XR * V[:, None]).T)
    f = Omega @ r
    return f, r - X @ f, Omega


# --------------------------------------------------------------------------- #
# Descriptors                                                                 #
# --------------------------------------------------------------------------- #


def rolling_ewma_beta(r_ex: np.ndarray, m_ex: np.ndarray, window: int = 252,
                      hl: float = 63, min_obs: int = 63,
                      chunk: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """HBETA and HSIGMA: EWMA-weighted market-model regression on a trailing window.

    r_ex (T, N) stock excess returns (NaN allowed); m_ex (T,) market excess returns.
    Returns (beta, hsigma), each (T, N); rows before the first full window are NaN.
    """
    T, N = r_ex.shape
    w = ewma_weights(window, hl)
    beta = np.full((T, N), np.nan)
    hsig = np.full((T, N), np.nan)
    Rw = sliding_window_view(r_ex, window, axis=0)       # (T-W+1, N, W) view, no copy
    Mw = sliding_window_view(m_ex, window)               # (T-W+1, W)
    for s in range(0, Rw.shape[0], chunk):
        Rc = Rw[s:s + chunk]
        M = Mw[s:s + chunk][:, None, :]
        ok = np.isfinite(Rc)
        ww = np.where(ok, w, 0.0)
        sw = ww.sum(-1)
        Rz = np.where(ok, Rc, 0.0)
        mb = (ww * M).sum(-1) / sw
        rb = (ww * Rz).sum(-1) / sw
        dm = M - mb[..., None]
        dr = Rz - rb[..., None]
        var = (ww * dm**2).sum(-1) / sw
        b = (ww * dr * dm).sum(-1) / sw / var
        e = np.where(ok, dr - b[..., None] * dm, 0.0)
        hs = np.sqrt((ww * e**2).sum(-1) / sw)
        bad = ok.sum(-1) < min_obs
        b[bad] = np.nan
        hs[bad] = np.nan
        beta[window - 1 + s: window - 1 + s + len(Rc)] = b
        hsig[window - 1 + s: window - 1 + s + len(Rc)] = hs
    return beta, hsig


# --------------------------------------------------------------------------- #
# Specific risk (USE4 Sec. 5)                                                 #
# --------------------------------------------------------------------------- #


def specific_ts_var(u: np.ndarray, hl_vol: float, nw_lags: int, nw_hl: float,
                    floor_frac: float = 0.25, cap_frac: float = 4.0) -> float:
    """One stock's daily specific variance with a bounded NW multiplier (USE4 Eq. 5.2).

    sigma^2 = C_NW * EWMA_{hl_vol}(u^2), C_NW = V_NW / V_0 (both at nw_hl),
    bounded to [floor_frac, cap_frac] so bid-ask bounce cannot collapse the estimate.
    Missing days are skipped (observation-sequence decay).
    """
    x = u[np.isfinite(u)][:, None]
    v0 = ewma_lag_cov(x, hl_vol, 0)[0, 0]
    c0 = ewma_lag_cov(x, nw_hl, 0)[0, 0]
    cnw = newey_west_cov(x, nw_hl, nw_lags)[0, 0] / c0
    return float(v0 * np.clip(cnw, floor_frac, cap_frac))


def coverage_gamma(u_window: np.ndarray, h_min: int = 60, h_ramp: int = 120) -> float:
    """CNE5-style blending coefficient (not spelled out in the USE4 notes)."""
    x = u_window[np.isfinite(u_window)]
    h = len(x)
    if h < 2:
        return 0.0
    q1, q3 = np.percentile(x, [25, 75])
    s_rob = (q3 - q1) / 1.35
    s_eq = x.std(ddof=1)
    z = abs((s_eq - s_rob) / s_rob) if s_rob > 0 else np.inf
    return float(min(1.0, max(0.0, (h - h_min) / h_ramp)) * min(1.0, max(0.0, np.exp(1 - z))))


def bayes_shrink(sigma: np.ndarray, capw: np.ndarray, group: np.ndarray,
                 q: float = 0.1) -> np.ndarray:
    """USE4 Eq. 5.6-5.9: shrink toward the cap-weighted mean of each size group."""
    out = sigma.copy()
    for g in np.unique(group):
        m = group == g
        mu = np.average(sigma[m], weights=capw[m])
        delta = np.sqrt(np.mean((sigma[m] - mu) ** 2))
        dev = np.abs(sigma[m] - mu)
        den = delta + q * dev
        vshr = np.divide(q * dev, den, out=np.zeros_like(dev), where=den > 0)
        out[m] = vshr * mu + (1.0 - vshr) * sigma[m]
    return out


# --------------------------------------------------------------------------- #
# Risk analytics (Sec. 10)                                                    #
# --------------------------------------------------------------------------- #


def risk_decomposition(X: np.ndarray, F: np.ndarray, spec_var: np.ndarray,
                       h: np.ndarray) -> dict[str, np.ndarray | float]:
    """Total risk, Euler factor contributions, specific variance, MCTR (monthly units)."""
    x = X.T @ h
    Fx = F @ x
    fac_var = float(x @ Fx)
    spc_var = float(np.sum(h**2 * spec_var))
    sigma = np.sqrt(fac_var + spc_var)
    return {
        "sigma": sigma,
        "exposures": x,
        "factor_contrib_var": x * Fx,                   # sums to fac_var
        "specific_var": spc_var,
        "mctr": (X @ Fx + spec_var * h) / sigma,         # sum(h * mctr) == sigma
        "xsr_corr": Fx / (np.sqrt(np.diag(F)) * sigma),  # rho(k, p) for x-sigma-rho
    }


# --------------------------------------------------------------------------- #
# Validation (USE4 Appendix A)                                                #
# --------------------------------------------------------------------------- #


def bias_statistic(ret: np.ndarray, vol: np.ndarray) -> float:
    """USE4 Eq. A2: standard deviation of standardized returns (ddof=1)."""
    return float(np.std(ret / vol, ddof=1))


def mrad(b: np.ndarray, window: int = 12) -> float:
    """Mean rolling absolute deviation of rolling bias statistics from 1.

    b: (T, P) standardized returns for P portfolios on NON-OVERLAPPING periods.
    """
    b = np.atleast_2d(b.T).T
    stats = [np.std(b[t - window:t], axis=0, ddof=1) for t in range(window, len(b) + 1)]
    return float(np.mean(np.abs(np.array(stats) - 1.0)))


def qlike(ret: np.ndarray, vol: np.ndarray, eps: float = 1e-12) -> float:
    """Q-likelihood loss mean(b^2 - ln b^2); minimized in expectation at the true vol."""
    b2 = (ret / vol) ** 2 + eps
    return float(np.mean(b2 - np.log(b2)))
```

### A.2 `tests/kernels/test_reference_kernels.py`

As tested, the file imports from a sibling `reference_kernels.py`; in the repository change only that import line to `from eqrisk.kernels import (...)`.

```python
"""Known-answer tests for reference_kernels.py. Run: pytest -q test_reference_kernels.py"""
import time

import numpy as np
import pytest

from reference_kernels import (
    bayes_shrink, bias_statistic, combine_vol_corr, constrained_wls, coverage_gamma,
    effective_obs, eigen_adjust, eigen_bias, ewma_lag_cov, ewma_weights, factor_cov_nw,
    industry_neff, mrad, newey_west_cov, orthogonalize, qlike, risk_decomposition,
    rolling_ewma_beta, specific_ts_var, standardize, vra_lambda,
)


def _cross_section(seed: int = 7, N: int = 500, n_ind: int = 20, n_sty: int = 12):
    rng = np.random.default_rng(seed)
    ind = rng.integers(0, n_ind, N)
    ind[:n_ind] = np.arange(n_ind)                        # every industry non-empty
    mcap = np.exp(rng.normal(10, 1.2, N))
    estu = np.ones(N, bool)
    S = np.column_stack([standardize(rng.standard_normal(N), mcap, estu) for _ in range(n_sty)])
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], S])
    ind_cols = np.arange(1, 1 + n_ind)
    capw = mcap / mcap.sum()
    ind_capw = np.array([capw[ind == i].sum() for i in range(n_ind)])
    return rng, X, mcap, capw, ind_cols, ind_capw


# ---- EWMA / NW ------------------------------------------------------------ #

def test_ewma_weights_normalized_and_increasing():
    w = ewma_weights(500, 84)
    assert abs(w.sum() - 1) < 1e-12 and np.all(np.diff(w) > 0)


def test_effective_obs_hl84():
    assert abs(effective_obs(5000, 84) - 242.4) < 0.5


def test_ewma_recovers_known_sigma():
    rng = np.random.default_rng(1)
    A = rng.standard_normal((4, 4))
    S = A @ A.T + 4 * np.eye(4)
    R = rng.multivariate_normal(np.zeros(4), S, size=40000)
    E = ewma_lag_cov(R, 1e12)
    assert np.allclose(E, R.T @ R / len(R), atol=1e-8)        # huge HL == uniform
    assert np.linalg.norm(E - S) / np.linalg.norm(S) < 0.05


def test_combine_vol_corr_exact():
    rng = np.random.default_rng(2)
    A = rng.standard_normal((4, 4))
    S = A @ A.T + 4 * np.eye(4)
    Vv, Vc = 1.7 * S, S + np.diag(np.arange(1.0, 5.0))
    F = combine_vol_corr(Vv, Vc)
    corr = lambda M: M / np.sqrt(np.outer(np.diag(M), np.diag(M)))
    assert np.allclose(np.diag(F), np.diag(Vv))
    assert np.allclose(corr(F), corr(Vc))


def test_newey_west_lifts_ar1_toward_long_run_variance():
    rng = np.random.default_rng(3)
    T, rho = 60000, 0.5
    e = rng.standard_normal(T)
    x = np.empty(T)
    x[0] = e[0]
    for t in range(1, T):
        x[t] = rho * x[t - 1] + e[t]
    v0 = ewma_lag_cov(x[:, None], 1e9)[0, 0]                 # ~ 1/(1-rho^2) = 1.333
    vnw = newey_west_cov(x[:, None], 1e9, 20)[0, 0]          # long-run 1/(1-rho)^2 = 4
    assert vnw > v0 and abs(vnw - 4.0) / 4.0 < 0.10


# ---- Eigenfactor adjustment ---------------------------------------------- #

def test_eigen_smile_and_psd():
    rng = np.random.default_rng(4)
    B = rng.standard_normal((10, 10))
    F0 = B @ B.T / 10 + 0.05 * np.eye(10)
    v = eigen_bias(F0, T_sim=30, n_sims=400, rng=np.random.default_rng(1))
    Fe, gamma = eigen_adjust(F0, v, a=1.0)
    assert v[0] > 1 > v[-1]                                  # smallest eigenfactors most underforecast
    assert np.linalg.eigvalsh(Fe).min() > -1e-10
    assert np.allclose(gamma, v)                             # a = 1 -> gamma = v


def test_eigen_bias_deterministic_with_seed():
    rng = np.random.default_rng(5)
    B = rng.standard_normal((6, 6))
    F0 = B @ B.T + np.eye(6)
    v1 = eigen_bias(F0, 50, 200, np.random.default_rng(42))
    v2 = eigen_bias(F0, 50, 200, np.random.default_rng(42))
    assert np.array_equal(v1, v2)


@pytest.mark.slow
def test_eigen_timing_k33():
    rng = np.random.default_rng(6)
    B = rng.standard_normal((33, 600)) * 0.01
    Fd = B @ B.T / 600
    t0 = time.time()
    v_eq = eigen_bias(Fd, T_sim=242, n_sims=1000, rng=np.random.default_rng(3))
    t1 = time.time()
    est = lambda R: factor_cov_nw(R, 84, 5, 504, 2)
    v_same = eigen_bias(Fd, T_sim=1000, n_sims=100, rng=np.random.default_rng(3), estimator=est)
    t2 = time.time()
    print(f"\nequal_weight_neff M=1000: {t1 - t0:.2f}s  same-estimator M=100: {t2 - t1:.2f}s")
    assert v_eq[0] > 1 > v_eq[-1] and v_same[0] > 1
    assert t1 - t0 < 30 and t2 - t1 < 30


# ---- Volatility regime adjustment ---------------------------------------- #

def test_vra_fixed_points():
    assert np.isclose(vra_lambda(np.ones(500), 42)[-1], 1.0, atol=1e-6)
    assert abs(vra_lambda(np.full(2000, 4.0), 42)[-1] - 2.0) < 1e-3


# ---- Cross-sectional regression ------------------------------------------ #

def test_standardize_moments():
    rng, X, mcap, *_ = _cross_section()
    z = standardize(rng.lognormal(size=500), mcap, np.ones(500, bool))
    assert abs(np.average(z, weights=mcap)) < 1e-12 and abs(z.std() - 1) < 1e-12


def test_orthogonalize_residual_uncorrelated():
    rng = np.random.default_rng(8)
    z = rng.standard_normal(400)
    y = 0.8 * z + rng.standard_normal(400)
    w = rng.uniform(0.5, 2.0, 400)
    res = orthogonalize(y, z, w, np.ones(400, bool))
    assert abs(np.sum(w * res * z)) < 1e-8 and abs(np.sum(w * res)) < 1e-8


def test_csr_recovers_factor_returns_and_constraint():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    f_true = rng.normal(0, 0.01, X.shape[1])
    f_true[ind_cols] -= ind_capw @ f_true[ind_cols]          # enforce sum w_i f_i = 0
    f_hat, u, Om = constrained_wls(X @ f_true, X, np.sqrt(mcap), ind_cols, ind_capw)
    assert np.allclose(f_hat, f_true, atol=1e-10)
    assert abs(ind_capw @ f_hat[ind_cols]) < 1e-12
    assert np.allclose(u, 0, atol=1e-12)


def test_pure_style_portfolios_unit_exposure_and_dollar_neutral():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    _, _, Om = constrained_wls(rng.standard_normal(500), X, np.sqrt(mcap), ind_cols, ind_capw)
    sty = np.arange(1 + len(ind_cols), X.shape[1])
    E = Om[sty] @ X                                           # exposures of style portfolios
    assert np.allclose(E[:, sty], np.eye(len(sty)), atol=1e-10)
    assert np.allclose(E[:, 0], 0, atol=1e-10)                # dollar-neutral (country exp = 0)


def test_country_factor_tracks_cap_weighted_market():
    rng, X, mcap, capw, ind_cols, ind_capw = _cross_section()
    fc, mk = [], []
    for _ in range(250):
        f_t = rng.normal(0, 0.01, X.shape[1])
        f_t[ind_cols] -= ind_capw @ f_t[ind_cols]
        r = X @ f_t + rng.normal(0, 0.02, 500) / np.sqrt(mcap / mcap.mean()) ** 0.25
        fh, _, _ = constrained_wls(r, X, np.sqrt(mcap), ind_cols, ind_capw)
        fc.append(fh[0])
        mk.append(capw @ r)
    assert np.corrcoef(fc, mk)[0, 1] > 0.99


def test_industry_neff():
    assert np.isclose(industry_neff(np.ones(12)), 12.0)
    assert industry_neff(np.array([100.0, 1, 1, 1])) < 1.1


# ---- Descriptors ---------------------------------------------------------- #

def test_rolling_beta_matches_direct_wls():
    rng = np.random.default_rng(0)
    T, N, W = 600, 50, 252
    m = rng.normal(0, 0.01, T)
    true_b = rng.uniform(0.5, 1.5, N)
    r = m[:, None] * true_b + rng.normal(0, 0.015, (T, N))
    r[rng.random((T, N)) < 0.02] = np.nan
    b, hs = rolling_ewma_beta(r, m)
    t, n = 450, 7
    w = ewma_weights(W, 63)
    y, x = r[t - W + 1:t + 1, n], m[t - W + 1:t + 1]
    ok = np.isfinite(y)
    A = np.column_stack([np.ones(ok.sum()), x[ok]])
    sw = np.sqrt(w[ok])
    coef, *_ = np.linalg.lstsq(A * sw[:, None], y[ok] * sw, rcond=None)
    assert np.isclose(b[t, n], coef[1])
    assert np.all(np.isnan(b[:W - 1]))
    assert np.nanmean(np.abs(b[-1] - true_b)) < 0.15


# ---- Specific risk -------------------------------------------------------- #

def test_specific_ts_var_iid():
    u = np.random.default_rng(9).normal(0, 0.015, 400)
    assert abs(np.sqrt(specific_ts_var(u, 84, 5, 252)) / 0.015 - 1) < 0.15


def test_specific_ts_var_nw_floor_on_bid_ask_bounce():
    e = np.random.default_rng(10).normal(0, 0.01, 2001)
    u = e[1:] - e[:-1]                                        # MA(1), rho_1 = -0.5: NW ratio ~ 1/6
    raw = newey_west_cov(u[:, None], 252, 5)[0, 0] / ewma_lag_cov(u[:, None], 252)[0, 0]
    v0 = ewma_lag_cov(u[:, None], 84)[0, 0]
    assert raw < 0.25                                         # unbounded NW would collapse the estimate
    assert np.isclose(specific_ts_var(u, 84, 5, 252), 0.25 * v0)


def test_coverage_gamma():
    rng = np.random.default_rng(11)
    assert coverage_gamma(rng.normal(0, 1, 252)) > 0.95
    assert coverage_gamma(rng.normal(0, 1, 50)) == 0.0
    x = rng.normal(0, 1, 252)
    x[:5] = 20.0                                              # a few huge outliers: sd ~ 3x robust sd
    assert coverage_gamma(x) < 0.5


def test_bayes_shrink_bounds_and_monotone_in_q():
    rng = np.random.default_rng(12)
    sig = np.abs(rng.normal(0.08, 0.03, 500))
    capw = np.exp(rng.normal(10, 1.2, 500))
    dec = np.repeat(np.arange(10), 50)
    mus = np.array([np.average(sig[dec == g], weights=capw[dec == g]) for g in dec])
    s1 = bayes_shrink(sig, capw, dec, q=0.1)
    s2 = bayes_shrink(sig, capw, dec, q=1.0)
    assert np.all(s1 >= np.minimum(sig, mus) - 1e-12) and np.all(s1 <= np.maximum(sig, mus) + 1e-12)
    assert np.all(np.abs(s2 - mus) <= np.abs(s1 - mus) + 1e-12)


# ---- Analytics ------------------------------------------------------------ #

def test_risk_decomposition_adds_up():
    rng, X, mcap, capw, *_ = _cross_section()
    K = X.shape[1]
    B = rng.standard_normal((K, K)) * 0.01
    F = B @ B.T + 1e-4 * np.eye(K)
    spec = (rng.uniform(0.04, 0.12, 500) / np.sqrt(12)) ** 2
    d = risk_decomposition(X, F, spec, capw)
    s = d["sigma"]
    assert np.isclose(d["factor_contrib_var"].sum() + d["specific_var"], s**2)
    assert np.isclose(capw @ d["mctr"], s)
    xsr = d["exposures"] * np.sqrt(np.diag(F)) * d["xsr_corr"]   # x * sigma * rho
    assert np.isclose(xsr.sum() + d["specific_var"] / s, s)


# ---- Validation statistics ------------------------------------------------ #

def test_bias_statistic_under_truth():
    rng = np.random.default_rng(13)
    vol = np.full(5000, 0.02)
    assert abs(bias_statistic(vol * rng.standard_normal(5000), vol) - 1) < 0.03


def test_mrad_normal_is_about_0_17():
    b = np.random.default_rng(14).standard_normal((240, 200))
    assert abs(mrad(b, 12) - 0.17) < 0.01


def test_qlike_minimized_at_true_scale():
    rng = np.random.default_rng(15)
    vol = np.full(20000, 0.02)
    r = vol * rng.standard_normal(20000)
    assert qlike(r, vol) < qlike(r, 0.8 * vol) and qlike(r, vol) < qlike(r, 1.25 * vol)
```

### A.3 `tests/optimize/test_optimize.py`

```python
"""Cross-checks for the Sec. 12 optimizer code (extracted verbatim from the blueprint)."""
import numpy as np
import pandas as pd
import cvxpy as cp

from eqrisk.optimize.factor_form import optimize_active
from eqrisk.optimize.riskfolio_adapter import exposure_bounds, to_riskfolio


def _model(seed=11, N=500, n_ind=20, n_sty=12):
    rng = np.random.default_rng(seed)
    K = 1 + n_ind + n_sty
    ind = rng.integers(0, n_ind, N)
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], rng.standard_normal((N, n_sty))])
    B = rng.standard_normal((K, K)) * 0.01
    F = B @ B.T + 1e-4 * np.eye(K)                               # monthly
    spec = (rng.uniform(0.04, 0.12, N) / np.sqrt(12)) ** 2         # monthly
    mcap = np.exp(rng.normal(10, 1.2, N))
    return rng, X, F, spec, mcap / mcap.sum(), n_ind, K


def test_factor_form_matches_dense_min_variance():
    _, X, F, spec, _, _, _ = _model()
    Sigma = X @ F @ X.T + np.diag(spec)
    w1 = cp.Variable(len(spec))
    cp.Problem(cp.Minimize(cp.quad_form(w1, cp.psd_wrap(Sigma))), [cp.sum(w1) == 1, w1 >= 0]).solve(solver=cp.CLARABEL)
    w2, status = optimize_active(X, F, spec, np.zeros(len(spec)), w_max=1.0)
    assert status == "optimal" and np.abs(w1.value - w2).max() < 1e-4


def test_active_optimization_respects_te_and_exposure_bands():
    rng, X, F, spec, w_b, n_ind, K = _model()
    alpha = rng.normal(0, 0.002, len(spec))
    sty, ind = np.arange(1 + n_ind, K), np.arange(1, 1 + n_ind)
    w, status = optimize_active(X, F, spec, w_b, alpha=alpha, te_max_ann=0.03,
                                style_idx=sty, ind_idx=ind)
    a = w - w_b
    te = np.sqrt((a @ X @ F @ X.T @ a + np.sum(a**2 * spec)) * 12)
    y = X.T @ a
    assert status == "optimal" and te <= 0.03 + 1e-6
    assert np.abs(y[sty]).max() <= 0.10 + 1e-6 and np.abs(y[ind]).max() <= 0.02 + 1e-6


def test_riskfolio_adapter_bounds_and_agreement_with_factor_form():
    rng, Xa, Fa, speca, _, n_ind, K = _model(seed=5, N=120, n_ind=8, n_sty=5)
    assets = [f"A{i:03d}" for i in range(120)]
    factors = ["COUNTRY"] + [f"IND_{i}" for i in range(n_ind)] + ["SIZE", "BETA", "MOM", "RESVOL", "BTOP"]
    X = pd.DataFrame(Xa, index=assets, columns=factors)
    F = pd.DataFrame(Fa, index=factors, columns=factors)
    spec = pd.Series(speca, index=assets)
    bounds = {"SIZE": (-0.1, 0.1), "BETA": (-0.1, 0.1), "MOM": (0.2, 0.5)}
    port = to_riskfolio(X, F, spec)
    port.ainequality, port.binequality = exposure_bounds(X, bounds)
    w_rp = port.optimization(model="Classic", rm="MV", obj="MinRisk", rf=0, l=0, hist=True)["weights"].values
    x = Xa.T @ w_rp
    for k, (lo, hi) in bounds.items():
        j = factors.index(k)
        assert lo - 1e-6 <= x[j] <= hi + 1e-6
    # same problem in native factor form
    d, U = np.linalg.eigh(Fa)
    L = U * np.sqrt(np.maximum(d, 0))
    w = cp.Variable(120)
    y = Xa.T @ w
    var = cp.sum_squares(L.T @ y) + cp.sum_squares(cp.multiply(np.sqrt(speca), w))
    cons = [cp.sum(w) == 1, w >= 0]
    for k, (lo, hi) in bounds.items():
        j = factors.index(k)
        cons += [y[j] >= lo, y[j] <= hi]
    cp.Problem(cp.Minimize(var), cons).solve(solver=cp.CLARABEL)
    Sigma = Xa @ Fa @ Xa.T + np.diag(speca)
    v_rp, v_ff = w_rp @ Sigma @ w_rp, w.value @ Sigma @ w.value
    assert abs(np.sqrt(v_rp) / np.sqrt(v_ff) - 1) < 1e-3     # same optimum risk
    assert np.abs(w_rp - w.value).max() < 1e-3
```

---

## Appendix B — Starter SIC → industry map

### B.1 Industries

Twenty starter industries sized for a ~500-name, √mcap-weighted regression. The parent column is the merge target when the thin-industry rule in §4.8 fires; merges resolve one level only, and a thin parent fails the run as a scheme-design error.

| Code | Covers | Parent |
|---|---|---|
| ENERGY | Oil and gas producers, refiners, services, coal, midstream (via overrides) | MATERIALS |
| MATERIALS | Chemicals, metals and mining, aggregates, paper and packaging | CAPITAL_GOODS |
| CAPITAL_GOODS | Machinery, electrical equipment, aerospace and defense, construction, distributors | TRANSPORTATION |
| TRANSPORTATION | Railroads, trucking, airlines, air freight, logistics | CAPITAL_GOODS |
| COMMERCIAL_SERVICES | Business and professional services, waste, staffing, consulting | CAPITAL_GOODS |
| CONSUMER_DURABLES_APPAREL | Autos and parts, homebuilders, appliances, apparel, footwear, leisure products | RETAIL |
| CONSUMER_SERVICES | Restaurants, hotels, casinos, cruise lines, online travel, education | RETAIL |
| RETAIL | Discretionary retail and e-commerce | CONSUMER_SERVICES |
| CONSUMER_STAPLES | Food, beverages, tobacco, household and personal products, staples retail | RETAIL |
| HEALTH_CARE_EQUIP_SERVICES | Medical devices, providers, managed care, distributors | PHARMA_BIOTECH |
| PHARMA_BIOTECH | Pharmaceuticals, biotechnology, life-science tools, CROs | HEALTH_CARE_EQUIP_SERVICES |
| BANKS | Commercial banks, savings institutions | FINANCIAL_SERVICES |
| FINANCIAL_SERVICES | Consumer finance, brokers, exchanges, asset managers, payments, data | BANKS |
| INSURANCE | Life, P&C, reinsurance, brokers | FINANCIAL_SERVICES |
| REAL_ESTATE | REITs, real estate services | UTILITIES |
| SOFTWARE_SERVICES | Software, IT services, data processing | TECH_HARDWARE |
| TECH_HARDWARE | Computers, storage, networking, components, test and measurement | SEMICONDUCTORS |
| SEMICONDUCTORS | Semiconductors and semiconductor equipment | TECH_HARDWARE |
| COMMUNICATION_SERVICES | Telecom, cable, media, entertainment, interactive media (via overrides) | SOFTWARE_SERVICES |
| UTILITIES | Electric, gas, water, multi-utilities, independent power | REAL_ESTATE |

### B.2 `configs/overrides/industry_map.csv`

Rows are evaluated top to bottom and the **first match wins**, so specific codes precede broad ranges. Lines starting with `#` are comments. An unmapped SIC (for example 6770 blank checks, 99xx non-classifiable) goes to the exception report, and the run fails until an override is added.

```text
sic_lo,sic_hi,industry,note
# ---- specific codes first ----
1531,1531,CONSUMER_DURABLES_APPAREL,homebuilders (operative builders)
2830,2836,PHARMA_BIOTECH,drugs; biologicals; in-vitro diagnostics
2840,2844,CONSUMER_STAPLES,soaps; detergents; cosmetics
3021,3021,CONSUMER_DURABLES_APPAREL,footwear
3410,3412,MATERIALS,metal cans and containers
3533,3533,ENERGY,oil and gas field machinery
3570,3579,TECH_HARDWARE,computers; storage; networking gear
3630,3639,CONSUMER_DURABLES_APPAREL,household appliances
3660,3669,TECH_HARDWARE,communications equipment
3674,3674,SEMICONDUCTORS,semiconductors and related devices
3670,3679,TECH_HARDWARE,electronic components other than semiconductors
3711,3716,CONSUMER_DURABLES_APPAREL,motor vehicles; bodies; parts
3751,3751,CONSUMER_DURABLES_APPAREL,motorcycles and bicycles
3812,3812,CAPITAL_GOODS,defense electronics; navigation
3825,3825,TECH_HARDWARE,electronic test and measurement
3826,3826,PHARMA_BIOTECH,laboratory analytical instruments (life-science tools)
3840,3851,HEALTH_CARE_EQUIP_SERVICES,medical devices and instruments; ophthalmic goods
3940,3949,CONSUMER_DURABLES_APPAREL,toys and sporting goods
4400,4499,TRANSPORTATION,water transport (cruise lines via overrides)
4600,4699,ENERGY,pipelines other than natural gas
4700,4729,CONSUMER_SERVICES,travel arrangement (online travel)
4950,4959,COMMERCIAL_SERVICES,sanitary and waste services
5045,5045,TECH_HARDWARE,computer equipment wholesale
5047,5047,HEALTH_CARE_EQUIP_SERVICES,medical equipment wholesale
5065,5065,TECH_HARDWARE,electronic parts wholesale
5122,5122,HEALTH_CARE_EQUIP_SERVICES,drug wholesale
5171,5172,ENERGY,petroleum products wholesale
5331,5331,CONSUMER_STAPLES,variety and warehouse stores
5400,5499,CONSUMER_STAPLES,food stores
5812,5813,CONSUMER_SERVICES,restaurants
6324,6324,HEALTH_CARE_EQUIP_SERVICES,managed care
6798,6798,REAL_ESTATE,REITs
7310,7319,COMMUNICATION_SERVICES,advertising agencies
7370,7379,SOFTWARE_SERVICES,software; IT services; data processing
8731,8731,PHARMA_BIOTECH,commercial biological research (CROs)
# ---- broad ranges ----
0100,0999,CONSUMER_STAPLES,agriculture
1000,1099,MATERIALS,metal mining
1200,1299,ENERGY,coal
1300,1399,ENERGY,oil and gas extraction and services
1400,1499,MATERIALS,aggregates and nonmetallic minerals
1500,1799,CAPITAL_GOODS,construction and engineering
2000,2199,CONSUMER_STAPLES,food; beverages; tobacco
2200,2399,CONSUMER_DURABLES_APPAREL,textiles and apparel
2400,2499,MATERIALS,lumber and wood
2500,2599,CONSUMER_DURABLES_APPAREL,furniture
2600,2699,MATERIALS,paper and packaging
2700,2799,COMMUNICATION_SERVICES,publishing
2800,2899,MATERIALS,chemicals
2900,2999,ENERGY,petroleum refining
3000,3099,MATERIALS,rubber and plastics products
3100,3199,CONSUMER_DURABLES_APPAREL,leather goods
3200,3399,MATERIALS,glass; cement; primary metals
3400,3569,CAPITAL_GOODS,fabricated metal; industrial machinery
3580,3629,CAPITAL_GOODS,HVAC; service machinery; electrical equipment
3640,3659,CAPITAL_GOODS,lighting and wiring equipment
3680,3699,CAPITAL_GOODS,other electrical equipment
3700,3799,CAPITAL_GOODS,aerospace; defense; rail and other transport equipment
3800,3899,CAPITAL_GOODS,instruments and controls
3900,3999,CONSUMER_DURABLES_APPAREL,other manufacturing
4000,4399,TRANSPORTATION,rail; transit; trucking; courier
4500,4599,TRANSPORTATION,airlines; air freight
4730,4799,TRANSPORTATION,freight forwarding and logistics
4800,4899,COMMUNICATION_SERVICES,telecom; cable; broadcasting
4900,4999,UTILITIES,electric; gas; water; multi-utilities
5000,5099,CAPITAL_GOODS,durable goods distributors
5100,5199,CONSUMER_STAPLES,nondurable goods distributors
5200,5999,RETAIL,retail trade including e-commerce
6000,6099,BANKS,banks and savings institutions
6100,6299,FINANCIAL_SERVICES,consumer finance; brokers; exchanges; asset managers
6300,6499,INSURANCE,insurance carriers and brokers
6500,6599,REAL_ESTATE,real estate operators and services
6700,6799,FINANCIAL_SERVICES,holding and investment offices
7000,7099,CONSUMER_SERVICES,hotels and casinos
7200,7299,CONSUMER_SERVICES,personal services
7300,7399,COMMERCIAL_SERVICES,business services
7800,7899,COMMUNICATION_SERVICES,film; streaming; entertainment
7900,7999,CONSUMER_SERVICES,amusement and recreation
8000,8099,HEALTH_CARE_EQUIP_SERVICES,health services
8200,8299,CONSUMER_SERVICES,education
8700,8799,COMMERCIAL_SERVICES,engineering; consulting; research
```

### B.3 `configs/overrides/industry_overrides.csv` (starter examples)

Overrides are keyed by a ticker and the date on which that ticker identifies the issuer, and are resolved to `issuer_id` at load, so later renames do not break them. These are starter examples written from general knowledge, not from pulled SIC data: confirm each issuer's SIC in your security master first **[VERIFY]**. An override that does not change the SIC mapping is harmless, and the exception report lists such no-op rows.

```text
ticker,as_of,industry,reason
GOOGL,2026-09-01,COMMUNICATION_SERVICES,interactive media (SIC 7370)
META,2026-09-01,COMMUNICATION_SERVICES,interactive media (SIC 7370)
DIS,2026-09-01,COMMUNICATION_SERVICES,media and entertainment
V,2026-09-01,FINANCIAL_SERVICES,payments
MA,2026-09-01,FINANCIAL_SERVICES,payments
PYPL,2026-09-01,FINANCIAL_SERVICES,payments
FI,2026-09-01,FINANCIAL_SERVICES,payments and processing
FIS,2026-09-01,FINANCIAL_SERVICES,payments and processing
GPN,2026-09-01,FINANCIAL_SERVICES,payments
SPGI,2026-09-01,FINANCIAL_SERVICES,ratings and indices
MCO,2026-09-01,FINANCIAL_SERVICES,ratings
MSCI,2026-09-01,FINANCIAL_SERVICES,indices and analytics
KMI,2026-09-01,ENERGY,midstream (natural gas transmission SIC)
WMB,2026-09-01,ENERGY,midstream
OKE,2026-09-01,ENERGY,midstream
TRGP,2026-09-01,ENERGY,midstream
CCL,2026-09-01,CONSUMER_SERVICES,cruise lines
RCL,2026-09-01,CONSUMER_SERVICES,cruise lines
NCLH,2026-09-01,CONSUMER_SERVICES,cruise lines
LRCX,2026-09-01,SEMICONDUCTORS,semiconductor equipment
KLAC,2026-09-01,SEMICONDUCTORS,semiconductor equipment
TER,2026-09-01,SEMICONDUCTORS,semiconductor test
IBM,2026-09-01,SOFTWARE_SERVICES,IT services and software
ACN,2026-09-01,SOFTWARE_SERVICES,IT services
DHR,2026-09-01,PHARMA_BIOTECH,life-science tools
CVS,2026-09-01,HEALTH_CARE_EQUIP_SERVICES,pharmacy benefits and managed care
PCAR,2026-09-01,CAPITAL_GOODS,heavy trucks
GLW,2026-09-01,TECH_HARDWARE,glass and optical components
ECL,2026-09-01,MATERIALS,specialty chemicals
ADP,2026-09-01,COMMERCIAL_SERVICES,payroll and HR services
PAYX,2026-09-01,COMMERCIAL_SERVICES,payroll and HR services
CTAS,2026-09-01,COMMERCIAL_SERVICES,uniform and facility services
```

---

## Appendix C — Fundamentals concept map

### C.1 Concepts, fields, and fallback chains

Sharadar field names below are from the SF1 table as commonly documented **[VERIFY]** against your subscription's data dictionary. EDGAR tags are US-GAAP taxonomy concepts from `companyfacts`; try each in order and record which one supplied the value.

| Concept | Used by | Sharadar SF1 | EDGAR fallback chain (first available wins) | Notes |
|---|---|---|---|---|
| Net income to common, TTM | ETOP | `netinccmn` (ART) | `NetIncomeLossAvailableToCommonStockholdersBasic` → `NetIncomeLoss` → `ProfitLoss` − `NetIncomeLossAttributableToNoncontrollingInterest` | TTM from four discrete quarters |
| Operating cash flow, TTM | CETOP | `ncfo` (ART) | `NetCashProvidedByUsedInOperatingActivities` → `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | 10-Q values are year-to-date: difference them |
| Depreciation and amortization, TTM | CETOP alternative | `depamor` (ART) | `DepreciationDepletionAndAmortization` → `DepreciationAndAmortization` → `DepreciationAmortizationAndAccretionNet` | Only if you switch cash earnings to NI + D&A |
| Dividends per share, TTM | DTOP | `dps` (ART) | `CommonStockDividendsPerShareDeclared` → `CommonStockDividendsPerShareCashPaid` | **Primary source is the corporate-actions feed**: regular cash dividends with ex-date in the last 252 sessions **[OURS]**; fundamentals are the fallback |
| Book equity (common) | BTOP, BLEV | `equity` (ARQ) | `StockholdersEquity` → `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` − `MinorityInterest` | BE ≤ 0 → BTOP computed, BLEV missing |
| Preferred equity | MLEV, BLEV | (no direct field **[VERIFY]**) | `PreferredStockValue` → 0 | Missing → 0, flagged |
| Long-term debt | MLEV, BLEV | `debtnc` (ARQ) | `LongTermDebtNoncurrent` → `LongTermDebtAndCapitalLeaseObligations` → `LongTermDebt` − `LongTermDebtCurrent` | Exclude operating-lease liabilities |
| Total debt | DTOA | `debt` (ARQ) | long-term debt + (`DebtCurrent` → `LongTermDebtCurrent` + `ShortTermBorrowings` + `CommercialPaper`) | |
| Total assets | DTOA | `assets` (ARQ) | `Assets` | |
| Revenue, annual | SGRO | `revenue` (ARY) | `Revenues` → `RevenueFromContractWithCustomerExcludingAssessedTax` → `RevenueFromContractWithCustomerIncludingAssessedTax` → `SalesRevenueNet`; banks: `InterestAndDividendIncomeOperating` + `NoninterestIncome` | Per share with weighted shares, split-adjusted |
| EPS, annual | EGRO | `eps` (ARY) | `EarningsPerShareBasic` → `EarningsPerShareBasicAndDiluted` → `EarningsPerShareDiluted` | Split-adjust with the corporate-actions feed |
| Shares outstanding | SIZE, LIQUIDITY | `sharesbas` (ARQ) | `dei:EntityCommonStockSharesOutstanding` (cover page, per class, dated) → `CommonStockSharesOutstanding` → `WeightedAverageNumberOfSharesOutstandingBasic` | Split-adjust from its as-of date to *t* (§4.4) |
| Weighted average shares | SGRO per-share | `shareswa` (ARY) | `WeightedAverageNumberOfSharesOutstandingBasic` | |
| SIC | Industry | `TICKERS.siccode` | `submissions.sic` | Current attribute only; no history |

### C.2 Sharadar rules

Use only the as-reported dimensions ARQ (quarterly), ARY (annual), and ART (trailing twelve months); the MR* dimensions hold the most recent restated values and leak later information into history. `datekey` is the SEC filing date and drives `available_date`; `reportperiod` is the fiscal period end. Pull incrementally by `lastupdated` and keep every vintage you receive.

### C.3 EDGAR rules

Endpoints: `https://data.sec.gov/submissions/CIK##########.json` for filings, form types, SIC, and fiscal year end; `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` for all facts of one company (CIK zero-padded to 10 digits). For backfill, the nightly bulk `companyfacts.zip` avoids tens of thousands of requests **[VERIFY]** its current location on the SEC's API page. Send a `User-Agent` with your name and email and stay at or below 10 requests per second.

Each fact carries `val`, `start` (durations only), `end`, `fy`, `fp`, `form`, `filed`, and `accn`. Classify durations by `end − start`: about 90 days is a quarter, about 180 or 270 days is year-to-date, about 365 days is annual. The `fy` and `fp` fields describe the filing that reported the fact, and comparative prior-period values appear again in later filings, so key facts by `(concept, start, end)` and keep every `(filed, accn)` as a vintage. Fiscal years differ across companies, so build TTM windows from period end dates, never from calendar quarters. Derive Q4 flows as the annual value minus Q1–Q3. Ignore company-specific extension namespaces in v1 and log how often each fallback fires, per concept and per industry.

---

## Appendix D — References

**Methodology**

1. Menchero, J., Orr, D. J., Wang, J. (2011). *The Barra US Equity Model (USE4): Methodology Notes*. MSCI, August 2011. The source for §5–§11 **[USE4]** citations.
2. MSCI (2011). *The Barra US Equity Model (USE4): Empirical Notes*. Descriptor definitions and weights, as quoted in use4-learning-lab.
3. Menchero, J., Wang, J., Orr, D. J. (2012). Improving Risk Forecasts for Optimized Portfolios. *Financial Analysts Journal* 68(3), 40–50. Eigenfactor risk adjustment.
4. Newey, W. K., West, K. D. (1987). A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix. *Econometrica* 55(3), 703–708.
5. Menchero, J., Davis, B. (2011). Risk Contribution Is Exposure Times Volatility Times Correlation: Decomposing Risk Using the X-Sigma-Rho Formula. *Journal of Portfolio Management* 37(2), 97–106.
6. Duan, N. (1983). Smearing Estimate: A Nonparametric Retransformation Method. *Journal of the American Statistical Association* 78(383), 605–610. Basis for the E0 factor in §9.2.
7. Patton, A. J. (2011). Volatility Forecast Comparison Using Imperfect Volatility Proxies. *Journal of Econometrics* 160(1), 246–256. Robustness of the QLIKE loss.
8. Grinold, R. C., Kahn, R. N. (2000). *Active Portfolio Management*, 2nd ed. McGraw-Hill.
9. Diamond, S., Boyd, S. (2016). CVXPY: A Python-Embedded Modeling Language for Convex Optimization. *Journal of Machine Learning Research* 17(83), 1–5.

**Code and data**

10. `jeves1202/use4-learning-lab` — https://github.com/jeves1202/use4-learning-lab (CC BY-NC 4.0)
11. `UePG-21/Barra-risk-model` — https://github.com/UePG-21/Barra-risk-model (no license: all rights reserved)
12. `dcajasn/Riskfolio-Lib` — https://github.com/dcajasn/Riskfolio-Lib (BSD-3); docs: https://riskfolio-lib.readthedocs.io
13. `fja05680/sp500` — https://github.com/fja05680/sp500 (MIT)
14. Databento documentation — https://databento.com/docs
15. FRED API — https://fred.stlouisfed.org/docs/api/fred/
16. SEC EDGAR APIs — https://www.sec.gov/search-filings/edgar-application-programming-interfaces
17. Kenneth R. French Data Library — https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
18. `exchange_calendars` — https://github.com/gerrymanoim/exchange_calendars
19. DuckDB-WASM — https://github.com/duckdb/duckdb-wasm · stlite — https://github.com/whitphx/stlite
