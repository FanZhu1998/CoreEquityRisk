"""EQRisk Studio: the background job runner, and every page renders on the development data."""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

from studio_lib.jobs import DAILY_STEPS, JobRunner, daily_progress  # noqa: E402

PAGES = ["studio_pages/home.py", "studio_pages/data.py", "studio_pages/estimate.py", "studio_pages/validate.py",
         "studio_pages/publish.py", "studio_pages/system.py", "views/factor_returns.py", "views/factor_risk.py",
         "views/exposures.py", "views/specific_risk.py", "views/portfolio.py", "views/optimizer.py"]


def _wait(runner, timeout=30.0):
    t0 = time.time()
    while runner.active() is not None and time.time() - t0 < timeout:
        time.sleep(0.1)


def test_one_job_at_a_time_and_its_log_and_status_are_kept(tmp_path):
    r = JobRunner(tmp_path)
    job = r.start("demo", ["demo"], command=[sys.executable, "-c", "import time; print('staging'); time.sleep(1)"])
    with pytest.raises(RuntimeError):
        r.start("second", ["demo"], command=[sys.executable, "-c", "pass"])
    _wait(r)
    done = r.current()
    assert done.id == job.id and done.status == "succeeded" and done.returncode == 0
    assert "staging" in r.tail(done)
    again = JobRunner(tmp_path)                       # a later Studio session sees the finished job
    assert [j.id for j in again.history()] == [job.id] and again.active() is None


def test_failed_and_stopped_jobs_are_recorded(tmp_path):
    r = JobRunner(tmp_path)
    r.start("bad", ["bad"], command=[sys.executable, "-c", "raise SystemExit(3)"])
    _wait(r)
    assert r.current().status == "failed" and r.current().returncode == 3
    r.start("long", ["long"], command=[sys.executable, "-c", "import time; time.sleep(60)"])
    r.stop()
    assert r.current().status == "stopped" and r.active() is None


def test_daily_progress_follows_the_log():
    names = [n for n, _ in DAILY_STEPS]
    assert daily_progress("")[0] == 0
    assert names[daily_progress("... ingested ... staging ... descriptors ...")[0]] == "Exposures"
    assert names[daily_progress("ingested staging descriptors exposures notify")[0]] == "Gates"


@pytest.mark.golden
def test_every_studio_page_renders():
    if not (ROOT / "data" / "model").exists():
        pytest.skip("no model tables")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app" / "studio.py"), default_timeout=600)
    at.run()
    assert not at.exception, at.exception
    for page in PAGES:
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)
