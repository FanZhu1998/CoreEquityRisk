"""EQRisk command line (blueprint §13.1)."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from eqrisk.config import DEFAULT_CONFIG, PRESET_NAMES, Project, load_config, load_project
from eqrisk.log import configure_logging
from eqrisk.manifest import finish, new_manifest, write_manifest
from eqrisk.sources.base import IngestReport
from eqrisk.store import init_catalog

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="EQRisk: a USE4-style fundamental equity factor risk model.")

RootOpt = Annotated[Path, typer.Option("--root", help="Repository root (holds configs/ and data/).")]
ConfigOpt = Annotated[Path, typer.Option("--config", help="Model YAML, relative to --root.")]
StartOpt = Annotated[str | None, typer.Option(help="First date, YYYY-MM-DD (default history.price_start).")]
EndOpt = Annotated[str | None, typer.Option(help="Last date (default history.model_end or today).")]


class Stage(StrEnum):
    ingest = "ingest"
    stage = "stage"
    model = "model"
    all = "all"


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode (`eqrisk <command>`)."""


def _project(root: Path, config: Path) -> Project:
    project = load_project(root.resolve(), config)
    configure_logging(project.logs_dir)
    return project


def _echo_reports(reports: list[IngestReport]) -> None:
    for r in reports:
        extra = f" missing={len(r.missing)}" if r.missing else ""
        extra += f" errors={len(r.errors)}" if r.errors else ""
        typer.echo(f"  {r.source}/{r.dataset:<16} rows={r.rows:<9} written={r.partitions_written:<5} "
                   f"unchanged={r.partitions_unchanged}{extra}")


def _run_manifest(project: Project, command: str, as_of: date | None, reports: list[IngestReport]) -> None:
    m = new_manifest(root=project.root, command=command, model_id=project.config.model_id,
                     config_hash=project.config.config_hash(),
                     industry_scheme_version=project.config.industries.scheme_version, as_of=as_of)
    m = m.model_copy(update={"counts": {"ingest": [r.as_dict() for r in reports]}})
    status = "FAILED" if any(r.errors for r in reports) else "OK"
    write_manifest(finish(m, status), project.model_dir)


