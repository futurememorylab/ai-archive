import io
import json
import logging

from backend.app.logging_setup import configure_logging


def test_emits_structured_json(monkeypatch):
    stream = io.StringIO()
    configure_logging(stream=stream, level="INFO")
    logger = logging.getLogger("test")
    logger.info("hello", extra={"job_id": 42, "clip_id": 7})

    line = stream.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "hello"
    assert record["job_id"] == 42
    assert record["clip_id"] == 7
    assert record["levelname"] == "INFO"
