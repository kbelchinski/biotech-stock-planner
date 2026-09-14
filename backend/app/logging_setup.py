"""Console logging for the scan flow. Never attach credentials to log records."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

LOGGER_NAME = "catalyst_screener"

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
_DATE = "%H:%M:%S"


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


def configure_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(numeric)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT, _DATE))
        logger.addHandler(handler)
    logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def format_url(url: str, params: dict[str, object] | None = None) -> str:
    if not params:
        return url
    return f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