@app.command()
def init(root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Create the folder tree and catalog, validate configs under every preset, check keys."""
    root = root.resolve()
    config_path = config if config.is_absolute() else root / config
    for name in PRESET_NAMES:
        load_config(config_path, preset=name)
    project = _project(root, config)
    for d in (project.raw_dir, project.staged_dir, project.model_dir, project.logs_dir,
              project.reports_dir):
        d.mkdir(parents=True, exist_ok=True)
    init_catalog(project.catalog_path)

    missing = [p for p in (project.resolve(project.config.industries.map_file),
                           project.resolve(project.config.industries.overrides_file)) if not p.exists()]
    typer.echo(f"root        {project.root}")
    typer.echo(f"model_id    {project.config.model_id} (preset {project.config.preset})")
    typer.echo(f"config hash {project.config.config_hash()[:16]}")
    typer.echo(f"presets     {', '.join(PRESET_NAMES)} all validate")
    typer.echo(f"catalog     {project.catalog_path}")
    for key, present in project.settings.presence().items():
        typer.echo(f"  {key:<26} {'set' if present else 'MISSING'}")
    if missing:
        for p in missing:
            typer.echo(f"missing override file: {p}", err=True)
        raise typer.Exit(code=1)


@app.command()
def doctor(root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Connectivity, entitlements, dataset ranges and a cost estimate before any backfill."""
    from eqrisk.pipeline.doctor import run_doctor

    project = _project(root, config)
    rows = run_doctor(project)
    for r in rows:
        typer.echo(f"{r['status']:<8} {r['check']:<28} {r['detail']}")
    if any(r["status"] == "fail" for r in rows):
        raise typer.Exit(code=1)


@app.command()
def ingest(on: Annotated[str, typer.Option("--date", help="Session to ingest, YYYY-MM-DD.")],
           root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Pull every source for one session (idempotent)."""
    from eqrisk.calendar import get_calendar
    from eqrisk.pipeline.ingest import ingest_date

    project = _project(root, config)
    d = date.fromisoformat(on)
    if not get_calendar(project.config.calendar).is_session(d):
        typer.echo(f"{d} is not an {project.config.calendar} session; nothing to do")
        return
    reports = ingest_date(project, d)
    _echo_reports(reports)
    _run_manifest(project, f"ingest --date {d}", d, reports)


@app.command()
def backfill(start: StartOpt = None, end: EndOpt = None,
             stage: Annotated[Stage, typer.Option(help="Which stage(s) to run.")] = Stage.all,
             refresh_edgar: Annotated[bool, typer.Option(help="Re-pull EDGAR CIKs already stored.")] = False,
             root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Stage-wise backfill (§13.4): ingest everything, stage everything, then run the model by date."""
    from eqrisk.pipeline.ingest import backfill_ingest

    project = _project(root, config)
    h = project.config.history
    s = date.fromisoformat(start) if start else h.price_start
    e = date.fromisoformat(end) if end else (h.model_end or date.today())
    if stage in (Stage.ingest, Stage.all):
        reports = backfill_ingest(project, s, e, refresh_edgar=refresh_edgar)
        _echo_reports(reports)
        _run_manifest(project, f"backfill --stage ingest {s}..{e}", e, reports)
    if stage in (Stage.stage, Stage.all):
        from eqrisk.pipeline.stage import run_staging

        report = run_staging(project, e)
        typer.echo(json.dumps(report, indent=1, default=str))
        m = new_manifest(root=project.root, command=f"backfill --stage stage ..{e}", model_id=project.config.model_id,
                         config_hash=project.config.config_hash(),
                         industry_scheme_version=project.config.industries.scheme_version, as_of=e)
        write_manifest(finish(m.model_copy(update={"counts": {"staging": report}}), "OK"), project.model_dir)
    if stage in (Stage.model, Stage.all):
        from eqrisk.pipeline.model_run import (
            run_exposures,
            run_factor_cov_stage,
            run_regression_stage,
            run_specific_risk_stage,
        )

        report = {"exposures": run_exposures(project, e)}
        report["regression"] = run_regression_stage(project, e)
        report["factor_cov"] = run_factor_cov_stage(project, e)
        report["specific_risk"] = run_specific_risk_stage(project, e)
        typer.echo(json.dumps(report, indent=1, default=str))
        m = new_manifest(root=project.root, command=f"backfill --stage model ..{e}", model_id=project.config.model_id,
                         config_hash=project.config.config_hash(),
                         industry_scheme_version=project.config.industries.scheme_version, as_of=e)
        write_manifest(finish(m.model_copy(update={"counts": {"model": report}}), "OK"), project.model_dir)


@app.command("run-daily")
def run_daily_cmd(
        on: Annotated[str | None, typer.Option(
            "--date", help="Catch up through this session (default: the latest the vendor has).")] = None,
        offline: Annotated[bool, typer.Option(help="Skip ingest; use the raw data already stored.")] = False,
        stage: Annotated[bool, typer.Option(help="Rebuild the staged tables first.")] = True,
        force: Annotated[bool, typer.Option(help="Recompute --date even if it was processed.")] = False,
        root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Catch up every unprocessed session through D: ingest, stage, model, gates, LATEST_GOOD (§13.2)."""
    from eqrisk.pipeline.daily import run_daily

    project = _project(root, config)
    runs = run_daily(project, date.fromisoformat(on) if on else None, offline=offline, stage=stage, force=force)
    if not runs:
        typer.echo("nothing to do")
        return
    for m in runs:
        failed = [g["gate"] for g in m.gates.get("results", []) if not g["ok"]]
        typer.echo(f"{m.as_of} {m.status:<12} {', '.join(failed) or 'all gates pass'}")


@app.command()
def compact(month: Annotated[str, typer.Option("--month", help="Month to compact, YYYY-MM.")],
            root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Merge a month's daily model files into one file per table (§14)."""
    from eqrisk.pipeline.daily import compact_tables

    typer.echo(f"merged {compact_tables(_project(root, config), month)} day files")


@app.command()
def validate(start: Annotated[str | None, typer.Option(help="First forecast date (default: the first).")] = None,
             end: Annotated[str | None, typer.Option(help="Last forecast date (default: the last).")] = None,
             root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Bias battery and the §1.3 scorecard -> reports/ (§11.2)."""
    from eqrisk.validation.run import run_validation

    project = _project(root, config)
    res, out = run_validation(project, date.fromisoformat(start) if start else None,
                              date.fromisoformat(end) if end else None)
    for r in res.scorecard:
        typer.echo(f"{r['status']:<8} {r['area']:<16} {r['criterion']}: {r['value']}")
    typer.echo(f"report: {out / 'report.md'}")


@app.command()
def snapshot(on: Annotated[str | None, typer.Option("--date", help="As-of date (default: latest good).")] = None,
             out: Annotated[Path, typer.Option("--out", help="Where to write the .npz snapshot.")] = Path("snap.npz"),
             root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Write a RiskModelSnapshot (X, F, specific variance) for notebooks and optimizers (§10)."""
    from eqrisk.model.snapshot import ModelStore

    project = _project(root, config)
    snap = ModelStore(project).snapshot(date.fromisoformat(on) if on else None)
    snap.save(out)
    typer.echo(f"{snap.as_of}: {len(snap.sids)} securities x {len(snap.factors)} factors -> {out}")


@app.command()
def ui(port: Annotated[int, typer.Option(help="Local port for the workbench.")] = 8501,
       root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Streamlit workbench (§15.1) at http://localhost:PORT."""
    import os
    import subprocess
    import sys

    project = _project(root, config)
    env = {**os.environ, "EQRISK_ROOT": str(project.root), "EQRISK_CONFIG": str(project.config_path)}
    raise typer.Exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(project.root / "app" / "main.py"),
                                      "--server.port", str(port), "--server.headless", "true"], env=env))


@app.command("export-site")
def export_site_cmd(on: Annotated[str | None, typer.Option("--date", help="As-of date (default: latest good).")] = None,
                    years: Annotated[int | None, typer.Option(help="Years of history (default: config).")] = None,
                    out: Annotated[Path | None, typer.Option(help="Output folder (default: config).")] = None,
                    root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Static offline viewer -> site/ (§15.2); serve it with `python -m http.server -d site`."""
    from eqrisk.pipeline.export_site import export_site

    path, size = export_site(_project(root, config), date.fromisoformat(on) if on else None, years, out)
    typer.echo(f"{path}: {size / 1e6:.2f} MB")


@app.command("pull-overrides")
def pull_overrides_cmd(start: StartOpt = None, end: EndOpt = None,
                       root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Pull only the vendor codes and CIKs named in configs/overrides (after editing them)."""
    from eqrisk.pipeline.ingest import pull_overrides

    project = _project(root, config)
    s = date.fromisoformat(start) if start else project.config.history.price_start
    e = date.fromisoformat(end) if end else date.today()
    reports = pull_overrides(project, s, e)
    _echo_reports(reports)
    _run_manifest(project, "pull-overrides", e, reports)


if __name__ == "__main__":  # pragma: no cover
    app()
