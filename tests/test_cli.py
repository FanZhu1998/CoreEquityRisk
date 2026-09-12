import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from eqrisk.cli import app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_init_creates_tree_and_catalog(tmp_path: Path):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for rel in ("data/raw", "data/staged", "data/model/us_lc_v1", "logs", "reports"):
        assert (tmp_path / rel).is_dir()
    assert (tmp_path / "data" / "catalog.duckdb").is_file()
    assert "all validate" in result.output


def test_init_rejects_unknown_key(tmp_path: Path):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    cfg = tmp_path / "configs" / "model_us_lc.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["regression"]["typo_key"] = 1
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code != 0
