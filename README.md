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
copy .env.example .env                    # then fill in the keys
uv run eqrisk init                        # folders, catalog, config validation, key check
uv run eqrisk doctor                      # connectivity and entitlements
uv run eqrisk backfill --end 2026-09-10   # ingest, stage, model (hours: EDGAR is rate-limited)
uv run eqrisk validate                    # bias battery and the §1.3 scorecard -> reports/
& ".\EQRisk Studio.bat"                   # open the app at http://127.0.0.1:8520
```

`.env` keys (see `.env.example`):

| Key | For | Required |
|---|---|---|
| `EODHD_API_KEY` | End-of-day prices and volume | yes |
| `FRED_API_KEY` | Risk-free rate (DTB3) | yes |
| `SEC_USER_AGENT` | Your name and contact email, as the SEC requires | yes |
| `DATABENTO_API_KEY` | Prices, metered against `sources.yaml` `max_cost_usd` | no |
| `NASDAQ_DATA_LINK_API_KEY` | Sharadar corporate actions | no |

Keys stay on this machine. They live only in `.env` (git-ignored), reach the code as pydantic
`SecretStr`, and travel as query parameters that `safe_url` strips from every log line and
exception message. The Studio reports each key as configured or missing and never reads its value.
`tools/check_no_secrets.py` blocks a commit that carries a key — by filename, or by matching the
values in your `.env` against the staged content — and runs automatically once you install the
hook:

```powershell
uv run pre-commit install                              # ruff, mypy, layer checks, secret guard
uv run python tools/check_no_secrets.py --all          # scan every tracked file
uv run python tools/check_no_secrets.py --history      # scan every commit on every ref
```

## The app

EQRisk Studio is the day-to-day interface: one window that loads data, estimates the factors,
validates the model and publishes the results. Every button runs the same `eqrisk` command as the
CLI and the scheduled job — the Studio starts them in the background and shows their progress. It
never writes to the data store itself.

### Starting it

Double-click **`EQRisk Studio.bat`** in the project folder. It starts the server minimized (a window
named *EQRisk Studio server*), waits for it to answer, then opens the Studio in its own Edge window
with no browser toolbars, or in your default browser if Edge is not installed. If the server is
already running it just opens the window.

```powershell
uv run streamlit run app/studio.py        # the same app from a terminal, with the log in view
```

- It listens on **http://127.0.0.1:8520**, and on that address only, so it is not reachable from
  your network. If 8520 is taken, change `PORT` at the top of the .bat.
- For a desktop icon: **System › Jobs & settings › Settings › Create desktop shortcut**.
- To stop it: **Settings › Shut down Studio**, or close the server window. A job that is running
  keeps running and reappears the next time you open the Studio.

### Your day in the app

1. Open the Studio. **Today** opens first and says where the model stands: the session it is
   estimated through, the last published session (`LATEST_GOOD`), how the last daily update ended,
   the market return (the country factor), the λF / λS volatility-regime multipliers, and the
   cross-sectional R² with the name count.
2. If sessions are pending, click **Run today's update**. Progress moves through six steps — *Wait
   for data · Ingest · Stage · Exposures · Factor model · Gates* — with the live log underneath.
   One session takes about five minutes.
3. When it finishes the page refreshes itself and shows the quality gates of that run, the style
   factor returns in units of their trailing volatility, the best and worst industries, and the λ
   trend over the last year.
4. If nothing is pending, Today says so and names the next session and when it can be estimated:
   vendors publish end-of-day data after **18:30 ET** (`pipeline.vendor_ready_after_et`). A
   **Re-run** button recomputes the last session from the raw data already on disk.

**Use stored data only** appears beside the main button when the raw prices on disk already cover
the pending sessions. It skips the downloads and re-estimates from what you have.

### Navigating

Navigation is the bar across the top; *Explore*, *Portfolio* and *System* are menus.

| Page | What you do there |
|---|---|
| **Today** | Where the model stands, the one-click daily update, gate results, today's factor moves |
| **Data** | Check keys and connections, see what is on disk, download history or one session, inspect data-quality exceptions |
| **Estimate** | Catch up, re-estimate one session, rebuild the whole model history, watch model health |
| **Validate** | Run the point-in-time backtest and read the §1.3 scorecard |
| **Explore ▾** | Factor returns · Factor risk · Exposures · Specific risk |
| **Portfolio ▾** | Portfolio analyzer · Optimizer |
| **System ▾** | Publish · Jobs & settings |

**Explore** and **Portfolio** show one date at a time. Open the sidebar with the arrow at the top
left to change the **As of** date; it defaults to the latest published session.

**Data** reports each `.env` key as *configured* or *missing* — never its value. **Test connections**
runs the same checks as `eqrisk doctor` (reachability and entitlements, per vendor), and **Reload
.env** picks up an edited file without restarting. *Load data* has four tabs: **One session** for a
single day, **Date range** for history (a full ten-year build takes a few hours, mostly EDGAR's rate
limit, and resumes where it stopped), **Override files** for what `configs/overrides/` names, and
**Rebuild staging** to rebuild every staged table from raw data in about a minute. A daily update
already downloads what it needs, so these are for history, gaps and repairs.

**Estimate** does the same work as Today plus two things Today does not: *Re-estimate one session*
with the data source you choose — **Fresh download**, **Stored data**, or **Stored staging** (skips
restaging, fastest) — and, behind a confirmation, *Rebuild the whole model history*, which takes
about an hour and is what you run after changing parameters in `configs/model_us_lc.yaml`.

**Validate** takes a window (2019-01-02 to the last session by default) and runs the backtest. The
scorecard lists every success criterion as PASS, FAIL or not-yet-measurable, next to the external
checks (§11.3), the factor bias chart with its sampling band, the eigenfactor smile before and
after the adjustment, and specific-risk bias by size and volatility decile.

**System › Publish** exports the static offline viewer (model outputs only, about 1 MB, no vendor
prices or fundamentals), serves it on 127.0.0.1:8000, writes a `.npz` snapshot for notebooks and
optimizers, and compacts a finished month's daily files into one file per table.

**System › Jobs & settings** has five tabs: **Studio jobs** (every job this app has run, with its
full log), **Pipeline runs** (the run manifests — config hash, git SHA, gates, timings), **Daily
schedule** (register, run or remove the Windows task `EQRisk Daily`, 06:30 by default; see
`docs/RUNBOOK.md` §2), **Settings** (model parameters, data sources, paths, desktop shortcut, shut
down), and **Runbook & decisions**, which renders `RUNBOOK.md`, `DECISIONS.md` and this README in
the app.

### How it behaves

- **One job at a time.** A button that cannot start explains why on hover: another job is running,
  the scheduled job is running, or a required key is missing. You can use the Studio, the scheduled
  task, or both.
- **Jobs outlive the window.** Closing the Studio does not stop a running job. Logs go to
  `logs/studio/`, and the job reappears when you come back.
- **Keys are never displayed.** Only *configured* or *missing*; see the key table above.
- **Raw data is immutable.** A download whose contents differ becomes a new version; nothing is
  overwritten.
- **Nothing is published unless the gates pass.** A FAIL gate quarantines the session and
  `LATEST_GOOD` does not advance, so the viewer, the snapshots and the workbench keep serving the
  last good model.

### If something goes wrong

| Symptom | What to do |
|---|---|
| The window opens blank, or not at all | Look at the *EQRisk Studio server* window for the error, or start it with `uv run streamlit run app/studio.py` to see the log |
| "Missing in .env: …" | Add the keys to `.env`, then **Data › Reload .env**. No restart needed |
| A job failed | Its log panel opens by itself; the full log is in **System › Studio jobs** and under `logs/studio/` |
| A session was quarantined | A FAIL gate fired. **Today** names the gate; `docs/RUNBOOK.md` §3 covers the recovery |
| Port 8520 already in use | The launcher opens whatever is already listening. To move it, change `PORT` in the .bat |

The older eight-page workbench is still there (`uv run eqrisk ui`, port 8501) and shares the
Studio's theme; the Studio's *Explore* and *Portfolio* menus are those same pages.

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

Four compartments, in dependency order. Each one can be imported, tested and replaced on its
own, and an import may only point at a lower layer — `tests/test_architecture.py` fails the build
otherwise (D-023).

```text
foundation   eqrisk/kernels/      pure numpy: numpy in, numpy out, no I/O   (Appendix A)
             eqrisk/{config,calendar,store,frames,manifest,ids,log}.py

