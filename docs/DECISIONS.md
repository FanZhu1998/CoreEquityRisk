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

## D-006 · License (2026-09-11)

**Context.** §2.4 and §0 describe a proprietary codebase in a private repository.

**Decision (owner).** The repository is private on GitHub and keeps its MIT `LICENSE`. The
reference-repository cautions in §2 still apply regardless of our license: nothing from
`use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model` (no license) is copied.
