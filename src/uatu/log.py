"""Structured logging: JSON lines on stdout (or coloured console output for local development)."""

import logging
import sys
from typing import Literal

import structlog


def configure_logging(level: str = "INFO", fmt: Literal["json", "console"] = "json") -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if fmt == "console":
        render: list[structlog.typing.Processor] = [structlog.dev.ConsoleRenderer()]
    else:
        render = [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=[*shared, *render],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route warnings from libraries (websockets, SQLAlchemy...) through the same renderer.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared],
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *render],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.WARNING)
