"""structlog setup (blueprint §3.3): human-readable console on stderr, JSON lines in logs/."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

_SHARED: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_logging(logs_dir: Path | None = None, level: str = "INFO") -> None:
    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.dev.ConsoleRenderer(colors=False)],
        foreign_pre_chain=_SHARED))
    root.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logs_dir / "eqrisk.jsonl", encoding="utf-8")
        fh.setFormatter(structlog.stdlib.ProcessorFormatter(
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()],
            foreign_pre_chain=_SHARED))
        root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
