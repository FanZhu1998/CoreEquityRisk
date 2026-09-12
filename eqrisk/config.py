"""Model and source configuration (blueprint §16), validated by pydantic v2.

Rule 4: every parameter lives in YAML. The models below declare shape and constraints only
and carry no numeric defaults, so a missing key is a validation error rather than a silent
fallback, and an unknown key is rejected (extra="forbid"). Presets are overlays read from
configs/presets.yaml and deep-merged over the model YAML before validation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PresetName = Literal["use4s", "use4l", "lab"]
PRESET_NAMES: tuple[str, ...] = get_args(PresetName)
StyleName = Literal[
    "BETA", "MOMENTUM", "SIZE", "EARNINGS_YIELD", "RESIDUAL_VOLATILITY", "GROWTH",
    "DIVIDEND_YIELD", "BOOK_TO_PRICE", "LEVERAGE", "LIQUIDITY", "NONLINEAR_SIZE", "NONLINEAR_BETA",
]
STYLE_NAMES: tuple[str, ...] = get_args(StyleName)

DEFAULT_CONFIG = Path("configs/model_us_lc.yaml")
PRESETS_FILE = "presets.yaml"
SOURCES_FILE = "sources.yaml"
# Descriptor weights are hand-entered decimals (.667 + .333); this only catches typos.
_WEIGHT_SUM_TOL = 1e-3


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Weighted(_Block):
    @field_validator("weights", check_fields=False)
    @classmethod
    def _weights_sum_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        if any(w < 0 for w in v.values()):
            raise ValueError(f"{cls.__name__}: descriptor weights must be non-negative")
        total = sum(v.values())
        if abs(total - 1.0) > _WEIGHT_SUM_TOL:
            raise ValueError(f"{cls.__name__}: descriptor weights sum to {total:.4f}, expected 1")
        return v


def _ordered_pair(v: tuple[float, float], what: str) -> tuple[float, float]:
    if v[0] > v[1]:
        raise ValueError(f"{what}: lower bound {v[0]} exceeds upper bound {v[1]}")
    return v


# --------------------------------------------------------------------------- #
# Model YAML                                                                  #
# --------------------------------------------------------------------------- #


class HistoryCfg(_Block):
    price_start: date
    model_start: date
    model_end: date | None

    @model_validator(mode="after")
    def _ordered(self) -> HistoryCfg:
        if self.model_start < self.price_start:
            raise ValueError("history.model_start precedes history.price_start")
        if self.model_end is not None and self.model_end < self.model_start:
            raise ValueError("history.model_end precedes history.model_start")
        return self


class RiskFreeCfg(_Block):
    provider: Literal["fred"]
    series: str
    daycount: Literal["act360"]


class SourcesCfg(_Block):
    prices: Literal["eodhd", "databento"]
    corp_actions: Literal["eodhd_implied", "sharadar", "databento_ref"]
    fundamentals: Literal["edgar", "sharadar"]
    membership: Literal["fja05680", "sharadar"]
    risk_free: RiskFreeCfg


class EstuCfg(_Block):
    mode: Literal["sp500", "top_n"]
    top_n: int
    min_history_days: int
    exclude_secondary_share_class: bool


class UniverseCfg(_Block):
    coverage: Literal["sp500"]
    estu: EstuCfg
    regression_weight: Literal["sqrt_total_mcap"]


class ThinRuleCfg(_Block):
    min_neff: float
    min_count: int
    lookback_days: int
    max_breach_days: int


class IndustriesCfg(_Block):
    scheme_version: str
    map_file: Path
    overrides_file: Path
    thin_rule: ThinRuleCfg


class OutlierCfg(_Block):
    error_robust_z: float
    clip_sigma: float
    size_clip_sigma: float | None


class BetaCfg(_Block):
    window: int
    half_life: float
    min_obs: int


class MomentumCfg(_Block):
    window: int
    lag: int
    half_life: float
    min_obs: int


class WindowHalfLifeCfg(_Block):
    window: int
    half_life: float


class ResvolCfg(_Weighted):
    weights: dict[Literal["DASTD", "CMRA", "HSIGMA"], float]
    dastd: WindowHalfLifeCfg
    cmra_months: int
    orthogonalize_to: list[StyleName]


class EarningsYieldCfg(_Weighted):
    weights: dict[Literal["CETOP", "ETOP"], float]
    cash_earnings: Literal["cfo", "ni_plus_da"]


class GrowthCfg(_Weighted):
    weights: dict[Literal["EGRO", "SGRO"], float]
    years: int
    min_years: int


class LeverageCfg(_Weighted):
    weights: dict[Literal["MLEV", "DTOA", "BLEV"], float]


class LiquidityCfg(_Weighted):
    weights: dict[Literal["STOM", "STOQ", "STOA"], float]
    block: int
    orthogonalize_to: list[StyleName]


class DividendYieldCfg(_Block):
    exclude_special: bool


class NonlinearCfg(_Block):
    orthogonalize_to: list[StyleName]


class MissingFillCfg(_Block):
    method: Literal["regression"]
    regressors: list[str]


class FundamentalsTimingCfg(_Block):
    availability_lag_sessions: int
    max_staleness_months: int


class DescriptorsCfg(_Block):
    outliers: OutlierCfg
    beta: BetaCfg
    momentum: MomentumCfg
    resvol: ResvolCfg
    earnings_yield: EarningsYieldCfg
    growth: GrowthCfg
    leverage: LeverageCfg
    liquidity: LiquidityCfg
    dividend_yield: DividendYieldCfg
    nonlinear_size: NonlinearCfg
    nonlinear_beta: NonlinearCfg
    missing_fill: MissingFillCfg
    fundamentals: FundamentalsTimingCfg


class RegressionCfg(_Block):
    constraint: Literal["cap_weighted_industry_sum_zero"]
    min_names: int
    input_clip_mad: float


class HalfLifeLagsCfg(_Block):
    half_life: float
    nw_lags: int


class EigenCfg(_Block):
    enabled: bool
    a: float
    n_sims: int
    estimator: Literal["same", "equal_weight_neff", "equal_weight_full"]
    backfill_refresh: Literal["daily", "weekly"]
    seed: Literal["date_hash"]


class FactorVraCfg(_Block):
    enabled: bool
    half_life: float
    sigma_source: Literal["lag0_ewma", "final_daily"]
    z_cap: float


class FactorCovCfg(_Block):
    vol: HalfLifeLagsCfg
    corr: HalfLifeLagsCfg
    history_cap_days: int
    min_history_days: int
    provisional_until_days: int
    eigen: EigenCfg
    vra: FactorVraCfg


class SpecificTsCfg(_Block):
    half_life: float
    nw_lags: int
    nw_half_life: float
    window: int
    c_nw_bounds: tuple[float, float]
    min_sigma_monthly: float

    @field_validator("c_nw_bounds")
    @classmethod
    def _bounds(cls, v: tuple[float, float]) -> tuple[float, float]:
        return _ordered_pair(v, "specific_risk.ts.c_nw_bounds")


class GammaCfg(_Block):
    method: Literal["cne5"]
    h_min: int
    h_ramp: int


class StructuralCfg(_Block):
    min_fit_names: int
    gamma_fit_threshold: float
    e0: Literal["smearing"]


class ShrinkageCfg(_Block):
    groups: Literal["size_decile"]
    n_groups: int
    q: float


class SpecificVraCfg(_Block):
    half_life: float
    z_cap: float


class SpecificRiskCfg(_Block):
    ts: SpecificTsCfg
    gamma: GammaCfg
    structural: StructuralCfg
    shrinkage: ShrinkageCfg
    vra: SpecificVraCfg


class GatesCfg(_Block):
    universe_min: int
    universe_max: int
    imputed_max: float
    country_track_bp: float
    lambda_bounds: tuple[float, float]
    spec_median_ann: tuple[float, float]
    price_coverage_min: float
    exposure_mean_tol: float
    exposure_std_tol: float
    constraint_resid_max: float
    cond_max: float
    regression_n_min: int
    r2_bounds: tuple[float, float]
    factor_shock_z: float
    stability_min: float
    stability_fundamental_min: float
    stability_lag_days: int
    vif_warn: float
    vif_fail: float

    @field_validator("lambda_bounds", "spec_median_ann", "r2_bounds")
    @classmethod
    def _bounds(cls, v: tuple[float, float]) -> tuple[float, float]:
        return _ordered_pair(v, "gates")


class PriceQaCfg(_Block):
    stale_run_days: int
    max_abs_return_no_action: float
    mcap_continuity_tol: float


class CorpActionQaCfg(_Block):
    split_detect_tol: float
    split_ratio_rel_tol: float
    split_small_term_max: int
    split_fraction_max: float
    split_confirm_window_days: int
    dividend_min_rel: float
    special_dividend_multiple: float
    dividend_lookback_sessions: int


class QaCfg(_Block):
    prices: PriceQaCfg
    corp_actions: CorpActionQaCfg


class SecurityMasterCfg(_Block):
    min_era_coverage: float
    name_match_min_similarity: float
    primary_class_lookback_days: int
    shares_overrides_file: Path
    cik_links_file: Path
    predecessor_gap_days: int
    filing_slack_days: int
    share_outlier_factor: float
    share_outlier_window_days: int
    public_float_max_age_months: int


class FundamentalsCfg(_Block):
    concepts_file: Path
    quarter_days: tuple[int, int]
    half_year_days: tuple[int, int]
    nine_month_days: tuple[int, int]
    year_days: tuple[int, int]
    comparative_tolerance_days: int


class ExportSiteCfg(_Block):
    enabled: bool
    years: int
    out: Path


class OutputsCfg(_Block):
    root: Path
    export_site: ExportSiteCfg


class ModelConfig(_Block):
    model_id: str
    preset: PresetName
    calendar: Literal["XNYS"]
    horizon_days: int
    history: HistoryCfg
    sources: SourcesCfg
    universe: UniverseCfg
    industries: IndustriesCfg
    descriptors: DescriptorsCfg
    regression: RegressionCfg
    factor_cov: FactorCovCfg
    specific_risk: SpecificRiskCfg
    gates: GatesCfg
    qa: QaCfg
    security_master: SecurityMasterCfg
    fundamentals: FundamentalsCfg
    outputs: OutputsCfg

    def config_hash(self) -> str:
        """sha256 of the canonical JSON of the fully resolved configuration."""
        canon = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# sources.yaml: endpoints, rate limits, dataset IDs                           #
# --------------------------------------------------------------------------- #


class HttpCfg(_Block):
    retries: int
    backoff_initial_s: float
    backoff_max_s: float
    timeout_s: float


class EodhdCfg(_Block):
    base_url: str
    exchange: str
    requests_per_second: float
    prev_buffer_days: int
    listings_refresh_days: int


class DatabentoCfg(_Block):
    dataset: str
    schema_: str
    stype_in: str
    max_cost_usd: float

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _rename_schema(cls, data: Any) -> Any:
        # "schema" shadows a BaseModel attribute; accept it from YAML under its natural name.
        if isinstance(data, dict) and "schema" in data:
            data = {**data, "schema_": data["schema"]}
            data.pop("schema")
        return data


class EdgarCfg(_Block):
    data_url: str
    www_url: str
    requests_per_second: float
    forms: list[str]
    cik_lookup_refresh_days: int


class FredCfg(_Block):
    base_url: str


class Fja05680Cfg(_Block):
    contents_api: str
    raw_base: str
    file_prefix: str


class FamaFrenchCfg(_Block):
    base_url: str
    factors_file: str
    momentum_file: str


class SharadarCfg(_Block):
    base_url: str
    page_size: int


class SourcesConfig(_Block):
    http: HttpCfg
    eodhd: EodhdCfg
    databento: DatabentoCfg
    edgar: EdgarCfg
    fred: FredCfg
    fja05680: Fja05680Cfg
    famafrench: FamaFrenchCfg
    sharadar: SharadarCfg


# --------------------------------------------------------------------------- #
# concepts.yaml: Appendix C fundamentals concept map                          #
# --------------------------------------------------------------------------- #


class ConceptItem(_Block):
    kind: Literal["instant", "flow_ttm", "annual"]
    unit: str
    chain: list[str]
    fallback_sum: list[str] | None = None
    per_concept: bool = False       # emit one series per concept; the consumer picks by priority
    sharadar: str | None


class ConceptsConfig(_Block):
    items: dict[str, ConceptItem]

    def concepts(self) -> set[tuple[str, str]]:
        """(taxonomy, concept) for every concept any item may read."""
        out = set()
        for item in self.items.values():
            for c in [*item.chain, *(item.fallback_sum or [])]:
                out.add(split_concept(c))
        return out


def split_concept(name: str) -> tuple[str, str]:
    """'dei:EntityCommonStockSharesOutstanding' -> ('dei', ...); bare names are us-gaap."""
    tax, _, concept = name.rpartition(":")
    return (tax or "us-gaap", concept)


# --------------------------------------------------------------------------- #
# Secrets (.env via pydantic-settings)                                        #
# --------------------------------------------------------------------------- #


class Settings(BaseSettings):
    """API keys and the SEC contact. Values are never logged or written to artefacts."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    databento_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    eodhd_api_key: SecretStr | None = None
    nasdaq_data_link_api_key: SecretStr | None = None
    sec_user_agent: str | None = None

    def presence(self) -> dict[str, bool]:
        return {name: getattr(self, name) is not None for name in type(self).model_fields}


