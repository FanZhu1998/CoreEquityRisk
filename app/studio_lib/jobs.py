"""Background jobs for the Studio. Each action runs one `eqrisk` process with its output in
logs/studio/<id>.log and its status in logs/studio/<id>.json. Only one job runs at a time, because
the pipeline's stages write the same tables. A job keeps running if the Studio window is closed; the
next Studio start picks it up again from its status file."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STILL_ACTIVE = 259                      # Windows GetExitCodeProcess value for a live process

# Steps of `eqrisk run-daily`, each recognised by the first log line of that step.
DAILY_STEPS: list[tuple[str, tuple[str, ...]]] = [
    ("Wait for data", ("waiting for vendor",)),
    ("Ingest", ("ingested",)),
    ("Stage", ("staging", "security master", "fundamentals staged")),
    ("Exposures", ("descriptors",)),
    ("Factor model", ("exposures",)),     # regression, covariance and specific risk follow this line
    ("Gates", ("notify",)),
]


def daily_progress(log_text: str) -> tuple[int, list[str]]:
    """Index of the latest step whose marker appears in the log (0 if none yet), and the step names."""
    reached = 0
    for i, (_, markers) in enumerate(DAILY_STEPS):
        if any(m in log_text for m in markers):
            reached = i
    return reached, [name for name, _ in DAILY_STEPS]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == _STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@dataclass
class Job:
    id: str
    label: str
    args: list[str]
    started: str
    log: str
    pid: int
    status: str = "running"               # running | succeeded | failed | stopped | ended
    finished: str | None = None
    returncode: int | None = None

    @property
    def seconds(self) -> float:
        end = datetime.fromisoformat(self.finished) if self.finished else datetime.now()
        return (end - datetime.fromisoformat(self.started)).total_seconds()


class JobRunner:
    """Owns the Studio's background `eqrisk` process (one per Studio server)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "logs" / "studio"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen[bytes] | None = None
        self.job: Job | None = None
        for job in self.history(limit=5):                 # a job left running by an earlier Studio session
            if job.status == "running":
                self.job = job
                break

    # ---- state -----------------------------------------------------------------------------
    def _save(self, job: Job) -> None:
        (self.dir / f"{job.id}.json").write_text(json.dumps(asdict(job), indent=1), encoding="utf-8")

    def poll(self) -> None:
        job = self.job
        if job is None or job.status != "running":
            return
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is None:
                return
            job.status, job.returncode = ("succeeded" if rc == 0 else "failed"), rc
            self.proc = None
        elif pid_alive(job.pid):
            return
        else:
            job.status = "ended"                          # started by an earlier session; exit code unknown
        job.finished = _now()
        self._save(job)

    def active(self) -> Job | None:
        self.poll()
        return self.job if self.job is not None and self.job.status == "running" else None

    def current(self) -> Job | None:
        """The running job, else the most recent one."""
        self.poll()
        if self.job is not None:
            return self.job
        recent = self.history(limit=1)
        return recent[0] if recent else None

    def history(self, limit: int = 50) -> list[Job]:
        jobs = []
        for path in sorted(self.dir.glob("*.json"), reverse=True)[:limit]:
            try:
                jobs.append(Job(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
        return jobs

    # ---- actions ---------------------------------------------------------------------------
    def start(self, label: str, args: list[str], command: list[str] | None = None) -> Job:
        """Run `eqrisk <args>` (or `command`, for tests) in the project folder."""
        if self.active() is not None:
            raise RuntimeError("another job is still running")
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        log = self.dir / f"{job_id}.log"
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        argv = command or [sys.executable, "-m", "eqrisk.cli", *args]
        with open(log, "wb") as out:
            self.proc = subprocess.Popen(argv, cwd=self.root, stdout=out, stderr=subprocess.STDOUT, env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.job = Job(id=job_id, label=label, args=args, started=_now(), log=str(log), pid=self.proc.pid)
        self._save(self.job)
        return self.job

    def stop(self) -> None:
        job = self.active()
        if job is None:
            return
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=30)
            self.proc = None
        else:
            subprocess.run(["taskkill", "/PID", str(job.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job.status, job.finished = "stopped", _now()
        self._save(job)

    # ---- output ----------------------------------------------------------------------------
    @staticmethod
    def text(job: Job) -> str:
        try:
            raw = Path(job.log).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""
        return _ANSI.sub("", raw).replace("\r\n", "\n")

    def tail(self, job: Job, lines: int = 40) -> str:
        rows = [r for r in self.text(job).split("\n") if r.strip()]
        return "\n".join(rows[-lines:])
