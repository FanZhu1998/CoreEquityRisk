"""The layer boundaries of blueprint section 2, enforced.

Modularity that is only documented drifts. These tests read the imports of every module and
fail if data, model, outputs, orchestration or UI code reaches somewhere it must not:

    kernels(0) <- config/log/ids/calendar/manifest/store/frames(1) <- sources(2) <- staging(3)
              <- model(4) <- analytics/validation/optimize(5) <- pipeline(6) <- cli(7)

An import may only point at a lower or equal layer, so the graph stays acyclic and any layer
can be tested, replaced or imported on its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LAYER = {"kernels": 0,
         "config": 1, "log": 1, "ids": 1, "calendar": 1, "manifest": 1, "store": 1, "frames": 1,
         "sources": 2, "staging": 3, "model": 4,
         "analytics": 5, "validation": 5, "optimize": 5,
         "pipeline": 6, "cli": 7}

# Kernels are pure numpy (rule 2): no frames, no I/O, no vendor or UI libraries.
KERNEL_FORBIDDEN = {"polars", "pandas", "duckdb", "httpx", "streamlit", "plotly", "matplotlib",
                    "pathlib", "os", "io", "requests"}

# The UI reads model outputs; it never re-implements the data or model layers. It may read
# through the model store and analytics, ask the config where things live, and use the
# read-only helpers behind the status pages. Everything else it wants, it runs as a CLI job.
APP_ALLOWED = {"eqrisk.config", "eqrisk.manifest", "eqrisk.store", "eqrisk.calendar",
               "eqrisk.model.snapshot", "eqrisk.model.exposures",
               "eqrisk.analytics.risk", "eqrisk.optimize.factor_form", "eqrisk.optimize.riskfolio_adapter",
               "eqrisk.pipeline.daily", "eqrisk.pipeline.doctor"}


def _imports(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def _modules(package: str) -> list[Path]:
    return sorted(p for p in (ROOT / package).rglob("*.py") if "__pycache__" not in p.parts)


def _layer(path: Path) -> str:
    return path.relative_to(ROOT / "eqrisk").parts[0].removesuffix(".py")


@pytest.mark.parametrize("path", _modules("eqrisk"), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_module_imports_a_higher_layer(path: Path) -> None:
    src = _layer(path)
    if src not in LAYER:
        return
    for mod in _imports(path):
        if not mod.startswith("eqrisk."):
            continue
        tgt = mod.split(".")[1]
        if tgt in LAYER and LAYER[tgt] > LAYER[src]:
            pytest.fail(f"{path.relative_to(ROOT)} ({src}, layer {LAYER[src]}) imports {mod} "
                        f"({tgt}, layer {LAYER[tgt]}): move the shared code down, do not import upwards")


@pytest.mark.parametrize("path", _modules("eqrisk/kernels"), ids=lambda p: p.name)
def test_kernels_are_pure_numpy(path: Path) -> None:
    for mod in _imports(path):
        if mod.split(".")[0] in KERNEL_FORBIDDEN or (mod.startswith("eqrisk.")
                                                     and not mod.startswith("eqrisk.kernels")):
            pytest.fail(f"kernels/{path.name} imports {mod}; kernels take numpy in and return numpy out, "
                        "and depend only on numpy and each other (blueprint rule 2)")


@pytest.mark.parametrize("path", _modules("eqrisk"), ids=lambda p: str(p.relative_to(ROOT)))
def test_the_model_never_imports_the_ui(path: Path) -> None:
    for mod in _imports(path):
        assert mod.split(".")[0] not in {"streamlit", "plotly", "app"}, (
            f"{path.relative_to(ROOT)} imports {mod}: presentation belongs in app/, and the model layer must "
            "stay importable from a notebook or a job with no UI installed")


@pytest.mark.parametrize("path", _modules("app"), ids=lambda p: str(p.relative_to(ROOT)))
def test_the_ui_only_reaches_the_published_read_surface(path: Path) -> None:
    for mod in _imports(path):
        if not mod.startswith("eqrisk"):
            continue
        assert mod in APP_ALLOWED, (f"{path.relative_to(ROOT)} imports {mod}, which is not part of the UI's "
                                    f"read surface. Read model outputs through eqrisk.model.snapshot.ModelStore, "
                                    f"or run the work as an `eqrisk` CLI job.")


@pytest.mark.parametrize("path", _modules("app"), ids=lambda p: str(p.relative_to(ROOT)))
def test_the_ui_does_not_write_to_the_data_store(path: Path) -> None:
    """Writes to data/ happen in background CLI jobs, so the store has one writer at a time."""
    src = path.read_text(encoding="utf-8")
    for banned in ("write_parquet(", "replace_table(", "upsert_dates(", "write_raw(", "write_manifest("):
        assert banned not in src, (f"{path.relative_to(ROOT)} calls {banned}: the UI must not write model or "
                                   "raw data; start an `eqrisk` job instead")


def test_every_module_is_assigned_a_layer() -> None:
    """A new top-level module or package must be placed in the map, not silently unchecked."""
    unplaced = sorted({_layer(p) for p in _modules("eqrisk")} - set(LAYER) - {"__init__"})
    assert not unplaced, (f"not in LAYER: {unplaced}. Add each to tests/test_architecture.py with the layer "
                          "it belongs to, so its imports are checked.")
