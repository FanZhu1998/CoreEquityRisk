# EQRisk decision log

Decisions that deviate from, or fill gaps in, `docs/BLUEPRINT.md` (rule 10). Newest last.
Each entry: context, decision, and when to revisit.

---

## D-001 · Data tier for the keys on this machine (2026-09-11)

**Context.** The blueprint's recommended tier is Databento prices + Sharadar fundamentals and
corporate actions (§4.1, D3). This machine has keys for Databento, FRED and EODHD, and no
Sharadar key. A probe on 2026-09-10 (the earlier `equity-risk-model` prototype) found that on
this EODHD subscription only `/api/eod` and `/api/exchange-symbol-list` return 200;
`/api/fundamentals`, `/api/historical-market-cap`, `/api/div` and `/api/splits` return 403. It
also recorded that Databento `EQUS.SUMMARY` history begins 2024-07-01 and that Databento is
metered.

**Decision.**

| Need | Source used | Blueprint slot |
|---|---|---|
| Daily prices and volume | EODHD `/api/eod` (unadjusted OHLCV plus `adjusted_close`) | alternative "EOD vendor" |
| Splits and dividends | **Implied** from EODHD's unadjusted close vs `adjusted_close` (`corp_actions: eodhd_implied`) | fills the corporate-actions gap |
| Shares outstanding, SIC, fundamentals | SEC EDGAR `companyfacts` + `submissions` | Tier 0 / Tier 2 fallback |
| Risk-free | FRED `DTB3` | as specified |
| Membership | `fja05680/sp500` | as specified |
| Validation factors | Ken French daily factors | as specified |

Databento `EQUS.SUMMARY` and Sharadar adapters are still implemented behind the same interface
(`sources.*` in the model YAML), so a key or entitlement change is a config change. Every
Databento request is priced with `metadata.get_cost` first and refused above
`sources.yaml: databento.max_cost_usd`.

**How implied corporate actions work.** On a session with no action, the vendor's adjusted
return equals the price return. A split shows up as a ratio `k = R_adj / R_price` far from 1,
snapped to the nearest small rational (`qa.corp_actions`). A cash dividend shows up as a small
positive gap between the adjusted and price returns, from which the per-share amount follows.
Every daily file stores the previous row from the *same* vendor response, so a return never
mixes two adjusted-close vintages (the vendor back-adjusts history on each new dividend).

**Revisit when** Sharadar or an EODHD fundamentals/corporate-actions entitlement is added, or
`eqrisk doctor` reports a different entitlement.

## D-002 · `mypy --strict` and the verbatim kernels (2026-09-11)

**Context.** Phase 1 requires Appendix A.1 saved verbatim *and* `mypy --strict eqrisk/kernels`
clean. With numpy 2.5.3's stubs, seven return statements in A.1 fail `warn_return_any`, because
the stubs type `@` and `/` results as `Any`. The logic is correct: all 24 known-answer tests
pass.

**Decision.** Keep `reference.py` byte-identical to Appendix A.1 (enforced by
`tests/kernels/test_verbatim.py`) and relax only `warn_return_any`, only for that module, in
`pyproject.toml`. New kernel modules get the full `--strict` treatment.

## D-003 · Configuration completeness (2026-09-11)

**Decision.** The pydantic models carry no numeric defaults: every value must appear in YAML,
and unknown keys fail validation. Presets live in `configs/presets.yaml`, not in code. Additions
to the §16 YAML, all needed to keep rule 4 (no magic numbers):

- `history`: default backfill window (the §17 development slice).
- `sources`: adds `eodhd` / `eodhd_implied` as allowed values (D-001).
- `specific_risk.shrinkage.n_groups: 10` (the "10 market-cap deciles" of §9.4).
- `gates`: the remaining §11.4 and §6.4 thresholds (price coverage, moment tolerances, condition
  number, n, R² bounds, factor-shock z, stability, VIF).
- `qa`: price-QA thresholds from §4.3–§4.4 and implied-corporate-action tolerances (D-001).
- `industries.scheme_version: sic20_v1`: §16 says `sic19_v1`, but Appendix B defines 20
  industries.

PyYAML implements YAML 1.1, which reads `1.0e6` as a string. Exponents in the YAML carry an
explicit sign (`1.0e+6`), and pydantic coerces numeric strings anyway.

## D-004 · Eigen simulation seed (2026-09-11)

**Context.** §8.2 seeds with `np.random.default_rng(hash((model_id, date)))`. Python's `hash()`
of strings is salted per process (`PYTHONHASHSEED`), so that seed differs between runs and
breaks the bit-identical rerun requirement.

**Decision.** Seed = the first 8 bytes of `sha256(f"{model_id}|{date.isoformat()}")` as an
unsigned integer.

## D-005 · Raw storage layout (2026-09-11)

**Decision.** Raw partitions hold immutable vintage files, `v000.parquet`, `v001.parquet`, …
A write whose bytes equal the latest vintage is a no-op, which gives byte-identical re-ingests;
a revision creates the next vintage, and staging reads the latest. Price-like sources partition
by `date=YYYY-MM-DD`. EDGAR partitions by `cik=##########` (the natural key), with the pull date
recorded in a column.

## D-007 · Implied corporate actions and price QA (2026-09-11)

**Context.** The EODHD dividends and splits endpoints return 403 (D-001), so actions are
inferred. On 2023 AAPL and MSFT ex-dates, the multiplicative inversion `D = P_{t-1} - P_t / R`
recovers the declared dividends to the cent ($0.23/$0.24; $0.68/$0.75). Rows with no event show
|D/P| < 1e-6.

