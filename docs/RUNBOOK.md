# EQRisk runbook

How to run the model day to day, recover from failures, re-run a date, and rotate keys.
Commands run from the repository root (`C:\Users\Alcor\Projects\CoreEquityRisk`) with `uv run eqrisk …`.
The installed entry point is `.venv\Scripts\eqrisk.exe`.

## 1. What a daily run does

`eqrisk run-daily` processes every session after the last one with model outputs, through the
latest session the vendor should have published. That is today after 18:30 ET, otherwise the
previous session. For each run:

1. **Wait for data.** Probe EODHD for AAPL, MSFT and JPM on the target session. Poll every 10
   minutes for up to 90 minutes. If the data never appears, write a `SKIPPED` manifest and exit;
   the next scheduled run catches up.
2. **Ingest.** For each pending session: prices, reference snapshots, and EDGAR filings since the
   watermark.
3. **Stage.** Rebuild every staged table from raw (DECISIONS D-010).
4. **Model.**
   - Exposures for the pending sessions.
   - Regression, factor covariance (with its own eigen simulation per session) and specific risk.
     These carry state, so they are recomputed from `history.model_start`; only the pending
     sessions are written (D-019).
5. **Gates (§11.4).**
   - Every session gets a manifest: `OK`, or `QUARANTINED` when a FAIL gate trips.
   - `data/model/us_lc_v1/LATEST_GOOD.json` moves only on `OK`.
6. **Notify.** A Windows toast, always also appended to `logs/notifications.log`.

Consumers (`ModelStore`, the UI, `eqrisk snapshot`) read `LATEST_GOOD`, never a quarantined date.

The first production run (session 2026-09-11, on this laptop) took 5.1 minutes end to end:

| Step | Time |
|---|---|
| Ingest | 69 s |
| Staging | 45 s |
| Exposures | 68 s |
| Regression | 10 s |
| Factor covariance | 6 s |
| Specific risk | 109 s |

Every run writes its own timings to its manifest under `counts.timings`.

| Status | Meaning | Action |
|---|---|---|
| `OK` | all FAIL gates passed (WARN gates may be listed) | none; read warnings in the toast or manifest |
| `QUARANTINED` | outputs written, but a FAIL gate tripped; `LATEST_GOOD` unchanged | §3 |
| `FAILED` | an exception stopped the run | §4 |
| `SKIPPED` | the vendor had not published the session in time | none; the next run catches up |

Where to look:
- **Manifests:** `data/model/us_lc_v1/manifests/`. Each holds the gate results, timings and watermarks.
- **Logs:** `logs/eqrisk.jsonl` (structured) and `logs/notifications.log`.
- **Pointer:** `data/model/us_lc_v1/LATEST_GOOD.json`.

## 2. Scheduling on Windows

Run once every morning at 06:30; catch-up handles missed days (laptop asleep, vendor late). In a
PowerShell window:

```powershell
$root = "C:\Users\Alcor\Projects\CoreEquityRisk"
$action = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\eqrisk.exe" -Argument "run-daily" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 06:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "EQRisk Daily" -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable` runs a missed 06:30 start once the machine wakes. The toast only shows while
you are logged on; the log line is always written. The blueprint's `schtasks` form works too, but
it cannot set "start when available":

```text
schtasks /Create /SC DAILY /ST 06:30 /TN "EQRisk Daily" /TR "C:\Users\Alcor\Projects\CoreEquityRisk\.venv\Scripts\eqrisk.exe run-daily"
```

Check it: `Get-ScheduledTaskInfo -TaskName "EQRisk Daily"` (last run time and result). Remove it:
`Unregister-ScheduledTask -TaskName "EQRisk Daily"`.

## 3. A quarantined session

1. Open the session's manifest and read `gates.results`: every entry with `"ok": false` and
   `"level": "FAIL"` tripped.
2. Fix the cause. Typical causes:
   - **`data_freshness`:** the vendor missed names. Wait and re-run, or check `eqrisk doctor`.
   - **`regression`:** too few names or a singular system. Look for a universe or industry
     problem in `data/staged/exceptions`.
   - **`specific_coverage`:** a coverage name has no forecast, usually a new listing without
     exposures.
   - **Identity or share-count errors:** fix `configs/overrides/*.csv`, then run
     `eqrisk pull-overrides`.
3. Re-run the session: `eqrisk run-daily --date YYYY-MM-DD --force`. It re-stages, recomputes and
   upserts the session. `LATEST_GOOD` moves to it if its gates pass.
4. Sessions already processed after it keep their stored outputs. To refresh them too, re-run each
   in date order with `--force`, or rebuild with `eqrisk backfill --stage model`.

## 4. A failed run

Read the traceback in `logs/eqrisk.jsonl` and the `FAILED` manifest's `counts.error`, fix the
cause, then run `eqrisk run-daily` again. A session counts as processed only once its specific
risk (written last) exists, so a run that died midway is simply redone: every write replaces the
session's rows (upsert), never appends. Nothing under `data/raw` is ever modified in place (new
vendor data becomes a new vintage), so staging and the model can always be rebuilt:
`eqrisk backfill --stage stage`, then `--stage model`.

## 5. Re-running a date

- **With fresh vendor data:** `eqrisk run-daily --date YYYY-MM-DD --force`.
- **With the raw data already stored:** `eqrisk run-daily --date YYYY-MM-DD --force --offline`.
- **Model only, with the staged tables as they are:** add `--no-stage`.

Rerunning a session with unchanged inputs reproduces its files byte for byte (Phase 10
acceptance). The eigen simulation is seeded by model id and date (D-004).

## 6. Rotating API keys

Keys live only in `.env` at the repository root:
- `EODHD_API_KEY`, `FRED_API_KEY`, `DATABENTO_API_KEY`
- `SEC_USER_AGENT`, a name plus contact email, as the SEC requires

To rotate:
1. Edit the value in `.env`.
2. Run `eqrisk doctor` to confirm connectivity and entitlements.

Nothing else caches keys, and keys are never written to logs, manifests or data files. Databento
requests are priced before they are sent and refused above `databento.max_cost_usd` in
`configs/sources.yaml`.

## 7. Monthly housekeeping

- **Compact.** After a month ends, run `eqrisk compact --month YYYY-MM`. It merges that month's
  daily files (`year=YYYY/day=…parquet`) into one `month=YYYY-MM.parquet` per model table. It is
  safe to re-run; a date re-run later lands in a day file again and is folded in next time.
- **Validate.** Run `eqrisk validate` and read the scorecard in
  `reports/validation_<model>_<start>_<end>/report.md`. Re-run it after any model change.
- **Review exceptions.** Read `data/staged/exceptions`: identity, industry, corporate-action and
  share-count issues. Most are fixed with an override row.

## 8. Full rebuilds

| Change | Command |
|---|---|
| Override files edited | `eqrisk pull-overrides`, then `eqrisk backfill --stage stage`, then `--stage model` |
| Model parameters changed (configs/model_us_lc.yaml) | `eqrisk backfill --stage model` |
| Staging code changed | `eqrisk backfill --stage stage`, then `--stage model` |
| New machine or lost data | `eqrisk backfill --stage all` (ingest is resumable; EDGAR is pulled at ≤ 8 requests/second) |

The backfill simulates the eigen adjustment weekly (§13.4); daily runs simulate every session.
