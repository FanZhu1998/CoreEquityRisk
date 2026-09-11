"""EQRisk command line (blueprint §13.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from eqrisk.config import DEFAULT_CONFIG, PRESET_NAMES, load_config, load_project
from eqrisk.log import configure_logging
from eqrisk.store import init_catalog

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="EQRisk: a USE4-style fundamental equity factor risk model.")

RootOpt = Annotated[Path, typer.Option("--root", help="Repository root (holds configs/ and data/).")]
ConfigOpt = Annotated[Path, typer.Option("--config", help="Model YAML, relative to --root.")]


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode (`eqrisk <command>`)."""


@app.command()
def init(root: RootOpt = Path("."), config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Create the folder tree and catalog, validate configs under every preset, check keys."""
    root = root.resolve()
    config_path = config if config.is_absolute() else root / config
    for name in PRESET_NAMES:
        load_config(config_path, preset=name)
    project = load_project(root, config)
    configure_logging(project.logs_dir)
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


if __name__ == "__main__":  # pragma: no cover
    app()
