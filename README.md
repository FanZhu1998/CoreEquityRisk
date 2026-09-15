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
powershell -ExecutionPolicy Bypass -File desktop\scripts\pack.ps1   # build the Windows app (.NET 10 SDK)
.\desktop\artifacts\releases\EQRiskDesktop-win-Setup.exe             # install it; EQRisk opens
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
exception message. The desktop app shows where each key is set, never its value (see [Keys](#keys)).
`tools/check_no_secrets.py` blocks a commit that carries a key — by filename, or by matching the
values in your `.env` against the staged content — and runs automatically once you install the
hook:

```powershell
uv run pre-commit install                              # ruff, mypy, layer checks, secret guard
uv run python tools/check_no_secrets.py --all          # scan every tracked file
uv run python tools/check_no_secrets.py --history      # scan every commit on every ref
```

## The app

EQRisk for Windows is the day-to-day interface: a native desktop app (C# on .NET 10, WPF) that
loads data, estimates the factors, validates the model and publishes the results. Every button runs
the same `eqrisk` command as the CLI and the scheduled task, in the background, with its progress
in the activity bar. Pages read the model through the engine and never write to the data store.
How it is built: `docs/DECISIONS.md` D-025.

### Installing it

Build the installer once; it needs the [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0):

```powershell
powershell -ExecutionPolicy Bypass -File desktop\scripts\pack.ps1
```

It writes these to `desktop\artifacts\releases\`, which git ignores:

| File | Use |
|---|---|
| `EQRiskDesktop-win-Setup.exe` | Installs EQRisk for your account (no administrator rights) with Start menu and desktop shortcuts and an entry in *Installed apps*, installing the .NET 10 Desktop Runtime first if it is missing |
| `EQRiskDesktop-win-Portable.zip` | Unzip anywhere and run `EQRisk.exe` |
| `*.nupkg`, `releases.win.json` | What a GitHub release needs for in-app updates |

The files are not code-signed, so the first time Windows SmartScreen may say it protected your PC:
choose **More info › Run anyway**. Signing needs a code-signing certificate.

API keys are never part of the build: the engine reads them from `.env` and the app from its
per-user vault, while running. `pack.ps1` compares the publish folder and the finished packages
(opened up) with your `.env` values and refuses to package on a match; run the same check on any
folder with `uv run python tools/check_no_secrets.py --scan <folder>`.

It runs on Windows 10 version 2004 or later and on Windows 11. To run it from source instead:
`dotnet run --project desktop\src\EQRisk.Desktop`.

On first start EQRisk looks for this project folder. A copy started from inside the folder finds
it by itself; an installed copy asks for it in **Settings › Engine folder**. The folder needs its
Python environment (`uv sync`). **Data** then lists each key and where it is set.

### A typical day

1. Open EQRisk from the Start menu or the notification-area icon. **Today** says whether any
   session is pending.
2. Click **Run today's update**. The activity bar at the bottom follows its six steps — *Wait for
   data · Ingest · Stage · Exposures · Factor model · Gates* — with the live log; one session takes
   about five minutes. You can close the window: the update carries on.
3. When it ends a notification says so, and every page moves to the new session. With the daily
   schedule registered, this happens by itself every morning.

### The window

The rail on the left groups the pages; the status at its foot is the engine's (green when ready,
amber while starting, red with the reason). The activity bar along the bottom shows the job that is
running or just ended, its elapsed time, the steps of a daily update, its log, and **Stop**.

| Page | What you do there |
|---|---|
| **Today** | Where the model stands: estimated through, last published (`LATEST_GOOD`), the last update, the market return, λF / λS, R². **Run today's update** when sessions are pending, or **Re-run** the last one. The gates of the last run, today's style moves, the best and worst industries, the λ trend |
| **Data** | The API keys and where each is set; **Test connections** (`eqrisk doctor`); what is on disk; load **One session**, a **Date range**, the **Override files**, or **Rebuild staging**; data-quality exceptions by type |
| **Estimate** | Catch up; re-estimate one session from a **Fresh download**, **Stored data** or **Stored staging**; rebuild the whole model history (behind a confirmation); fit and regime charts; that session's factor returns |
| **Validate** | Run the point-in-time backtest over a window; the §1.3 scorecard, the §11.3 external checks, the factor bias chart with its sampling band, the eigenfactor smile, specific-risk bias by decile |
| **Explore** | **Factor returns** (cumulative, pick the factors), **Factor risk** (volatilities and the correlation heatmap: hover a cell), **Exposures** and **Specific risk** (every name, searchable), each for an **As of** date |
| **Portfolio** | **Portfolio analyzer**: open a CSV of `ticker,weight` (add `bench_weight` for active risk) or edit the holdings, then **Analyze risk**. **Optimizer**: factor form (cvxpy) or Riskfolio, with exposure bands, a tracking-error cap, turnover and an optional factor tilt |
| **System** | **Jobs & runs** (every job with its full log, and the pipeline run manifests), **Publish** (the static viewer, an `.npz` snapshot, monthly compaction), **Settings** |

One job runs at a time, whether it was started here, in a terminal or by the schedule, and a job
that needs a key that is not set says which before it starts. Nothing is published unless the
gates pass: a FAIL quarantines the session and `LATEST_GOOD` stays where it was, so every page, the
viewer and the snapshots keep serving the last good model.

### Keys

Keys can stay in `.env`. To keep them out of the project folder altogether, use **Data › API keys ›
Set…**: the value is encrypted with Windows DPAPI for your account and stored in
`%LOCALAPPDATA%\EQRisk\vault.json`. A key set in the app takes the place of the one in `.env`. Only
jobs that download receive keys, as environment variables of that one process; the engine's read
server never sees them, and no page, log or dialog shows a value.

### The notification-area icon

Closing the window leaves EQRisk in the notification area, in Windows efficiency mode, so a running
or scheduled update is never cut short (switch this off in **Settings**). The icon's dot is green
when the model is up to date, amber when a session is pending and red on a problem; a ring shows an
update's progress. Click it for a summary and **Run today's update**, double-click to open the
window, right-click for the menu, which has **Exit EQRisk**. **Settings › Start with Windows** puts it
there at sign-in.

### The daily schedule

**Settings › Daily schedule › Schedule** registers the Windows task `EQRisk Daily` (06:30 by
default). It runs `EQRisk.exe --run-daily` as you, with no window, and catches up after sleep; the
run appears in **Jobs & runs**. The app and the task never run two jobs at once. Exit codes and the
command-line alternative are in `docs/RUNBOOK.md` §2.

### Updates

A copy installed with Setup.exe checks GitHub Releases when it starts (switch this off in
**Settings**) and installs an update from **Settings › Updates**. While the repository is private,
this needs a read-only token (**Set token…**), kept in the same vault. Releasing is manual: attach
the files `pack.ps1` wrote to a GitHub release.

### Where things are

| What | Where |
|---|---|
| Settings, the key vault, the job history | `%LOCALAPPDATA%\EQRisk\` (`settings.json`, `vault.json`, `eqrisk.db`) |
| The app's log, one file a day | `%LOCALAPPDATA%\EQRisk\logs\` |
| Each job's full output | `%LOCALAPPDATA%\EQRisk\logs\jobs\` |
| Model data, reports, the viewer | this folder: `data/`, `reports/`, `site/` |

### If something goes wrong

| Symptom | What to do |
|---|---|
| The engine status is red | It names the reason. Usually **Settings › Engine folder** needs this folder, or `.venv` is missing (`uv sync`). The app log has the details |
| A job will not start for a missing key | Set it in **Data › API keys**, or add it to `.env` |
| A job failed | Its log is in the activity bar; the full log is in **Jobs & runs** |
| A session was quarantined | A FAIL gate fired. **Today** names it; `docs/RUNBOOK.md` §3 covers the recovery |
| Nothing happens when you start EQRisk | It is already running: starting it again brings that window forward. If nothing appears, read the app log |

The eight-page Streamlit workbench (`uv run eqrisk ui`, §15.1) and the static viewer (§15.2) are
still there for a browser.

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
             eqrisk/pipeline/     orchestration: ingest, stage, model_run, daily, gates, export,
                                  and feed.py, the read server behind the desktop app
             eqrisk/cli.py        the only entry point that runs work

UI           desktop/             EQRisk for Windows: the C# / .NET 10 / WPF app (D-025)
             app/main.py          the eight-page workbench (views/, common.py)
             site_template/       static offline viewer (index.html, app.js, analyzer.js)
```

The UI reads model outputs through `ModelStore` (the desktop app through `eqrisk serve`, which
wraps it) and starts everything else as an `eqrisk` CLI job, so it never writes to the store and
cannot collide with the scheduled run.

```text
desktop/           EQRisk.slnx: Core, Infrastructure, Presentation, Desktop and their tests; scripts/
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
powershell -File desktop\scripts\check.ps1     # the desktop app: warning-free build and every C# test
```

`uv run pre-commit install` runs ruff, mypy, the layer checks and the secret guard on every
commit, and the desktop build and tests when `desktop/` changes. All hooks run from this project's
environment and the installed .NET SDK, so they need no downloads.

| Suite | Covers |
|---|---|
| `tests/kernels/` | known-answer tests against Appendix A, including the verbatim check |
| `tests/staging/` | corporate actions, share counts, PIT fundamentals, universe |
| `tests/model/` | descriptors, exposures, regression, covariance, specific risk |
| `tests/validation/`, `tests/pipeline/` | bias batteries, scorecard, §11.4 gates |
| `tests/test_architecture.py` | the layer boundaries above |
| `tests/tools/` | the commit guard, in both directions |
| `tests/pipeline/test_feed.py` | the desktop app's read server: every read on real data, the wire protocol, no key in any response |
| `tests/ui/` | the static viewer's JavaScript analyzer against the Python algebra |
| `desktop/tests/` | the app's layers and service graph, the engine protocol against the real engine, jobs and their cleanup, the key vault, the scheduler, every view model |

## License

MIT (see `LICENSE`). No code from `use4-learning-lab` (CC BY-NC) or `UePG-21/Barra-risk-model`
(no license) is included. "Barra" and "USE4" are MSCI trademarks; they appear here only as
literature references.
