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
        typer.echo(json.dumps({"stage": "model", "status": "not available yet"}))


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