**Decision.**
- `g = R/p` more than 20% away from 1 is a split only if it is within 0.5% of a ratio a:b whose
  smaller term is ≤ 4 (3:2, 5:4, 20:1, 1:10). A 3% tolerance would read a 25% spin-off
  (g ≈ 1.2375) as a 5:4 split. A denominator bound of 20 would read it as 21:17. Any other large `g > 1` is a **distribution** (spin-off or special dividend): it counts in
  total return and is excluded from dividend yield.
- Special dividends: distributions, and dividends above 3x the median of the security's prior
  252-session dividends.
- A return whose vendor "previous row" is not the previous session (a skipped day) is set
  **missing** rather than kept as a multi-session return, as are quarantined jumps (> 50% with no
  action, or with an action the prices cannot explain) and non-positive prices. Stale runs (≥ 5 identical closes) keep their return but are
  flagged, so the name leaves the ESTU on those days.
- Some delisted `XXX_old` series carry `adjusted_close == close` throughout (EODHD never
  adjusted them). Their returns are price-only; the dividend-yield descriptor falls back to EDGAR
  dividends per share for them.

## D-008 · Identity resolution, share classes, issuer market cap (2026-09-11)

**Context.** The "(Updated)" fja05680 file writes reused tickers without a suffix, EODHD moves a
renamed company's history to its new ticker (FB.US now holds an unrelated fund), and SEC's ticker
map only knows current holders. EODHD's symbol-change endpoint returns 403.

**Decision.** Resolution works from raw data only (see `eqrisk/staging/security_master.py`):
membership eras → vendor code by price coverage (≥ 90% of the era's sessions) → rename links to
the ticker that entered the index on the next session → CIK via override, the current
constituent list, SEC's ticker map (only when the SEC title matches the vendor name, which
defeats reused tickers), or an exact company-name key against SEC's 1.06M-name list,
disambiguated by filing history. Unresolved cases go to the `exceptions` table and are fixed in
`configs/overrides/ticker_map.csv`.

