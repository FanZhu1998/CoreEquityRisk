"""EQRisk Studio: one window for the daily workflow (load data, estimate, validate, explore, publish).

Start it by double-clicking `EQRisk Studio.bat` in the project folder, or run
`uv run streamlit run app/studio.py`. Every action runs the same `eqrisk` command as the CLI and the
scheduled job, in the background and one at a time. API keys come from the project's .env and are
never displayed.
"""

import sys
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                  # pages import `common` and `studio_lib`

from studio_lib.theme import MARK, inject_css  # noqa: E402

st.set_page_config(page_title="EQRisk Studio", page_icon=str(MARK), layout="wide", initial_sidebar_state="collapsed")
inject_css()
st.logo(str(MARK), size="large")


def page(path: str, title: str, icon: str, **kw: object) -> st.Page:
    return st.Page(path, title=title, icon=icon, **kw)  # type: ignore[arg-type]


PAGES = {
    "": [
        page("studio_pages/home.py", "Today", ":material/today:", default=True),
        page("studio_pages/data.py", "Data", ":material/database:"),
        page("studio_pages/estimate.py", "Estimate", ":material/functions:"),
        page("studio_pages/validate.py", "Validate", ":material/fact_check:"),
    ],
    "Explore": [
        page("views/factor_returns.py", "Factor returns", ":material/show_chart:"),
        page("views/factor_risk.py", "Factor risk", ":material/grid_on:"),
        page("views/exposures.py", "Exposures", ":material/stacked_bar_chart:"),
        page("views/specific_risk.py", "Specific risk", ":material/scatter_plot:"),
    ],
    "Portfolio": [
        page("views/portfolio.py", "Portfolio analyzer", ":material/donut_large:"),
        page("views/optimizer.py", "Optimizer", ":material/tune:"),
    ],
    "System": [
        page("studio_pages/publish.py", "Publish", ":material/ios_share:"),
        page("studio_pages/system.py", "Jobs & settings", ":material/settings:"),
    ],
}

st.navigation(PAGES, position="top").run()
