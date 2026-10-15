"""
logging_config.py — Structured logging with structlog

- Dev:  easy to read (colorized console)
- Prod: JSON (parseable by log aggregators like Datadog, Sentry)
- Replaces all print() calls
"""
import logging
import sys

import structlog

from core.config import settings


def setup_logging() -> None:
    """Configure logging — call once at app start."""

    # ---------- Log level ----------
    log_level = getattr(logging, settings.LOG_LEVEL)

    # ---------- Standard logging (for libraries) ----------
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # ---------- Reduce library noise ----------
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # ---------- structlog processors ----------
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.LOG_FORMAT == "json" or settings.is_production:
        # Production: JSON
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: colorized
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__):
    """Helper to get a logger."""
    return structlog.get_logger(name)