Share classes: EDGAR gives issuer-level share counts only (class counts are dimensional and
absent from `companyfacts`), so class market caps are unknown. The primary class is the one
with the higher median dollar volume over the last 63 shared sessions. Issuer market cap =
issuer shares × primary-class close; every class carries it (SIZE and the price ratios are
issuer-level per §4.2). Issuers with no usable count (Berkshire's cover page is dimensional only)
get missing market cap, drop out of the ESTU with reason `missing_mcap`, and can be fixed in
`configs/overrides/shares_overrides.csv`.

## D-009 · Fundamentals: trailing twelve months and vintages (2026-09-11)

**Decision.** TTM at a period end is FY(prior year) + YTD(current) − YTD(prior-year comparative),
all from 10-K/10-Q duration facts, instead of summing four derived quarters. It needs no Q4
derivation and uses the comparatives every 10-Q carries. Each component uses its latest vintage
known at the time, so a 10-K/A restatement changes TTM only from the restatement's filing date
forward. Items whose EDGAR chain needs a subtraction in Appendix C (book equity less minority
interest, long-term debt less current portion) use the chain's earlier concepts only. The small
bias is accepted for v1.

## D-010 · Staging is a pure function of raw data (2026-09-11)

**Decision.** `eqrisk backfill --stage stage` rebuilds every staged table from the raw store on
each run: the security master, returns, market cap, fundamentals, industries and universes. Reruns
are bit-identical, and an override fix flows everywhere at once. Only the per-CIK fundamentals
computation is cached, keyed by raw vintage and concept-map hash. The daily pipeline re-stages
fully (seconds to minutes) and runs the model incrementally.

## D-011 · Share counts, split confirmation, vendor-name quirks (2026-09-11)

**Context.** The first real staging run produced market caps of $2.4×10¹⁶ (EIX), $3.3×10¹⁶
(YUM) and $2.0×10¹⁶ (AJG). Their cover-page `dei:EntityCommonStockSharesOutstanding` facts carry
XBRL scale errors (EIX: 362,570,075,000,000 shares; PKG ×10³). One name then held 60–70% of
the √cap regression weight, and five industries tripped the thin-industry rule. DDOG tagged a
class with 0 shares. STZ, V, ERIE, and BRK after 2011 have no non-dimensional count at all.
Prices alone cannot separate a 5:4 split (Brown-Forman 2018) from a 20% spin-off (Alliance Data
2021).

**Decision.**
- Share counts are kept **per concept**. On each date the first fresh concept wins, in the order
  override → cover page → balance sheet → weighted-average basic → weighted-average diluted, so
  definitions do not alternate. Zero counts are dropped.
- A count more than 3× from the median of the issuer's other counts within 400 days, compared
  on a split-normalized basis so genuine splits pass, is dropped as a scale error and listed in
  `exceptions` (`share_count_outlier`).
- Last resort: `dei:EntityPublicFloat` divided by the primary class's close on the float date,
  flagged `shares_source = public_float`. It understates insider-heavy issuers, which is
  accepted because the alternative is dropping the issuer.
- Split ratios with both terms > 1 must lie within (1/3, 3); Aimco's 2020 spin-off (g = 9.29)
  would otherwise snap to 28:3. Every split is confirmed against the issuer's share counts
  before and after (within 200 days). If the count moved closer to 1× than to k×, the event is
  re-run as a distribution, or as unexplained for a ratio below 1 (`split_reclassified`). This
  also catches vendor unit errors. EODHD quoted Rockwell Collins' close at a tenth of its value
  until 2016-06-01, which reads as a 1:10 reverse split.
- EODHD keeps an old company's history under a reused code and names it after today's holder
  (XL, NFX). For an era that has ended, a superseded code (`XXX_old`) is therefore preferred.
  `MNK_old`'s name is simply wrong, and 21st Century Fox lives under `TFCFA/TFCF`; both are in
  `ticker_map.csv`. `eqrisk pull-overrides` fetches what the override files name.
- Holding-company reorganizations (BlackRock 2024, Disney 2019, Cigna 2018, Xerox 2019,
  WestRock 2018, ExxonMobil 2026) are linked to their predecessor registrant by name
  (`issuer_ciks`); Apache→APA, Bunge, and Mylan→Viatris are linked manually in `cik_links.csv`.
  Fundamentals are relabelled to the issuer, so the as-of lookup continues across the change.

## D-012 · Descriptor and exposure details (2026-09-11)

**Decision.**
- RSTR is the EWMA-weighted *mean* of daily log excess returns over sessions t−525…t−22
  (lag L + 1 = 22 rows, per the §6.2 sum over d = L+1…L+T), with weights renormalized over
  available days. For complete histories this ranks names exactly as the sum does; it also
  keeps names with gaps comparable.
- DASTD uses a 63-observation minimum. CMRA needs 189 of 252 sessions and floors Z at −0.99.
  A turnover block counts with half its days traded. These additions to §16 are marked [OURS].
- DTOP comes from implied regular dividends over the last 252 sessions, on today's share
  basis. For series the vendor never adjusts (some `_old` codes, D-007) it comes from EDGAR
  dividends per share, and is 0 when neither shows a dividend.
- Per-share growth inputs (EPS; revenue ÷ weighted shares) are put on one split basis using
  each value's filing date. Splits before the 2016 price history are unknown, so five-year
  windows that reach back before 2016 can mis-scale an early year for names that split in
  2011–2015. The effect fades out of the model window by 2019–2020; an earlier price start
  would remove it.
- Turnover divides class volume by issuer shares, so multi-class issuers show lower turnover.
- §6.3 step 8 re-imposes orthogonality (RESVOL, NLBETA ⟂ BETA; NLSIZE ⟂ SIZE) after imputation,
  then standardizes. Standardization is affine, so the §17 requirement |ρ| < 1e-8 holds exactly.
- The `exposures` and `descriptors` tables are stored **wide**, one column per style or
  descriptor, instead of the long (date, sid, factor) layout in §14. The regression reads a
  dense X per date. DuckDB `UNPIVOT` gives the long view.

## D-013 · The regression sample (2026-09-11)

**Decision.** The regression for the return ending on day t uses the ESTU as of the close of
t−1, restricted to names with an unflagged return on t. The industry constraint weights w_i and
the √cap regression weights are computed over that same sample. The constraint then makes the
country factor exactly the cap-weighted mean of the sample's industries, and an ESTU name
without a return on t cannot pull the identification toward itself. A name whose return on t is
flagged (stale, jump, gap) is in neither the sample nor the specific returns. Coverage names
outside the ESTU get out-of-sample specific returns.

## D-014 · MOMENTUM sits at two §17 guideline edges (2026-09-11)

**Observation.** Over the 2018–2021 slice, MOMENTUM's median monthly stability coefficient is
0.898 (by year: 0.915, 0.885, 0.921, 0.889). Its non-imputed coverage bottoms out at 98.8% on 5
of 1,008 sessions (April 2020). The imputed names on those days are the new Dow Inc., CARR, OTIS,
CTVA, FOX and FOXA, securities that had just come into existence and lack the 252 sessions
`momentum.min_obs` requires. All other styles meet §17 (price styles ≥ 99% coverage and ≥ 0.90
stability; fundamental styles ≥ 95% and ≥ 0.95).

**Why it is not a bug.** RSTR's §16 definition (504-session window, 126-session half-life, 21+1
lag) replaces about 11% of the EWMA weight each month, which puts the month-over-month
correlation near 0.9 by construction. The §11.4 daily gate for stability is 0.80, which MOMENTUM
clears on every date.

**Decision.** Keep the §16 parameters. The Phase 4 golden test holds MOMENTUM to 0.98 coverage
and 0.88 median stability, and every other style to the §17 thresholds. Revisit if the bias
battery (Phase 9) shows the momentum factor's risk misforecast.

**Where the 0.80 WARN gate trips (2018–2026, after D-016/D-017).**

| Style | Days < 0.80 | Months | Worst | Pattern |
|---|---|---|---|---|
| NONLINEAR_BETA | 246 | 26 | 0.04 (2020-03) | whenever betas reorder; worst in the COVID crash |
| GROWTH | 154 | 16 | 0.61 | late February–March: 10-K season resets five-year growth for most names at once |
| MOMENTUM | 130 | 21 | 0.60 (2018-12) | return reversals: December 2018, April 2020 |
| BETA | 47 | 4 | 0.51 | February 2018 and March 2020 |
| EARNINGS_YIELD | 29 | 5 | 0.74 | the 2020 earnings collapse and the 2021 rebound |
| RESIDUAL_VOLATILITY | 22 | 2 | 0.58 | March–April 2020 only |

