from __future__ import annotations

import json
import logging
from pathlib import Path

from caida_ai_ops.itdk.config import Settings
from caida_ai_ops.itdk.structured_logging import REDACTED, JsonFormatter, redact
from tests.conftest import TEST_MASTER_KEY


def test_nested_sensitive_fields_and_patterns_are_redacted() -> None:
    session_id = "123e4567-e89b-42d3-a456-426614174000"
    value = {
        "authorization": "Bearer top-secret",
        "nested": {"password": "hunter2", "safe": f"session={session_id}"},
        "items": ["postgresql://user:secret@db/example", "Bearer hidden"],
    }
    redacted = redact(value)
    rendered = json.dumps(redacted)
    for secret in ("top-secret", "hunter2", session_id, "secret@", "hidden"):
        assert secret not in rendered
    assert REDACTED in rendered


def test_formatter_redacts_registered_literals_in_message_and_context() -> None:
    secret = "literal-secret-value"
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "failed with %s",
        (secret,),
        None,
    )
    record.event = "failure"
    record.context = {"safe": secret, "token": "another-secret"}
    payload = json.loads(JsonFormatter((secret,)).format(record))
    assert secret not in json.dumps(payload)
    assert payload["context"] == {"safe": REDACTED, "token": REDACTED}
    assert set(payload) == {"timestamp", "level", "logger", "message", "event", "context"}


def test_split_database_values_are_registered_for_literal_redaction(tmp_path: Path) -> None:
    raw_values = {
        "DB_HOST": "db.private.internal",
        "DB_PORT": "55432",
        "DB_NAME": "private_database",
        "DB_USERNAME": "private_username",
        "DB_PASSWORD": "private_password",
    }
    settings = Settings.from_env(
        {
            "MCP_MASTER_KEY": TEST_MASTER_KEY,
            "OUTPUT_DIR": str(tmp_path),
            **raw_values,
        }
    )
    message = " ".join((*raw_values.values(), settings.database_url))
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, message, (), None)
    rendered = JsonFormatter(settings.log_redaction_values).format(record)
    settings_repr = repr(settings)
    for raw_value in (*raw_values.values(), settings.database_url, TEST_MASTER_KEY):
        assert raw_value not in rendered
        assert raw_value not in settings_repr
    assert REDACTED in rendered


def test_split_database_field_names_are_sensitive() -> None:
    redacted = redact(
        {
            "db_username": "private_username",
            "db_password": "private_password",
            "username": "another_username",
        }
    )
    assert redacted == {
        "db_username": REDACTED,
        "db_password": REDACTED,
        "username": REDACTED,
    }
