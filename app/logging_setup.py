"""Structured JSON logging configuration.

Every request emits one JSON line with: trace_id, store_id, endpoint, method,
latency_ms, event_count (ingest only), status_code — the fields an on-call engineer
greps for.
"""
from __future__ import annotations

import logging
import sys

from pythonjsonlogger import json as jsonlogger

from .config import get_settings


def configure_logging() -> logging.Logger:
    settings = get_settings()
    logger = logging.getLogger("store_intelligence")
    if logger.handlers:  # idempotent under reload / repeated imports
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level"},
        )
    )
    logger.addHandler(handler)
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False
    return logger


LOG = configure_logging()
