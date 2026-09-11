# EQRisk: agent instructions

The source of truth is docs/BLUEPRINT.md; read the sections named in each task before writing code.
Decisions that deviate from, or fill gaps in, the blueprint are recorded in docs/DECISIONS.md.
Read that file too before changing anything in the data layer.

## Rules (blueprint §0.1)

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

## Project facts that are not in the blueprint

- Data tier in use (DECISIONS D-001): EODHD end-of-day prices (unadjusted close plus adjusted close),
  corporate actions implied from those two series (EODHD's dividends and splits endpoints return 403
  on this subscription), SEC EDGAR for shares outstanding, SIC codes and fundamentals, FRED DTB3,
  `fja05680/sp500` for point-in-time membership. Databento is wired but metered: price every request
  with `metadata.get_cost` and never pull above `sources.yaml` `databento.max_cost_usd`.
- `eqrisk/kernels/reference.py` is Appendix A.1 verbatim. Do not edit it; add new kernels in
  sibling modules with their own known-answer tests.

## Commands

```text
uv sync                                  # install
uv run pytest                            # full test suite (live tests deselected)
uv run pytest -m live                    # tests that call vendor APIs
uv run ruff check . && uv run mypy --strict eqrisk/kernels
uv run eqrisk --help                     # CLI (§13.1)
```
