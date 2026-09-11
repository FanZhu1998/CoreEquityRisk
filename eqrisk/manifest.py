"""Run manifests (blueprint rule 4, §14 `run_manifest`): config hash, git SHA, status, gates."""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from eqrisk.store import atomic_write_bytes, write_parquet

RunStatus = Literal["RUNNING", "OK", "QUARANTINED", "FAILED", "SKIPPED"]
_TRACKED_PACKAGES = ("numpy", "scipy", "polars", "duckdb", "pyarrow", "cvxpy", "exchange-calendars")


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    command: str
    model_id: str
    as_of: date | None
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = "RUNNING"
    config_hash: str
    git_sha: str | None
    git_dirty: bool
    industry_scheme_version: str
    watermarks: dict[str, Any] = {}
    gates: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    notes: list[str] = []
    package_versions: dict[str, str] = {}


def git_state(root: Path) -> tuple[str | None, bool]:
    """(HEAD SHA, working tree dirty?) or (None, False) outside a git checkout."""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip() != ""
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, False


def package_versions() -> dict[str, str]:
    out = {}
    for name in _TRACKED_PACKAGES:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return out


def new_manifest(*, root: Path, command: str, model_id: str, config_hash: str,
                 industry_scheme_version: str, as_of: date | None = None) -> RunManifest:
    sha, dirty = git_state(root)
    started = datetime.now(UTC)
    return RunManifest(
        run_id=f"{started:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}",
        command=command, model_id=model_id, as_of=as_of, started_at=started,
        config_hash=config_hash, git_sha=sha, git_dirty=dirty,
        industry_scheme_version=industry_scheme_version, package_versions=package_versions(),
    )


def finish(manifest: RunManifest, status: RunStatus) -> RunManifest:
    return manifest.model_copy(update={"status": status, "finished_at": datetime.now(UTC)})


def write_manifest(manifest: RunManifest, model_dir: Path) -> Path:
    """JSON file per run plus one row in the `run_manifest` Parquet table."""
    path = model_dir / "manifests" / f"{manifest.run_id}.json"
    atomic_write_bytes(manifest.model_dump_json(indent=2).encode(), path)
    # Nested fields (watermarks, gates, ...) are stored as JSON strings, as §14 specifies.
    row = {k: (json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v)
           for k, v in manifest.model_dump(mode="json").items()}
    write_parquet(pl.DataFrame([row]), model_dir / "run_manifest" / f"{manifest.run_id}.parquet")
    return path


def read_manifests(model_dir: Path) -> list[RunManifest]:
    folder = model_dir / "manifests"
    if not folder.exists():
        return []
    return sorted((RunManifest.model_validate_json(p.read_text(encoding="utf-8"))
                   for p in folder.glob("*.json")), key=lambda m: m.started_at)
