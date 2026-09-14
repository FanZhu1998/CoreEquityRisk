"""The two places where the C# desktop app (desktop/) depends on the engine by name rather than by
protocol. These tests read the C# sources, so renaming either side without the other fails here.

- The daily-update progress strip recognises each step by an event name the engine logs
  (desktop/src/EQRisk.Core/Jobs/DailyProgress.cs).
- The key catalog lists the environment variables the engine's Settings reads
  (desktop/src/EQRisk.Core/Keys/KeyCatalog.cs).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eqrisk.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "desktop" / "src" / "EQRisk.Core"


def _cs(rel: str) -> str:
    path = CORE / rel
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def _engine_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "eqrisk").rglob("*.py"))


def test_every_progress_marker_is_an_event_the_engine_logs() -> None:
    steps = re.findall(r'new\("([^"]+)",\s*\[([^\]]*)\]\)', _cs("Jobs/DailyProgress.cs"))
    assert len(steps) == 6, steps
    source = _engine_source()
    for name, markers in steps:
        for marker in re.findall(r'"([^"]+)"', markers):
            logged = re.search(r'log\.(?:info|warning)\(\s*f?"' + re.escape(marker), source)
            assert logged, f"step {name!r}: the engine never logs {marker!r}, so the progress strip would stall"


def test_the_key_catalog_matches_the_engine_settings() -> None:
    catalog = set(re.findall(r'new\("([A-Z_]+)",', _cs("Keys/KeyCatalog.cs")))
    engine = {name.upper() for name in Settings.model_fields}
    catalog.discard("GITHUB_UPDATE_TOKEN")               # the app's own token, never passed to the engine
    assert catalog == engine