Between 2020-02-11 and 03-12, cruise lines, energy and insurers (CCL, RCL, NCLH, HAL, HP, PXD,
LNC) jumped from betas of 0.3–1.9 to the 3σ trim bound. Over the same weeks, the previous month's
high-beta semiconductors fell back toward 1, and BETA kept only 0.61 month-over-month
correlation. NONLINEAR_BETA is a cube of BETA orthogonalized to BETA, so it is driven by that tail
and reshuffled completely.

A NONLINEAR_SIZE dip in November 2019 (worst 0.43) was not the market. It came from
ConocoPhillips' share counts filed in thousands, and it disappeared with D-016. The remaining trips
are genuine re-rankings, and the gate flags them as intended.

## D-015 · Specific-risk details (2026-09-11)

**Decision.**
- "Last 252 valid specific returns" (§9.1) are searched within at most 504 sessions
  (`specific_risk.ts.lookback_days`). γ's h counts valid returns in the last 252 sessions (§9.3).
- The first forecast is made once 252 sessions of specific returns exist (§4.9), so the
  structural model has names with γ ≥ 0.99 to fit.
- In the structural model, a coverage name whose industry has no γ ≥ 0.99 fit names that day
  takes the fit-weighted average industry level.
- Coverage names without a market cap (under 0.5%) skip shrinkage, since they have no size
  decile or cap weight, and keep their blended σ.

## D-006 · License (2026-09-11)

**Context.** §2.4 and §0 describe a proprietary codebase in a private repository.

**Decision (owner).** The repository is private on GitHub and keeps its MIT `LICENSE`. The
reference-repository cautions in §2 still apply regardless of our license: nothing from
`use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model` (no license) is copied.

## D-016 · A share count must imply a plausible turnover (2026-09-11)

**Context.** After D-011, ConocoPhillips' SIZE exposure was −6.8σ on 2019-10-23 and FLIR's
−8.7σ throughout. Both tag their weighted-average share counts in thousands: COP reports
1.13×10⁶ against a cover-page count of 1.10×10⁹. McDonald's weighted averages are in millions.
Four such facts per quarter outnumber the one correct cover count, so D-011's neighbour median
settles on the wrong level. Whenever the cover page is stale, the mis-scaled count wins.

**Decision.** Before the neighbour rule, each count is checked against the primary class's
median daily volume over the 63 sessions before its basis date. A count whose implied turnover
is outside 0.01%–50% a day (`security_master.share_turnover_band`) is dropped and listed as
`share_count_outlier` with reason `turnover`. Genuine counts fall orders of magnitude inside
that band; factor-of-10³ errors fall orders of magnitude outside it. Counts dated before the
price history are used only in its first months, so they are judged on its first sessions. That
catches COP's pre-2016 weighted averages, Berkshire's class-A-equivalent counts, and the 100
shares of BHGE's pre-merger shell. The public-float fallback must pass the same test: ARES's 2025
float would otherwise give it 1% of its real share count. A public float of zero (FTI) no longer
yields a count.

## D-017 · Vendor volume is split-adjusted to the pull date (2026-09-11)

**Context.** EODHD split-adjusts `volume` to the day it is pulled, as it does `adjusted_close`;
`close` stays unadjusted. In the backfill, AAPL's 2020-08-28 volume is 1.88×10⁸, four times the
4.7×10⁷ shares traded before its 4:1 split. NVDA's pre-2021 volume is ×40 (4:1 in 2021, 10:1 in
2024) and CMG's pre-2024 volume is ×50. Turnover (STOM/STOQ/STOA) divides volume by the day's
share count, so every name that later split had its liquidity overstated by the product of
those splits. That is an error and also a look-ahead.

