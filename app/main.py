"""EQRisk workbench (blueprint §15.1). Start it with `eqrisk ui` (or `streamlit run app/main.py`)."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))          # pages import the shared `common` module

st.set_page_config(page_title="EQRisk", layout="wide")

PAGES = [("status", "Status"), ("factor_returns", "Factor returns"), ("factor_risk", "Factor risk"),
         ("exposures", "Exposures"), ("specific_risk", "Specific risk"), ("portfolio", "Portfolio analyzer"),
         ("validation", "Validation"), ("optimizer", "Optimizer")]

st.navigation([st.Page(f"views/{name}.py", title=title, url_path=name) for name, title in PAGES]).run()
