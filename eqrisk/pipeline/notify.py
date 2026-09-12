"""Run notifications (blueprint §13.2 step 8): a Windows desktop toast, and always a log line."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

from eqrisk.config import Project
from eqrisk.log import get_logger

log = get_logger(__name__)

# Windows PowerShell's registered AppUserModelID, so the toast shows without installing anything.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
# The text arrives through environment variables, never through the command line.
_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]"
    " > $null;"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "$t.Item(0).AppendChild($x.CreateTextNode($env:EQRISK_TITLE)) > $null;"
    "$t.Item(1).AppendChild($x.CreateTextNode($env:EQRISK_BODY)) > $null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:EQRISK_APP)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)
_TOAST_BODY_MAX = 250            # characters a toast shows before truncating


def notify(project: Project, title: str, body: str) -> str:
    """Record the run summary in logs/notifications.log and, when configured on Windows, show a
    toast. Returns the channel used. Never raises: a failed toast is logged and ignored."""
    path = project.logs_dir / "notifications.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')} {title}: {body}\n")
    log.info("notify", title=title, body=body)
    if project.config.pipeline.notify != "toast" or sys.platform != "win32":
        return "log"
    env = {**os.environ, "EQRISK_TITLE": title, "EQRISK_BODY": body[:_TOAST_BODY_MAX], "EQRISK_APP": _APP_ID}
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST], env=env, timeout=30,
                       check=True, capture_output=True)
        return "toast"
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("toast failed", error=str(exc))
        return "log"
