"""Phase 1 acceptance: every preset validates; unknown or missing keys fail."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from eqrisk.config import PRESET_NAMES, load_config, load_project, load_sources

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "model_us_lc.yaml"


def _variant(tmp_path: Path, mutate=None, presets_mutate=None) -> Path:
    cfg_dir = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", cfg_dir)
    path = cfg_dir / "model_us_lc.yaml"
    if mutate:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if presets_mutate:
        p = cfg_dir / "presets.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        presets_mutate(data)
        p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_every_preset_validates(preset):
    assert load_config(CFG, preset=preset).preset == preset


def test_use4s_is_table_4_1_and_5_1():
    c = load_config(CFG, "use4s")
    assert (c.factor_cov.vol.half_life, c.factor_cov.vol.nw_lags) == (84, 5)
    assert (c.factor_cov.corr.half_life, c.factor_cov.corr.nw_lags) == (504, 2)
    assert c.factor_cov.vra.half_life == 42 and c.factor_cov.eigen.a == 1.0
    ts = c.specific_risk.ts
    assert (ts.half_life, ts.nw_lags, ts.nw_half_life) == (84, 5, 252)
    assert c.specific_risk.shrinkage.q == 0.1 and c.specific_risk.vra.half_life == 42


def test_use4l_overlay_changes_only_vol_and_vra_half_lives():
    s, l = load_config(CFG, "use4s"), load_config(CFG, "use4l")
    assert l.factor_cov.vol.half_life == 252 and l.specific_risk.ts.half_life == 252
    assert l.factor_cov.vra.half_life == 168 and l.specific_risk.vra.half_life == 168
    assert l.factor_cov.corr == s.factor_cov.corr and l.descriptors == s.descriptors


def test_lab_overlay():
    c = load_config(CFG, "lab")
    assert (c.factor_cov.corr.half_life, c.factor_cov.corr.nw_lags) == (252, 5)
    assert c.factor_cov.eigen.a == 1.2 and c.factor_cov.eigen.estimator == "equal_weight_neff"
    assert c.specific_risk.shrinkage.q == 1.0
    assert c.descriptors.growth.weights == {"EGRO": 0.90, "SGRO": 0.10}


def test_unknown_top_level_key_fails(tmp_path):
    path = _variant(tmp_path, lambda d: d.__setitem__("surprise", 1))
    with pytest.raises(ValidationError, match="surprise"):
        load_config(path)


def test_unknown_nested_key_fails(tmp_path):
    path = _variant(tmp_path, lambda d: d["descriptors"]["beta"].__setitem__("windw", 252))
    with pytest.raises(ValidationError, match="windw"):
        load_config(path)


def test_missing_key_fails(tmp_path):
    path = _variant(tmp_path, lambda d: d["factor_cov"]["vol"].pop("half_life"))
    with pytest.raises(ValidationError, match="half_life"):
        load_config(path)


def test_descriptor_weights_must_sum_to_one(tmp_path):
    path = _variant(tmp_path, lambda d: d["descriptors"]["leverage"].__setitem__(
        "weights", {"MLEV": 0.75, "DTOA": 0.15, "BLEV": 0.5}))
    with pytest.raises(ValidationError, match="sum to"):
        load_config(path)


def test_unknown_descriptor_name_fails(tmp_path):
    path = _variant(tmp_path, lambda d: d["descriptors"]["growth"].__setitem__(
        "weights", {"EGRO": 0.5, "EGRLF": 0.5}))
    with pytest.raises(ValidationError):
        load_config(path)


def test_preset_overlay_cannot_invent_keys(tmp_path):
    path = _variant(tmp_path, presets_mutate=lambda p: p["lab"]["factor_cov"].__setitem__("nope", 1))
    with pytest.raises(KeyError, match="factor_cov.nope"):
        load_config(path, preset="lab")


def test_config_hash_is_stable_and_sensitive(tmp_path):
    a, b = load_config(CFG), load_config(CFG)
    assert a.config_hash() == b.config_hash()
    path = _variant(tmp_path, lambda d: d["regression"].__setitem__("min_names", 101))
    assert load_config(path).config_hash() != a.config_hash()


def test_exponents_load_as_floats():
    g = load_config(CFG).gates
    assert g.cond_max == 1e6 and isinstance(g.cond_max, float)
    assert g.constraint_resid_max == 1e-10


def test_sources_yaml_validates():
    s = load_sources(ROOT / "configs")
    assert s.databento.schema_ == "ohlcv-1d" and s.edgar.requests_per_second <= 10


def test_settings_read_env_without_echoing_secrets(tmp_path):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    (tmp_path / ".env").write_text("EODHD_API_KEY=sekrit-123\nSEC_USER_AGENT=Test test@example.com\n")
    project = load_project(tmp_path)
    assert project.settings.eodhd_api_key is not None
    assert project.settings.eodhd_api_key.get_secret_value() == "sekrit-123"
    assert "sekrit-123" not in repr(project.settings)
    assert project.settings.presence()["eodhd_api_key"] and not project.settings.presence()["fred_api_key"]