def load_settings(root: Path) -> Settings:
    env = root / ".env"
    return Settings(_env_file=env if env.exists() else None)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def _overlay(base: dict[str, Any], patch: dict[str, Any], where: str = "") -> dict[str, Any]:
    """Deep-merge `patch` into a copy of `base`. Every patched key must already exist."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        path = f"{where}.{key}" if where else key
        if key not in out:
            raise KeyError(f"preset overlay sets unknown key {path!r}")
        if isinstance(value, dict) and isinstance(out[key], dict):
            out[key] = _overlay(out[key], value, path)
        else:
            out[key] = value
    return out


def load_config(path: Path, preset: str | None = None) -> ModelConfig:
    """Read the model YAML, apply its preset overlay (or `preset`), and validate."""
    raw = _read_yaml(path)
    name = preset if preset is not None else raw.get("preset")
    if name not in PRESET_NAMES:
        raise ValueError(f"unknown preset {name!r}; expected one of {PRESET_NAMES}")
    presets = _read_yaml(path.parent / PRESETS_FILE)
    if set(presets) != set(PRESET_NAMES):
        raise ValueError(f"{PRESETS_FILE} must define exactly {PRESET_NAMES}")
    merged = _overlay(raw, presets[name] or {})
    merged["preset"] = name
    return ModelConfig.model_validate(merged)


def load_sources(config_dir: Path) -> SourcesConfig:
    return SourcesConfig.model_validate(_read_yaml(config_dir / SOURCES_FILE))


@dataclass(frozen=True)
class Project:
    """Everything a command needs: repository root, validated configs, and secrets."""

    root: Path
    config_path: Path
    config: ModelConfig
    sources: SourcesConfig
    settings: Settings
    concepts: ConceptsConfig

    def resolve(self, p: Path | str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def staged_dir(self) -> Path:
        return self.data_dir / "staged"

    @property
    def model_dir(self) -> Path:
        return self.resolve(self.config.outputs.root) / self.config.model_id

    @property
    def catalog_path(self) -> Path:
        return self.data_dir / "catalog.duckdb"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def overrides_dir(self) -> Path:
        return self.config_path.parent / "overrides"


def load_project(root: Path | None = None, config: Path | None = None,
                 preset: str | None = None) -> Project:
    root = (root or Path.cwd()).resolve()
    config_path = (config or DEFAULT_CONFIG)
    config_path = config_path if config_path.is_absolute() else root / config_path
    cfg = load_config(config_path, preset)
    concepts_path = cfg.fundamentals.concepts_file
    return Project(
        root=root,
        config_path=config_path,
        config=cfg,
        sources=load_sources(config_path.parent),
        settings=load_settings(root),
        concepts=ConceptsConfig.model_validate(
            _read_yaml(concepts_path if concepts_path.is_absolute() else root / concepts_path)),
    )
