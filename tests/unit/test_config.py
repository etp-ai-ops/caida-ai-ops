from __future__ import annotations

from pathlib import Path

import pytest

from caida_ai_ops.itdk.config import ConfigurationError, Settings
from tests.conftest import TEST_DATABASE_URL, TEST_MASTER_KEY


def valid_env(tmp_path: Path) -> dict[str, str]:
    return {
        "MCP_MASTER_KEY": TEST_MASTER_KEY,
        "DATABASE_URL": TEST_DATABASE_URL,
        "OUTPUT_DIR": str(tmp_path / "outputs"),
    }


def valid_split_env(tmp_path: Path, *, host: str = "db.internal") -> dict[str, str]:
    environ = valid_env(tmp_path)
    del environ["DATABASE_URL"]
    environ.update(
        DB_HOST=host,
        DB_PORT="5432",
        DB_NAME="caida_itdk",
        DB_USERNAME="itdk_reader",
        DB_PASSWORD="test-password",
    )
    return environ


def test_defaults_and_normalization(tmp_path: Path) -> None:
    settings = Settings.from_env(valid_env(tmp_path))
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.db_pool_max == 5
    assert settings.query_timeout_ms == 30_000
    assert settings.log_level == "INFO"


def test_database_url_mode_is_preserved(tmp_path: Path) -> None:
    environ = valid_env(tmp_path)
    environ["DATABASE_URL"] = "postgres://reader:password@db.internal:5432/caida_itdk"
    assert Settings.from_env(environ).database_url == environ["DATABASE_URL"]


@pytest.mark.parametrize(
    ("host", "url_host"),
    [
        ("db.internal", "db.internal"),
        ("192.0.2.10", "192.0.2.10"),
        ("2001:db8::10", "[2001:db8::10]"),
        ("[2001:db8::10]", "[2001:db8::10]"),
    ],
)
def test_split_database_mode_supports_dns_ipv4_and_ipv6(tmp_path: Path, host: str, url_host: str) -> None:
    settings = Settings.from_env(valid_split_env(tmp_path, host=host))
    assert settings.database_url == f"postgresql://itdk_reader:test-password@{url_host}:5432/caida_itdk"


def test_split_database_mode_percent_encodes_url_components(tmp_path: Path) -> None:
    environ = valid_split_env(tmp_path)
    environ.update(
        DB_USERNAME="reader/name@example",
        DB_PASSWORD="p@ss:word/?#%",
        DB_NAME="caida/data set",
    )
    settings = Settings.from_env(environ)
    assert settings.database_url == (
        "postgresql://reader%2Fname%40example:p%40ss%3Aword%2F%3F%23%25@db.internal:5432/caida%2Fdata%20set"
    )


@pytest.mark.parametrize("missing", ["MCP_MASTER_KEY", "DATABASE_URL"])
def test_required_configuration_fails_closed(tmp_path: Path, missing: str) -> None:
    environ = valid_env(tmp_path)
    del environ[missing]
    with pytest.raises(ConfigurationError, match=missing):
        Settings.from_env(environ)


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD"])
def test_partial_split_database_configuration_is_rejected(tmp_path: Path, missing: str) -> None:
    environ = valid_split_env(tmp_path)
    del environ[missing]
    with pytest.raises(ConfigurationError, match="requires all five DB_\\*"):
        Settings.from_env(environ)


def test_url_and_split_database_configuration_conflict(tmp_path: Path) -> None:
    environ = valid_split_env(tmp_path)
    environ["DATABASE_URL"] = TEST_DATABASE_URL
    with pytest.raises(ConfigurationError, match="not both") as raised:
        Settings.from_env(environ)
    assert TEST_DATABASE_URL not in str(raised.value)
    assert environ["DB_USERNAME"] not in str(raised.value)
    assert environ["DB_PASSWORD"] not in str(raised.value)


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_split_database_port_is_rejected(tmp_path: Path, port: str) -> None:
    environ = valid_split_env(tmp_path)
    environ["DB_PORT"] = port
    with pytest.raises(ConfigurationError, match="DB_PORT.*1 through 65535") as raised:
        Settings.from_env(environ)
    assert port not in str(raised.value)


@pytest.mark.parametrize("name", ["DB_HOST", "DB_NAME", "DB_USERNAME", "DB_PASSWORD"])
def test_blank_split_database_component_is_rejected(tmp_path: Path, name: str) -> None:
    environ = valid_split_env(tmp_path)
    environ[name] = "   "
    with pytest.raises(ConfigurationError, match="requires all five DB_\\*"):
        Settings.from_env(environ)


def test_split_validation_errors_do_not_expose_raw_values(tmp_path: Path) -> None:
    environ = valid_split_env(tmp_path, host="bad host.example")
    environ.update(DB_USERNAME="private-user", DB_PASSWORD="private-password", DB_NAME="private-db")
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env(environ)
    rendered = str(raised.value)
    for raw_value in ("bad host.example", "private-user", "private-password", "private-db"):
        assert raw_value not in rendered


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MCP_MASTER_KEY", "short", "at least 32"),
        ("MCP_MASTER_KEY", "x" * 31 + " ", "visible ASCII"),
        ("MCP_MASTER_KEY", "x" * 31 + "é", "visible ASCII"),
        ("DATABASE_URL", "mysql://host/db", "DATABASE_URL"),
        ("DATABASE_URL", "postgresql://host", "DATABASE_URL"),
        ("MCP_HOST", "bad host", "MCP_HOST"),
        ("MCP_PORT", "0", "MCP_PORT"),
        ("MCP_PORT", "65536", "MCP_PORT"),
        ("DB_POOL_MAX", "0", "DB_POOL_MAX"),
        ("DB_POOL_MAX", "21", "DB_POOL_MAX"),
        ("QUERY_TIMEOUT_MS", "0", "QUERY_TIMEOUT_MS"),
        ("QUERY_TIMEOUT_MS", "3600001", "QUERY_TIMEOUT_MS"),
        ("QUERY_TIMEOUT_MS", "nope", "QUERY_TIMEOUT_MS"),
        ("OUTPUT_DIR", "relative", "OUTPUT_DIR"),
        ("LOG_LEVEL", "TRACE", "LOG_LEVEL"),
    ],
)
def test_invalid_configuration_is_rejected(tmp_path: Path, name: str, value: str, message: str) -> None:
    environ = valid_env(tmp_path)
    environ[name] = value
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env(environ)


def test_explicit_configuration_is_parsed(tmp_path: Path) -> None:
    environ = valid_env(tmp_path)
    environ.update(
        MCP_HOST="localhost",
        MCP_PORT="9000",
        DB_POOL_MAX="20",
        QUERY_TIMEOUT_MS="42",
        LOG_LEVEL="debug",
    )
    settings = Settings.from_env(environ)
    assert (settings.host, settings.port, settings.db_pool_max) == ("localhost", 9000, 20)
    assert settings.query_timeout_ms == 42
    assert settings.log_level == "DEBUG"


def test_output_directory_is_created_and_writable(tmp_path: Path) -> None:
    settings = Settings.from_env(valid_env(tmp_path))
    settings.prepare_output_dir()
    assert settings.output_dir.is_dir()


def test_output_path_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")
    environ = valid_env(tmp_path)
    environ["OUTPUT_DIR"] = str(output)
    with pytest.raises(ConfigurationError, match="cannot be created|not a directory"):
        Settings.from_env(environ).prepare_output_dir()
