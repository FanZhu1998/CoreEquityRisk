"""Shared, cached loaders for the workbench pages. Everything is read through ModelStore (§15.1):
the UI never runs model kernels except on-demand portfolio analytics and optimization."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from eqrisk.config import load_project
from eqrisk.manifest import RunManifest
from eqrisk.model.snapshot import ModelStore, RiskModelSnapshot

ROOT = Path(os.environ.get("EQRISK_ROOT") or Path(__file__).resolve().parents[1])
CONFIG = Path(os.environ["EQRISK_CONFIG"]) if os.environ.get("EQRISK_CONFIG") else None
SAMPLE_HOLDINGS = Path(__file__).resolve().parent / "sample_holdings.csv"
ANN = 12 ** 0.5
_MANIFEST_TTL_S = 60


@st.cache_resource
def store() -> ModelStore:
    return ModelStore(load_project(ROOT, CONFIG))


@st.cache_data(show_spinner=False)
def model_dates() -> list[date]:
    return store().dates()


@st.cache_data(show_spinner=False, ttl=_MANIFEST_TTL_S)
def latest() -> date:
    return store().latest()


def pick_date() -> date:
    """Sidebar as-of selector; defaults to LATEST_GOOD."""
    days = model_dates()[::-1]
    default = latest()
    return st.sidebar.selectbox("As of", days, index=days.index(default) if default in days else 0)


@st.cache_data(show_spinner=False)
def snapshot(d: date) -> RiskModelSnapshot:
    return store().snapshot(d)


@st.cache_data(show_spinner=False)
def factor_returns() -> pl.DataFrame:
    return store().factor_returns()


@st.cache_data(show_spinner=False)
def regression_stats() -> pl.DataFrame:
    return store().regression_stats()


@st.cache_data(show_spinner=False)
def factor_risk() -> pl.DataFrame:
    return store().factor_risk()


@st.cache_data(show_spinner=False)
def vra() -> pl.DataFrame:
    return store().vra()


@st.cache_data(show_spinner=False)
def eigen(d: date) -> pl.DataFrame:
    return store().eigen(d)


@st.cache_data(show_spinner=False)
def exposures(d: date) -> pl.DataFrame:
    return store().exposures(d)


@st.cache_data(show_spinner=False)
def exposure_history(sid: int) -> pl.DataFrame:
    return store().exposure_history(sid)


@st.cache_data(show_spinner=False)
def universe(d: date) -> pl.DataFrame:
    return store().universe(d)


@st.cache_data(show_spinner=False)
def specific(d: date) -> pl.DataFrame:
    return store().specific(d)


@st.cache_data(show_spinner=False)
def omega(d: date) -> pl.DataFrame:
    return store().omega(d)


@st.cache_data(show_spinner=False, ttl=_MANIFEST_TTL_S)
def manifests() -> list[RunManifest]:
    return store().manifests()


@st.cache_data(show_spinner=False, ttl=_MANIFEST_TTL_S)
def validation_report() -> dict[str, Any] | None:
    """The latest `eqrisk validate` output: summary, tables and figure paths."""
    vdir = store().validation_dir()
    if vdir is None:
        return None
    tables = {p.stem: pl.read_csv(p) for p in vdir.glob("*.csv")}
    return {"dir": str(vdir), "summary": json.loads((vdir / "summary.json").read_text(encoding="utf-8")),
            "tables": tables, "figures": sorted(str(p) for p in vdir.glob("*.png"))}
