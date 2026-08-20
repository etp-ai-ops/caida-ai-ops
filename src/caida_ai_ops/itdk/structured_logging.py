"""JSON logging configured to redact secret-bearing fields and text."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "db_name",
        "db_password",
        "db_username",
        "database_url",
        "mcp_master_key",
        "password",
        "secret",
        "set-cookie",
        "token",
        "username",
    }
)
BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
POSTGRESQL_CREDENTIAL_PATTERN = re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)")
UUID_PATTERN = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")


def redact(value: Any, *, field_name: str | None = None) -> Any:
    """Return a log-safe representation of nested structured data."""
    if field_name is not None and field_name.lower() in SENSITIVE_FIELD_NAMES:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(key): redact(item, field_name=str(key)) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = BEARER_PATTERN.sub(rf"\1{REDACTED}", value)
        value = POSTGRESQL_CREDENTIAL_PATTERN.sub(rf"\1{REDACTED}\2", value)
        return UUID_PATTERN.sub(REDACTED, value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Emit one compact JSON object per record."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = redact(event)
        context = getattr(record, "context", None)
        if context is not None:
            payload["context"] = redact(context)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(
            _redact_registered_secrets(payload, self._secrets),
            separators=(",", ":"),
            ensure_ascii=False,
        )


def configure_logging(level: str, *, secrets: tuple[str, ...] = ()) -> None:
    """Install the process-wide structured handler once at startup."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # The SDK's DEBUG/INFO messages can include complete protocol messages and
    # its warnings can include caller-supplied tool names. Service-owned logs
    # below the MCP boundary use only the sanitized stable context contract.
    logging.getLogger("mcp").setLevel(logging.CRITICAL)


def _redact_registered_secrets(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        return {key: _redact_registered_secrets(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_registered_secrets(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
    return value