data         eqrisk/sources/      one vendor dataset each -> data/raw (immutable vintages)
             eqrisk/staging/      raw -> point-in-time tables

model        eqrisk/model/        panels, descriptors, exposures, regression, covariance,
                                  specific risk; tables.py loads model inputs; snapshot.py is
                                  the one published read surface

outputs      eqrisk/analytics/    portfolio risk decomposition
             eqrisk/validation/   bias batteries, §11.2 backtest, §1.3 scorecard
             eqrisk/optimize/     factor-form and riskfolio optimizers
             eqrisk/pipeline/     orchestration: ingest, stage, model_run, daily, gates, export
             eqrisk/cli.py        the only entry point that runs work

UI           app/studio.py        EQRisk Studio: the daily app (studio_lib/, studio_pages/)
             app/main.py          the eight-page workbench (views/, common.py)
             site_template/       static offline viewer (index.html, app.js, analyzer.js)
```

The UI reads model outputs through `ModelStore` and starts everything else as an `eqrisk` CLI
job, so it never writes to the store and cannot collide with the scheduled run.

```text
configs/           model YAML (every parameter), presets, sources, EDGAR concept map, overrides/
tools/             check_no_secrets.py (pre-commit and CI guard)
tests/             unit tests; tests/golden/ runs the §17 phase acceptance on real data
docs/              BLUEPRINT.md, DECISIONS.md, RUNBOOK.md
data/              raw (immutable vintages), staged, model  (git-ignored)
```

## Tests

```powershell
uv run pytest                                  # unit tests (live vendor calls excluded by default)
uv run pytest -m golden                        # §17 phase acceptance on the development data
uv run pytest tests/test_architecture.py       # layer boundaries: data / model / outputs / UI
uv run ruff check .
uv run mypy eqrisk app                         # strict, whole codebase
uv run python tools/check_no_secrets.py --all  # no key or vendor data in any tracked file
```

`uv run pre-commit install` runs ruff, mypy, the layer checks and the secret guard on every
commit. All hooks run from this project's environment, so they need no downloads.

| Suite | Covers |
|---|---|
| `tests/kernels/` | known-answer tests against Appendix A, including the verbatim check |
| `tests/staging/` | corporate actions, share counts, PIT fundamentals, universe |
| `tests/model/` | descriptors, exposures, regression, covariance, specific risk |
| `tests/validation/`, `tests/pipeline/` | bias batteries, scorecard, §11.4 gates |
| `tests/test_architecture.py` | the layer boundaries above |
| `tests/tools/` | the commit guard, in both directions |
| `tests/ui/` | the job runner, and every Studio and workbench page rendering on real data |

## License

MIT (see `LICENSE`). No code from `use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model`
(no license) is included. "Barra" and "USE4" are MSCI trademarks; they appear here only as
literature references.
