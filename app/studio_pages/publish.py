"""Publish: the static offline viewer, snapshots for notebooks, and monthly compaction."""

import os
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

import streamlit as st
from common import ROOT
from studio_lib import theme
from studio_lib.state import project, summary
from studio_lib.widgets import job_button, job_panel

VIEWER_PORT = 8000
s = summary()
p = project()
site = p.resolve(p.config.outputs.export_site.out)
exports = p.data_dir / "exports"
exports.mkdir(parents=True, exist_ok=True)                # snapshot files land here (data/ is git-ignored)

theme.section("System", "Publish &amp; <em>export</em>",
              "Share results without the Studio: a self-contained viewer of model outputs (no vendor prices or "
              "fundamentals), or a snapshot file of exposures, factor covariance and specific variances.")


@st.cache_resource
def viewer_proc() -> dict[str, subprocess.Popen[bytes] | None]:
    return {"proc": None}


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


with st.container(border=True):
    theme.h2("Static viewer")
    exported = site.exists() and (site / "data" / "snapshot.json").exists()
    size = sum(f.stat().st_size for f in site.rglob("*") if f.is_file()) / 1e6 if exported else 0
    when = date.fromtimestamp((site / "data" / "snapshot.json").stat().st_mtime) if exported else None
    status = f"{size:.2f} MB, exported {when}" if exported else "not exported yet"
    theme.note(f"Folder <b>{theme.esc(site)}</b> · {status}")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        job_button("Export viewer", ["export-site"], key="export", primary=True, job_label="Export static viewer",
                   icon=":material/ios_share:")
    serving = port_open(VIEWER_PORT)
    with c2:
        if not serving and st.button("Serve locally", disabled=not exported, key="serve", icon=":material/lan:"):
            argv = [sys.executable, "-m", "http.server", str(VIEWER_PORT), "--bind", "127.0.0.1",
                    "--directory", str(site)]
            viewer_proc()["proc"] = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL,
                                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            st.rerun()
        if serving:
            st.link_button("Open viewer", f"http://127.0.0.1:{VIEWER_PORT}/", icon=":material/open_in_new:")
    with c3:
        proc = viewer_proc()["proc"]
        if proc is not None and proc.poll() is None and st.button("Stop serving", key="unserve"):
            proc.terminate()
            viewer_proc()["proc"] = None
            st.rerun()
    with c4:
        if exported and os.name == "nt" and st.button("Open folder", key="open-site", icon=":material/folder_open:"):
            os.startfile(site)
    theme.note(f"Served on this computer only (127.0.0.1:{VIEWER_PORT}). To host it privately elsewhere, copy the "
               "folder; avoid public hosting of licensed data (blueprint §15.2).")

with st.container(border=True):
    theme.h2("Snapshot for notebooks and optimizers")
    last = s["last"]
    c1, c2 = st.columns([2, 3])
    d = c1.date_input("As of", value=last, max_value=last, key="snap-date") if last else None
    out = exports / f"snapshot_{d}.npz" if d else None
    with c2:
        st.html("<div style='height:1.7rem'></div>")
        if d is not None:
            job_button("Write snapshot", ["snapshot", "--date", str(d), "--out", str(out)], key="snap",
                       job_label=f"Snapshot {d}", icon=":material/save:")
    if out is not None and out.exists():
        st.download_button(f"Download {out.name}", out.read_bytes(), file_name=out.name, icon=":material/download:")
        theme.note("Load it with <code>RiskModelSnapshot.load(path)</code>: X, F (monthly), specific variances, "
                   "market caps and the estimation-universe flag.")

with st.container(border=True):
    theme.h2("Monthly compaction")
    theme.note("Daily sessions are stored one file per day; after a month ends, merge them into one file per table. "
               "Safe to repeat.")
    today = date.today()
    prev = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    month = st.text_input("Month (YYYY-MM)", value=f"{prev:%Y-%m}", key="compact-month")
    job_button("Compact month", ["compact", "--month", month], key="compact", job_label=f"Compact {month}",
               icon=":material/compress:")

job_panel()
_ = Path  # keep the import for type readers
