"""Typed runtime configuration loaded once during application startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import IPv6Address
from pathlib import Path
from urllib.parse import quote, urlsplit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DB_POOL_MAX = 5
DEFAULT_QUERY_TIMEOUT_MS = 30_000
DEFAULT_OUTPUT_DIR = Path("/app/outputs")
DEFAULT_LOG_LEVEL = "INFO"
MIN_MASTER_KEY_LENGTH = 32
MAX_DB_POOL_SIZE = 20
ALLOWED_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
SPLIT_DATABASE_VARIABLES = (
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USERNAME",
    "DB_PASSWORD",
)


class ConfigurationError(ValueError):
    """Raised when runtime configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class Settings:
    master_key: str = field(repr=False)
    database_url: str = field(repr=False)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_pool_max: int = DEFAULT_DB_POOL_MAX
    query_timeout_ms: int = DEFAULT_QUERY_TIMEOUT_MS
    output_dir: Path = DEFAULT_OUTPUT_DIR
    log_level: str = DEFAULT_LOG_LEVEL
    database_redaction_values: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        raw_master_key = values.get("MCP_MASTER_KEY", "")
        master_key = _required(values, "MCP_MASTER_KEY")
        host = _optional(values, "MCP_HOST", DEFAULT_HOST)
        output_dir = Path(_optional(values, "OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
        log_level = _optional(values, "LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()

        errors: list[str] = []
        database_url, database_redaction_values = _database_configuration(values, errors)
        if len(master_key) < MIN_MASTER_KEY_LENGTH:
            errors.append(f"MCP_MASTER_KEY must be at least {MIN_MASTER_KEY_LENGTH} characters")
        if (
            raw_master_key != master_key
            or not master_key.isascii()
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in master_key)
        ):
            errors.append("MCP_MASTER_KEY must contain only visible ASCII characters")
        if not host or any(character.isspace() for character in host):
            errors.append("MCP_HOST must be a non-empty host without whitespace")
        if not output_dir.is_absolute():
            errors.append("OUTPUT_DIR must be an absolute path")
        if log_level not in ALLOWED_LOG_LEVELS:
            errors.append(f"LOG_LEVEL must be one of {', '.join(sorted(ALLOWED_LOG_LEVELS))}")

        port = _integer(values, "MCP_PORT", DEFAULT_PORT, minimum=1, maximum=65_535, errors=errors)
        pool_max = _integer(
            values,
            "DB_POOL_MAX",
            DEFAULT_DB_POOL_MAX,
            minimum=1,
            maximum=MAX_DB_POOL_SIZE,
            errors=errors,
        )
        timeout_ms = _integer(
            values,
            "QUERY_TIMEOUT_MS",
            DEFAULT_QUERY_TIMEOUT_MS,
            minimum=1,
            maximum=3_600_000,
            errors=errors,
        )

        if errors:
            raise ConfigurationError("Invalid configuration: " + "; ".join(errors))

        return cls(
            master_key=master_key,
            database_url=database_url,
            host=host,
            port=port,
            db_pool_max=pool_max,
            query_timeout_ms=timeout_ms,
            output_dir=output_dir,
            log_level=log_level,
            database_redaction_values=database_redaction_values,
        )

    @property
    def log_redaction_values(self) -> tuple[str, ...]:
        """Return every configured secret/raw database component for log filtering."""
        return (self.master_key, self.database_url, *self.database_redaction_values)

    def prepare_output_dir(self) -> None:
        """Create and verify the shared output directory without logging its contents."""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError("OUTPUT_DIR cannot be created") from exc
        if not self.output_dir.is_dir():
            raise ConfigurationError("OUTPUT_DIR is not a directory")
        if not os.access(self.output_dir, os.W_OK):
            raise ConfigurationError("OUTPUT_DIR is not writable")


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required configuration: {name}")
    return value


def _optional(values: Mapping[str, str], name: str, default: str) -> str:
    return values.get(name, "").strip() or default


def _database_configuration(values: Mapping[str, str], errors: list[str]) -> tuple[str, tuple[str, ...]]:
    database_url = values.get("DATABASE_URL", "").strip()
    split_values = {name: values.get(name, "") for name in SPLIT_DATABASE_VARIABLES}
    configured_split_values = tuple(value for value in split_values.values() if value.strip())

    if database_url and configured_split_values:
        errors.append("configure either DATABASE_URL or the complete DB_* set, not both")
        return database_url, configured_split_values
    if database_url:
        if not _is_postgresql_url(database_url):
            errors.append("DATABASE_URL must be a postgresql:// or postgres:// URL")
        return database_url, ()
    if not configured_split_values:
        errors.append("configure exactly one database mode: DATABASE_URL or the complete DB_* set")
        return "", ()
    if any(not split_values[name].strip() for name in SPLIT_DATABASE_VARIABLES):
        errors.append("split database configuration requires all five DB_* variables")
        return "", configured_split_values

    database_port = _required_database_port(split_values["DB_PORT"], errors)
    database_host = _format_database_host(split_values["DB_HOST"], errors)
    if database_port is None or database_host is None:
        return "", tuple(split_values.values())

    username = quote(split_values["DB_USERNAME"], safe="")
    password = quote(split_values["DB_PASSWORD"], safe="")
    database = quote(split_values["DB_NAME"], safe="")
    assembled_url = f"postgresql://{username}:{password}@{database_host}:{database_port}/{database}"
    return assembled_url, tuple(split_values.values())


def _required_database_port(raw: str, errors: list[str]) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        errors.append("DB_PORT must be an integer from 1 through 65535")
        return None
    if not 1 <= port <= 65_535:
        errors.append("DB_PORT must be an integer from 1 through 65535")
        return None
    return port


def _format_database_host(raw: str, errors: list[str]) -> str | None:
    if raw != raw.strip() or any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in raw
    ):
        errors.append("DB_HOST must be a valid nonblank hostname or IP address")
        return None

    literal = raw
    if raw.startswith("[") or raw.endswith("]"):
        if not (raw.startswith("[") and raw.endswith("]")):
            errors.append("DB_HOST must be a valid nonblank hostname or IP address")
            return None
        literal = raw[1:-1]
    if ":" in literal:
        if "%" in literal:
            errors.append("DB_HOST must be a valid nonblank hostname or IP address")
            return None
        try:
            address = IPv6Address(literal)
        except ValueError:
            errors.append("DB_HOST must be a valid nonblank hostname or IP address")
            return None
        return f"[{address}]"
    if any(character in raw for character in "%@/?#[]:"):
        errors.append("DB_HOST must be a valid nonblank hostname or IP address")
        return None
    return raw


def _integer(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    errors: list[str],
) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return default
    if not minimum <= value <= maximum:
        errors.append(f"{name} must be between {minimum} and {maximum}")
    return value


def _is_postgresql_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"postgres", "postgresql"} and bool(parsed.path.strip("/"))
