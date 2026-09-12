"""Corporate-action feeds (blueprint §4.3).

With `sources.corp_actions: eodhd_implied` (D-001) nothing is fetched: splits and dividends are
derived during staging from the unadjusted and adjusted closes EODHD already supplies
(`eqrisk.staging.corp_actions`). `sharadar` pulls the ACTIONS table. `databento_ref` is a
config slot for the Databento reference API, which this machine is not entitled to.
"""

from __future__ import annotations

from eqrisk.config import Project
from eqrisk.sources.base import Source, SourceError
from eqrisk.sources.sharadar import Sharadar


def corp_action_source(project: Project) -> Source | None:
    kind = project.config.sources.corp_actions
    if kind == "eodhd_implied":
        return None
    if kind == "sharadar":
        return Sharadar(project.sources, project.settings.nasdaq_data_link_api_key, "ACTIONS")
    raise SourceError("corp_actions: databento_ref needs the Databento reference API entitlement; "
                      "use eodhd_implied or sharadar")