The vendor does not treat every split alike. Comparing median volume over the 40 sessions on
either side of each of the 128 splits detected in 2016–2026 shows that EODHD adjusted volume for
94 of 103 forward splits and 19 of 25 reverse splits (GE's 1:8 in 2021 included). It left CHK's
1:200 (2020), Frontier's 1:15 (2017) and Pepco's 1:18 on the traded basis; most other exceptions
are vendor artefacts.

**Decision.** Staging puts volume back on the day's share basis without knowing when a row was
pulled (`corp_actions.vendor_split_basis`).
- A row's `adjusted_close / close` is the vendor's adjustment factor from its date to its pull
  date. Matching it against the running product of implied actions (D-007) shows which later
  splits the vendor had applied when the row was pulled.
- Each split is tested for whether the vendor also adjusted volume. If volume after the ex-date
  sits nearer k× the volume before it than 1×, by more than `qa.corp_actions.split_volume_margin`
  (0.3 in log), the split is left out. Small ratios, where volume noise cannot tell, count as
  adjusted, which is the vendor's usual practice.
- Volume is divided by the product of the remaining splits. Rows pulled on their own day, as in
  the daily pipeline, need no correction.
- Rows whose factor lies more than `qa.corp_actions.volume_basis_tol` (log units) from every split
  basis are listed as `volume_basis_unmatched`. In the 2026-09 backfill that is only AVB, whose
  vendor factor sits a constant 1.03 (log) from its own dividend history (no splits; volume is
  unaffected).

Staged `prices.volume` is shares traded that day. The share-class dollar-volume comparison
(D-008) still uses vendor volume, since both classes of an issuer carry the same later splits.

## D-018 · Validation backtest choices (2026-09-11)

**Decision** (`eqrisk/validation/backtest.py`, `validation:` in the model YAML).
- **Periods.** Forecasts are scored on non-overlapping 21-session periods from the first date with
  both a factor covariance and specific risk. The realized return is the sum of daily excess
  returns. A missing daily return inside a window counts as zero, i.e. the position is held as
  cash.
- **(a), (b).** Use the final matrix; "before" is Layers 1–2 and "after" is Layers 1–3, i.e. the
  final matrix divided by λ_F².
- **(c).** 100 random alpha vectors, drawn once from a seeded generator and fixed through time. The
  minimum-risk portfolios w = M⁻¹α are built and scored with each layer's matrix M. Factors not
  estimated throughout a window, such as a merged thin industry, are left out of that window.
- **(d).** "S&P 500" is the estimation universe (constituents less secondary share classes and
  names failing ESTU filters), cap-weighted with ESTU cap weights and also equal-weighted.
- **(e).** Industry portfolios are cap-weighted over ESTU names.
- **(f).** 100 random 50-name equal-weighted ESTU portfolios, redrawn every date. The seeds are
  sha256 of model, purpose and date, like D-004.
- **(g).** Forecast-volatility deciles use each stack's own forecasts.
- **Test-portfolio criterion.** Mean MRAD over families (c)–(f) ≤ 0.22. §1.3 asks for "close to
  0.17", with fat tails pushing it toward 0.19–0.22.
- **Operations criteria.** Read from `run-daily` manifests; `NOT RUN` until they exist.
  Bit-identical reruns are proved by the Phase 10 test, not by manifests.

## D-019 · The daily pipeline (2026-09-11)

**Decision** (`eqrisk/pipeline/daily.py`, `gates.py`, docs/RUNBOOK.md).
- **Catch-up.** A run starts after the last session that has outputs, whether from the backfill or
  a daily run, and goes through the latest session the vendor should have (today after 18:30 ET).
  A quarantined session counts as processed; `--force` re-runs one.
- **Recompute, write only pending sessions.** Staging is rebuilt fully (D-010). The regression's
  f/σ EWMA, both VRA multipliers and the specific-risk layers carry state, so the model recomputes
  them from `history.model_start` on every run and writes only the pending sessions. Each pending
  session simulates its own eigen adjustment, whereas the backfill simulated weekly (§13.4), so a
  daily session can differ slightly from the backfill's value for the same date. A rerun of a daily
  session is bit-identical.
- **VIF schedule.** VIF is computed every 21 sessions counted from the backfill's first date, so
  daily runs land on the same sessions.
- **Storage.** A session's rows go to `year=YYYY/day=YYYY-MM-DD.parquet`. Writing a date first
  removes it from wherever it was stored (the backfill's `data.parquet` or a month file), so each
  date lives in one file. `eqrisk compact --month` folds day files into `month=YYYY-MM.parquet`
  and is idempotent.
- **LATEST_GOOD.** `LATEST_GOOD.json` moves only when every FAIL gate passes; a failed or
  quarantined run never touches it. VIF gates as §6.4: warn above 5, fail above 10.
- **Vendor readiness.** EODHD has no dataset-range call on this plan, so the pipeline probes three
  liquid names for the session. It polls every 10 minutes for up to 90, then exits `SKIPPED`.
- **Notification.** A Windows toast through PowerShell's registered app id, which needs nothing
  installed, and always a line in `logs/notifications.log`. Email is not implemented: it would need
  SMTP credentials and a recipient address, which the owner has not provided.

## D-020 · Workbench and static viewer (2026-09-11)

**Context.** §15.2 suggests Bootstrap, Plotly.js and DuckDB-WASM for the static viewer, and Phase 11
asks for a site under 5 MB that works offline. Plotly's bundle alone is 4.2 MB, and DuckDB-WASM
needs tens of MB of WebAssembly. Node is not installed on this machine, and §17 wants the browser
analyzer tested against Python.

**Decision.**
- **Static viewer.** `site/` holds plain HTML, CSS and one `app.js` that draws its charts as SVG,
  plus `analyzer.js`, the portfolio algebra of `analytics.risk`. Data ships as compact JSON instead
  of Parquet: the snapshot, five years of history, the latest run status and the validation summary.
  The development export is well under the budget.
- **Exported fields.** Model outputs keyed by ticker only: exposures, F, specific variances, ESTU
  cap weights, factor returns, volatilities, λ series and bias summaries. No prices, volumes,
  market caps or fundamentals.
- **Rounding.** Numbers are rounded to 8–12 significant digits. The Python comparison runs on the
  same rounded arrays, so the 1e-8 match tests the algebra, not the rounding.
- **JS tests.** The analyzer is tested in V8 through `mini-racer`, a dev dependency, instead of node.
- **Workbench.** Streamlit with `st.navigation` (views in `app/views/`, loaders cached in
  `app/common.py`). Everything is read through `ModelStore`, which gained read-only history views.
  Only the portfolio analyzer and optimizer compute anything, and only on demand.
- **Optimizer page.** Riskfolio's tracking error is historical (§12.3), so the page applies the
  tracking-error cap to the factor-form optimizer only.

## D-021 · Factor bias in 2020–2021 (2026-09-12)

**Observation.** Phase 6 asks that per-factor bias statistics over 2020–2021 lie "mostly inside
[0.85, 1.15], with any exceptions listed". Only 13 of 33 do, with a mean of 1.12. The window holds
25 non-overlapping 21-session periods, so a bias statistic's own 95% band is 1 ± 0.28, and 25 of
33 lie inside that.

The misses are the COVID crash. Some factors were under-forecast in March 2020, before the regime
multiplier reacted:
- CONSUMER_SERVICES 1.65, LEVERAGE 1.63, CONSUMER_DURABLES_APPAREL 1.61
- ENERGY 1.49, BOOK_TO_PRICE 1.36, EARNINGS_YIELD 1.35

Others were over-forecast in the calm 2021 that followed:
- MATERIALS 0.71, LIQUIDITY 0.73, NONLINEAR_BETA 0.73, CAPITAL_GOODS 0.79

| Window | Periods | Mean bias | Inside [0.85, 1.15] | Inside its 95% band |
|---|---|---|---|---|
| 2019 | 12 | 0.75 | 10/33 | 23/33 |
| 2020–2021 | 25 | 1.12 | 13/33 | 25/33 |
| 2022–2026 | 55 | 1.00 | 27/33 | 32/33 |
| 2019–2026 | 91 | 1.02 | 30/33 | 30/33 |

The 2019 forecasts are provisional (under 756 sessions of factor returns) and carry the 2018
volatility spikes, which is why they over-forecast.

**Decision.** Keep the USE4S parameters (D6). The forecasts are unbiased over the full backtest and
since 2022. The Phase 6 golden test:
- holds the whole backtest to the §1.3 criterion: mean in [0.85, 1.15], two thirds of factors inside
- holds 2020–2021 to the statistic's 95% band
- prints the 2020–2021 exceptions

The literal 2020–2021 criterion is not met, and this entry records it. Revisit it when calibrating
the VRA half-life against the bias battery (a Phase 12 stretch goal).

## D-022 · Test-portfolio MRAD fails the §1.3 criterion (2026-09-12)

**Observation.** `eqrisk validate` over 2019–2026 (92 periods) passes every measured §1.3
criterion except one. Test portfolios have a 12-period MRAD of 0.239 against the 0.22 cap in
`validation.criteria.mrad_max`. By family: (c) optimized 0.240, (d) ESTU 0.238, (e) industry
0.218, (f) random 0.260. Splitting the window shows a pattern, not noise:

| Window | Periods | MRAD (c) | (d) | (e) | (f) | ESTU equal-weighted bias | Random 50-name bias | Mean factor bias |
|---|---|---|---|---|---|---|---|---|
| 2019–2021 | 37 | 0.33 | 0.34 | 0.28 | 0.45 | 1.15 | 1.16 | 1.02 |
| 2022–2026 | 55 | 0.19 | 0.25 | 0.22 | 0.28 | 0.70 | 0.72 | 1.00 |

- **2019–2021.** Diversified long-only portfolios were under-forecast through the COVID crash.
- **2022–2026.** The same portfolios are over-forecast by about 40%, although the pure factor
  portfolios are unbiased (mean 0.995). The error is therefore in how factors co-move, not in their
  volatilities.
- **Likely cause.** The USE4S correlation half-life of 504 sessions (D6) carries crash-era
  correlations for years after the crash.
- **Optimized portfolios.** The eigen adjustment keeps family (c) in line after 2021 (MRAD 0.19;
  bias 1.06, against 1.18 before the adjustment).

**Decision.** The scorecard reports this as FAIL. The threshold and parameters are unchanged,
because the blueprint fixes USE4S for v1. Options, left to the owner:
1. Evaluate the `lab` preset: correlation half-life 252, 5 NW lags, eigen a = 1.2. Build it as a
   second model id and compare the batteries.
2. Add a portfolio-level regime adjustment.
3. Accept the result for the 21-session horizon.

Revisit after option 1.

## D-023 · Layer boundaries made enforceable; table I/O and panel loading moved down (2026-09-12)

**Observation.** A pre-ship review of the import graph found one edge pointing the wrong way:
`eqrisk/validation/backtest.py` imported `factor_order` and `load_panel` from
`eqrisk/pipeline/model_run.py`, and `read_table` from `eqrisk/pipeline/stage.py`, while
`eqrisk/pipeline/model_run.py` imports `eqrisk/validation/bias.py`. That is a cycle between the
orchestration and validation packages: re-estimating the model inside the backtest pulled in the
daily pipeline, and neither package could be imported, tested or replaced without the other.

**Decision.** Move each function to the layer that owns it, with no shims:

| Function | Was | Now | Why |
|---|---|---|---|
| `read_table`, `replace_table` | `pipeline/stage.py` | `store.py` (1) | Storage primitives over Parquet, used by both the staging and model stages |
| `load_panel`, `factor_order` | `pipeline/model_run.py` | `model/tables.py` (4) | Turning staged tables into model inputs is a model concern; the daily run, `validate` and notebooks all need it |

`model/tables.py` is new. Call sites in `pipeline/model_run.py`, `validation/backtest.py` and the
phase 5–7 golden tests follow the new locations. Behaviour is unchanged: same functions, same
callers, and the phase acceptance tests reproduce the same numbers.

**Enforcement.** `tests/test_architecture.py` reads the imports of every module in `eqrisk/` and
`app/` and fails on: an import pointing at a higher layer; a kernel importing anything but numpy
and its siblings; anything in `eqrisk/` importing streamlit, plotly or `app/`; the UI importing
outside its published read surface; and the UI calling a write function. A module in a new
top-level package fails the completeness test until it is placed in the layer map. The layers are

```text
kernels(0) <- config/log/ids/calendar/manifest/store/frames(1) <- sources(2) <- staging(3)
          <- model(4) <- analytics/validation/optimize(5) <- pipeline(6) <- cli(7)
```

**Also in this pass.**

- `eqrisk/frames.py` (new, layer 1): `as_float`, `as_int`, `as_str` for reading one value out of a
  polars frame. `Series.max()` and friends are typed as any Python scalar, so the 15 report and
  manifest sites that need a number now say so and raise on an empty column instead of relying on
  `or 0.0`.
- `validation/bias.py`: `specific_bias` returns a `SpecificBias` TypedDict rather than
  `dict[str, float | dict[int, float]]`, so `["overall"]` is a float and `["by_decile"]` a mapping.
- `model/regression.py`: `DayFit.date` shadowed the `date` type for the fields declared after it.
  Both date fields are now `dt.date`. `typing.get_type_hints` resolved it correctly (annotations are
  evaluated in module scope), so this was a readability and static-analysis fix, not a live bug.
- `model/factor_cov.py`: the weekly eigen-refresh key is `(iso.year, iso.week)` instead of a slice
  of the ISO namedtuple, which typed as a variable-length tuple.
- `cli.py`: the ingest manifest status is annotated `RunStatus`, so an invalid status fails the type
  check rather than being written to a manifest.
- `mypy` now passes on all of `eqrisk` (62 modules) and `app` (22 modules), not only on
  `eqrisk/kernels`. Relaxations are per module and stated in `pyproject.toml`: untyped third-party
  libraries, and the two blueprint-verbatim optimizer modules whose signatures the blueprint
  supplies without annotations.

## D-024 · API keys: kept local by path, by content and by history (2026-09-12)

**Observation.** A GitHub remote is configured (`origin`), so anything committed is one `git push`
from being published. `.env` itself was ignored and no secret had ever been committed, but three
gaps remained: `.gitignore` matched only the exact name `.env`, so `.env.local` or a `.env.bak`
left by an editor was committable; nothing stopped a key being pasted into a notebook, a doc or a
config file, where no filename rule applies; and `pre-commit` was a dev dependency with no
configuration, so no check ran at commit time.

**Decision.** Three independent layers, because each catches a different mistake.

1. **Paths.** `.gitignore` now covers `.env*` (with `!.env.example`), `secrets.*`,
   `credentials.json`, `apikeys.yaml`, `*.pem`, `*.pfx`, `*.p12`, `*.key`, SSH private keys,
   `exports/` and `*.parquet`, alongside the existing `data/`, `logs/`, `reports/` and `site/`.
2. **Content.** `tools/check_no_secrets.py` reads the values out of the local `.env` and refuses a
   commit whose staged content contains any of them — including the contact address inside
   `SEC_USER_AGENT`, which may appear only in that variable and in git authorship. It also matches
   credential shapes for keys that are not on this machine (vendor token in a URL, quoted or bare
   API-key literal, `sk-`/`gh*_` tokens, AWS key ids, PEM private keys). Failure messages name the
   file and the variable and never print the value, so a blocked commit is safe to paste into a
   terminal, a log or a chat. `--all` scans every tracked file and `--history` every blob in every
   commit on every ref.
3. **Enforcement.** `.pre-commit-config.yaml` runs that guard on every commit, plus ruff, mypy and
   the architecture test. Installed with `uv run pre-commit install`; all hooks use
   `language: system`, so they need no downloads and behave the same in CI.

`tests/tools/test_check_no_secrets.py` covers both directions: 15 forbidden paths and 8 leaked
contents are blocked, 7 ordinary lines (including `{"api_token": self._key.get_secret_value()}`,
which is how an adapter is supposed to read a key) are not flagged, and no message echoes a secret.

**Verified.** `--history` over all 235 objects on every ref reports no secret and no vendor data has
ever been committed. A real `git commit` carrying a `.env` value was rejected by the hook, and a
forced `git add .env` was refused on both the path and the content rule. `.env.example` documents
the five variables by name, with no values.

**Unchanged and still true.** Keys live only in `.env`, reach the code as pydantic `SecretStr`, and
travel as query parameters that `eqrisk/sources/base.py::safe_url` strips from every exception and
log line (`tests/sources/test_http_base.py`). The Studio reports each key as configured or missing
and never reads its value. `httpx` loggers are pinned to WARNING so no request URL is logged.

## D-025 · A native Windows app replaces EQRisk Studio (2026-09-14)

**Context.** EQRisk Studio was a Streamlit page started by `EQRisk Studio.bat`: a console window, a
browser window without toolbars, a port to keep free, and nothing left running once the window
closed. The target is a native Windows app — one `EQRisk.exe`, an installer, a notification-area
icon — on a named stack: C# on .NET 10 (SDK 10.0.401), WPF with XAML and CommunityToolkit.Mvvm,
H.NotifyIcon.Wpf with an icon drawn by SkiaSharp, direct Windows API calls, Microsoft.Extensions.Hosting
with Serilog, Polly, SQLite, JSON settings, DPAPI, Tomlyn, System.Management, Velopack and xUnit.

**Decision.** The Python engine stays the only code that computes or writes the model. The desktop
app (`desktop/`, solution `EQRisk.slnx`) is a shell around it.

- **One engine, two channels.** Reads go to one long-lived `eqrisk serve` process
  (`eqrisk/pipeline/feed.py`): JSON lines over stdin and stdout, one request and one response per
  line, protocol version 1, NaN refused on the wire. It is read-only, holds no keys, and pays the
  3–6 s Python start once; a warm read takes milliseconds. Work — downloads, estimation, validation,
  exports — runs as the same `python -m eqrisk.cli …` commands the terminal and the schedule use.
  Each job is a child process inside a Windows Job Object that dies with the app, is logged to a
  file and recorded in SQLite. The six daily-update steps come from markers in the engine's own
  log lines; `tests/pipeline/test_desktop_contract.py` keeps both sides of that, and of the key
  list, in step.
- **Optimizer shared, not ported.** `eqrisk/optimize/service.py` holds the optimizer request and
  result that the workbench page and the `optimize` read both call.
- **Layers, enforced.** `EQRisk.Core` (records matching the engine's JSON, job specs, the key
  catalogue, settings) and `EQRisk.Presentation` (view models and chart models) target plain
  `net10.0`, so they cannot reference WPF or Windows. `EQRisk.Infrastructure` (engine process, job
  runner, SQLite, DPAPI vault, Task Scheduler, WMI) references Core only. `EQRisk.Desktop` (XAML,
  tray, native calls, hosting) composes them in `Hosting/Composition.cs`. An assembly-reference test
  fails the build otherwise, the C# twin of `tests/test_architecture.py`. A second test resolves
  every registered service on a WPF thread against a deadline: a dependency cycle through a factory
  registration gets past the container's checks and hangs the start with no window and no error,
  which happened once between the tray icon and the job activity.
- **Keys.** `.env` keeps working. A key set in the app is encrypted with DPAPI for the Windows user
  (plus app-specific entropy) in `%LOCALAPPDATA%\EQRisk\vault.json`, outside the repository, and
  handed as an environment variable only to jobs that download. Environment variables take
  precedence over `.env` in pydantic-settings, so the vault wins when both are set. The read server
  gets no keys, and every inherited key variable is scrubbed from its environment. The app shows
  where a key is set, never its value; the update token for a private repository sits in the same
  vault and reaches only the updater.
- **Stack rows that map onto this app rather than literally.** *HttpClient + Polly*: the app calls
  no web API itself (vendor HTTP stays in the engine, behind `metadata.get_cost` and the cost cap);
  Polly retries the engine server's start twice and a failed read once. *SQLite*: the job history.
  *Tomlyn*: reads `pyproject.toml` to recognise an engine folder. *WMI*: finds eqrisk jobs started
  outside the app (a terminal, the schedule), so two never run at once. *Own C# maths*: none; every
  figure comes from the tested Python engine, and a port would be a second implementation to keep
  in step. *Velopack*: Setup.exe, a portable zip and updates from GitHub Releases, built by
  `desktop/scripts/pack.ps1`; this change publishes no release.
- **Look.** WPF's Fluent dark theme is merged by pack URI (`Themes/Fluent.Dark.xaml`) instead of the
  experimental `Application.ThemeMode`, with the accent set to the site's steel blue and the
  palette and fonts of the viewer; DWM paints the title bar in the page colour. ScottPlot.WPF 5.1.59
  draws the charts. It needs SkiaSharp.Views.WPF 3.119, whose only builds target .NET Framework, so
  NU1701 is suppressed on that one package reference.
- **Retired.** `EQRisk Studio.bat` and the Streamlit Studio (`app/studio.py`, `app/studio_lib/`,
  `app/studio_pages/`, `tests/ui/test_studio.py`). The eight-page workbench (`eqrisk ui`, §15.1) and
  the static viewer (§15.2) stay.

**Verified (2026-09-14).** The C# solution builds with warnings as errors and passes 124 tests
(Core 32; Infrastructure 48, one opt-in test that registers a real scheduled task skipped;
Presentation 26; Desktop 18), among them every read and a restart against the real engine and the
resolution of the whole service graph. The Python suite passes (465 tests, golden included) with
ruff and mypy clean. Every page was opened in the running app, at 125% and at 150% display scaling,
against the development data; the portfolio analyzer and the factor-form optimizer ran end to end
(12.55% total risk for the sample holdings; 0.83% active risk, 424 names). Killing the app leaves no
engine process behind. `desktop/scripts/pack.ps1` produced Setup.exe (19.3 MB) and the portable zip
(15 MB), and the packaged build starts and reads the engine. Not exercised by hand: the tray menu
and flyout, and `--run-daily` against the vendors (its exit codes are unit-tested; running it would
download data).

## D-026 · EDGAR daily index: missing days and the watermark (2026-09-15)

**Observation.** The daily update for 2026-09-14 stopped in ingest: SEC answered HTTP 403 for the
daily index of Saturday 2026-09-12. SEC's Archives sit on S3-style storage, which answers
`403 AccessDenied` (a small XML error body), not 404, for any file that does not exist: weekends,
holidays (Labor Day and Columbus Day checked), a quarter not yet started. The client treated only
404 as "no index that day", so the first weekend inside the incremental window failed the run. The
same loop then set the watermark to the session date even when that day's index was only not
published yet, so a run before SEC publishes would have skipped that day's filings for good. The
desktop progress strip also showed the failure under Gates: it took the engine's `notify` event,
which a failed run sends from any step, as the Gates marker.

**Decision.**
- `HttpClient` maps a 403 whose body is an S3 error with code `AccessDenied` or `NoSuchKey` to
  `NotFoundError`. A real refusal (SEC's rate limit or a missing User-Agent, both HTML pages) is
  still an `EntitlementError` and still stops the run.
- `edgar_incremental` moves the `edgar_daily_index` watermark to the last day that had an index. The
  days after it are asked for again on the next run, and a weekend or holiday settles as soon as a
  later day's index appears. Point in time is unchanged: a filing counts from the first trading day
  after its filing date, whichever run picks it up.
- `run_daily` logs `gates evaluated` once a session's gates are computed, and the desktop app marks
  its Gates step by that event instead of `notify`.
