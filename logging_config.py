import json
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

DEFAULT_RUN_ID = os.getenv("RUN_ID", str(uuid.uuid4()))
RESERVED_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


def infer_service_name() -> str:
    if os.getenv("SERVICE_NAME"):
        return os.environ["SERVICE_NAME"]
    executable = os.path.basename(sys.argv[0])
    if executable == "spotify.py":
        return "notifier"
    return "server"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, run_id: str) -> None:
        super().__init__()
        self.service = service
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": getattr(record, "service", self.service),
            "event": getattr(record, "event", None),
            "run_id": getattr(record, "run_id", self.run_id),
        }

        for key, value in record.__dict__.items():
            if key in RESERVED_RECORD_ATTRS or key in payload or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value) if exc_value else None,
                "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
            }

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(service: str | None = None, run_id: str | None = None) -> str:
    service_name = service or infer_service_name()
    current_run_id = run_id or DEFAULT_RUN_ID
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, current_run_id))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level, logging.INFO))

    logging.getLogger("discord").setLevel(os.getenv("DISCORD_LOG_LEVEL", "WARNING").upper())
    logging.getLogger("werkzeug").setLevel(os.getenv("WERKZEUG_LOG_LEVEL", "INFO").upper())
    return current_run_id


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
