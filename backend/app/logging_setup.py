"""JSON logging setup — installs a `python-json-logger` JsonFormatter on
the root logger. Called once from `main.py` at process start."""

import logging
import sys
from typing import IO

from pythonjsonlogger.json import JsonFormatter


def configure_logging(stream: IO[str] | None = None, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts"},
        )
    )
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")
