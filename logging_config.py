"""
logging_config.py — Structured logging ด้วย structlog

- Dev: อ่านง่าย (colorized console)
- Prod: JSON (parse ได้ด้วย log aggregator เช่น Datadog, Sentry)
- แทน print() ทั้งหมด
"""
import logging
import sys

import structlog

from config import settings


def setup_logging() -> None:
    """ตั้งค่า logging — เรียกครั้งเดียวตอน app start"""

    # ---------- Log level ----------
    log_level = getattr(logging, settings.LOG_LEVEL)

    # ---------- Standard logging (ให้ library ใช้) ----------
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # ---------- ลด noise จาก library ----------
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
    """Helper สำหรับดึง logger"""
    return structlog.get_logger(name)