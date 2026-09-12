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
  action) and non-positive prices. Stale runs (≥ 5 identical closes) keep their return but are
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
  would otherwise snap to 28:3. Every split in (1/3, 3) is confirmed against the issuer's share
  counts before and after (within 200 days). If the count moved closer to 1× than to k×, the
  event is re-run as a distribution (`split_reclassified`).
- EODHD keeps an old company's history under a reused code and names it after today's holder
  (XL, NFX). For an era that has ended, a superseded code (`XXX_old`) is therefore preferred.
  `MNK_old`'s name is simply wrong, and 21st Century Fox lives under `TFCFA/TFCF`; both are in
  `ticker_map.csv`. `eqrisk pull-overrides` fetches what the override files name.
- Holding-company reorganizations (BlackRock 2024, Disney 2019, Cigna 2018, Xerox 2019,
  WestRock 2018, ExxonMobil 2026) are linked to their predecessor registrant by name
  (`issuer_ciks`); Apache→APA, Bunge, and Mylan→Viatris are linked manually in `cik_links.csv`.
  Fundamentals are relabelled to the issuer, so the as-of lookup continues across the change.

## D-006 · License (2026-09-11)

**Context.** §2.4 and §0 describe a proprietary codebase in a private repository.

**Decision (owner).** The repository is private on GitHub and keeps its MIT `LICENSE`. The
reference-repository cautions in §2 still apply regardless of our license: nothing from
`use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model` (no license) is copied.
