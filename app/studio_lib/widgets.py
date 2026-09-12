"""Shared Studio widgets: the live job panel and the buttons that start jobs."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from studio_lib import theme
from studio_lib.jobs import Job, daily_progress
from studio_lib.state import missing_required_keys, runner, scheduled_job_running

REFRESH_S = 2


def busy_reason(needs_keys: bool = False) -> str | None:
    """Why a new job cannot start right now, or None."""
    if runner().active() is not None:
        return "A job is already running; its progress is shown on this page."
    if scheduled_job_running():
        return "The scheduled EQRisk Daily job is running right now; wait for it to finish."
    if needs_keys and (missing := missing_required_keys()):
        return "Missing in .env: " + ", ".join(missing)
    return None


def start(label: str, args: list[str]) -> None:
    try:
        runner().start(label, args)
    except RuntimeError as exc:
        st.warning(str(exc))
        return
    st.rerun()


def job_button(label: str, args: list[str], *, key: str, primary: bool = False, needs_keys: bool = False,
               help: str | None = None, job_label: str | None = None, icon: str | None = None) -> None:
    """A button that starts `eqrisk <args>` in the background, disabled while anything else runs."""
    reason = busy_reason(needs_keys)
    if st.button(label, key=key, type="primary" if primary else "secondary", disabled=reason is not None,
                 help=reason or help, icon=icon):
        start(job_label or label, args)


def confirm_dialog(title: str, body_html: str, on_confirm: Callable[[], object]) -> None:
    """Ask before a long or destructive action; `on_confirm`'s return value is ignored."""
    @st.dialog(title)
    def _dialog() -> None:
        theme.note(body_html)
        c1, c2 = st.columns(2)
        if c1.button("Cancel", key=f"cancel-{title}", width="stretch"):
            st.rerun()
        if c2.button("Continue", key=f"ok-{title}", type="primary", width="stretch"):
            on_confirm()
            st.rerun()

    _dialog()


def _elapsed(job: Job) -> str:
    s = int(job.seconds)
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"


def job_panel(title: str = "Background job") -> None:
    """Live progress of the running job (refreshing every few seconds), else the last job's result."""
    r = runner()
    live = r.active() is not None

    @st.fragment(run_every=REFRESH_S if live else None)
    def _panel() -> None:
        job = r.current()
        if job is None:
            return
        text = r.text(job)
        running = job.status == "running"
        seen = st.session_state.setdefault("eq_seen_jobs", set())
        if not running and job.id not in seen:
            seen.add(job.id)
            if live:                                   # it just finished: refresh every cached read
                st.cache_data.clear()
                st.rerun(scope="app")
        head = f"{job.label} · {theme.pill(job.status)} · {_elapsed(job)}"
        st.html(f'<div class="eq-h2">{head}</div>')
        if job.args[:1] == ["run-daily"]:
            idx, names = daily_progress(text)
            theme.steps(names, idx if running else len(names) - 1, finished=job.status == "succeeded")
        elif running:
            st.progress(0.5, text="Working… (this step reports only when it finishes)")
        if running:
            c1, c2 = st.columns([1, 5])
            if c1.button("Stop job", key=f"stop-{job.id}"):
                r.stop()
                st.rerun(scope="app")
            c2.caption(f"Started {job.started.replace('T', ' ')} · `eqrisk {' '.join(job.args)}` · log: {job.log}")
        elif job.status == "succeeded":
            st.success(f"Finished in {_elapsed(job)}.", icon=":material/check_circle:")
        elif job.status in ("failed", "stopped", "ended"):
            st.error(f"{job.status.capitalize()} after {_elapsed(job)}. The log below shows why.",
                     icon=":material/error:")
        with st.expander("Log", expanded=running or job.status == "failed"):
            st.code(r.tail(job, 60) or "(no output yet)", language=None)

    _panel()
