from __future__ import annotations

import asyncio
import csv
import os
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import psycopg
import pytest
import pytest_asyncio
import uvicorn
from docker.errors import DockerException
from httpx2 import AsyncClient as MCPAsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from psycopg import sql
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.exceptions import ContainerStartException

from caida_ai_ops.itdk.config import Settings
from caida_ai_ops.itdk.server import create_app
from tests.conftest import TEST_MASTER_KEY

pytestmark = pytest.mark.integration

APP_ROLE = "itdk_test_reader"
APP_PASSWORD = "deterministic-test-only-password"


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    try:
        container = PostgresContainer("postgres:17-alpine")
        container.start()
    except (DockerException, ContainerStartException, ConnectionError, TimeoutError) as exc:
        if os.environ.get("ITDK_INTEGRATION_REQUIRED") == "1":
            raise RuntimeError(
                f"required Docker/PostgreSQL infrastructure unavailable: {type(exc).__name__}"
            ) from exc
        pytest.skip(f"Docker/PostgreSQL infrastructure unavailable: {type(exc).__name__}")
    try:
        yield container
    finally:
        container.stop()


def _admin_url(container: PostgresContainer) -> str:
    return container.get_connection_url(driver=None)


def _app_url(container: PostgresContainer) -> str:
    return (
        f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{container.get_container_host_ip()}:"
        f"{container.get_exposed_port(5432)}/{container.dbname}"
    )


@pytest.fixture(scope="module")
def provisioned_database(postgres: PostgresContainer) -> str:
    with psycopg.connect(_admin_url(postgres), autocommit=True) as connection:
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute(
            sql.SQL("REVOKE CREATE ON DATABASE {} FROM PUBLIC").format(sql.Identifier(postgres.dbname))
        )
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(APP_PASSWORD)
            )
        )
        connection.execute("CREATE SCHEMA caida_itdk")
        connection.execute(
            """
            CREATE TABLE caida_itdk.itdk_link_endpoints (
                link_id text NOT NULL,
                endpoint_ordinal integer NOT NULL,
                endpoint_token text NOT NULL,
                node_id text NOT NULL,
                PRIMARY KEY (link_id, endpoint_ordinal)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE caida_itdk.itdk_node_as (
                node_id text NOT NULL,
                asn bigint NOT NULL,
                method text,
                PRIMARY KEY (node_id, asn)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE caida_itdk.itdk_node_geolocation (
                node_id text PRIMARY KEY,
                continent text,
                country text,
                region text,
                city text,
                latitude double precision,
                longitude double precision,
                method text
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE caida_itdk.itdk_router_hostnames (
                ip inet NOT NULL,
                hostname text NOT NULL,
                PRIMARY KEY (ip, hostname)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO caida_itdk.itdk_link_endpoints VALUES
                ('L1', 1, 'N1967:192.0.2.1', 'N1967'),
                ('L1', 2, 'N2:198.51.100.2', 'N2'),
                ('L2', 1, 'N1967', 'N1967'),
                ('L2', 2, 'raw-d', 'N3')
            """
        )
        connection.execute(
            """
            INSERT INTO caida_itdk.itdk_node_as
            SELECT 'B' || lpad(value::text, 4, '0'), 64500, 'fixture'
            FROM generate_series(0, 1204) AS value
            """
        )
        connection.execute(
            """
            INSERT INTO caida_itdk.itdk_node_as VALUES
                ('N1967', 64501, 'hoiho'),
                ('N1967', 64502, 'alias')
            """
        )
        connection.execute(
            """
            INSERT INTO caida_itdk.itdk_node_geolocation VALUES
                ('N1967', 'North America', 'US', NULL, 'Los Angeles', 34.05, -118.25, 'hoiho'),
                ('N2', 'North America', 'US', 'CA', 'San Diego', 32.72, -117.16, 'maxmind'),
                ('N3', 'Europe', 'DE', NULL, 'Berlin', 52.52, 13.40, 'maxmind')
            """
        )
        connection.execute(
            """
            INSERT INTO caida_itdk.itdk_router_hostnames VALUES
                ('192.0.2.1', 'router-one.example'),
                ('192.0.2.1', 'router-one-backup.example'),
                ('2001:db8::1', 'edge%literal.example')
            """
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(postgres.dbname), sql.Identifier(APP_ROLE)
            )
        )
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA caida_itdk TO {}").format(sql.Identifier(APP_ROLE)))
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA caida_itdk TO {}").format(sql.Identifier(APP_ROLE))
        )
    return _app_url(postgres)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def running_server(
    provisioned_database: str, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[tuple[str, Path]]:
    output_dir = tmp_path_factory.mktemp("mcp-output")
    app = create_app(
        Settings(
            master_key=TEST_MASTER_KEY,
            database_url=provisioned_database,
            db_pool_max=2,
            query_timeout_ms=10_000,
            output_dir=output_dir,
            log_level="CRITICAL",
        )
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on"))
    server_task = asyncio.create_task(server.serve([listener]))
    with anyio.fail_after(10):
        while not server.started:
            await anyio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}", output_dir
    finally:
        server.should_exit = True
        await server_task


def _structured(result: Any) -> dict[str, Any]:
    dumped = result.model_dump(mode="json", by_alias=True)
    assert dumped["isError"] is False
    assert set(dumped["structuredContent"]) == {"file_path", "row_count", "columns"}
    return dumped["structuredContent"]


def _read_csv(metadata: dict[str, Any], output_dir: Path) -> list[list[str]]:
    path = Path(metadata["file_path"])
    assert path.parent == output_dir
    assert path.is_file()
    assert path.stat().st_size > 0
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.reader(source))
    assert len(rows) == metadata["row_count"] + 1
    assert rows[0] == [column["name"] for column in metadata["columns"]]
    return rows


@pytest.mark.asyncio(loop_scope="module")
async def test_authentication_protects_streamable_http(
    running_server: tuple[str, Path],
) -> None:
    base_url, _ = running_server
    async with httpx.AsyncClient() as client:
        for headers in (
            None,
            {"Authorization": "Bearer wrong"},
            {"Authorization": "Basic wrong"},
        ):
            response = await client.post(f"{base_url}/mcp", headers=headers)
            assert response.status_code == 401
            assert response.json() == {"error": "unauthorized"}
        duplicate = await client.get(
            f"{base_url}/mcp",
            headers=[
                ("Authorization", f"Bearer {TEST_MASTER_KEY}"),
                ("Authorization", f"Bearer {TEST_MASTER_KEY}"),
            ],
        )
        assert duplicate.status_code == 401
        authorized_message = await client.post(
            f"{base_url}/mcp",
            headers={"Authorization": f"Bearer {TEST_MASTER_KEY}"},
        )
        assert authorized_message.status_code != 401


@pytest.mark.asyncio(loop_scope="module")
async def test_every_generic_itdk_tool_over_streamable_http(
    running_server: tuple[str, Path],
) -> None:
    base_url, output_dir = running_server
    headers = {"Authorization": f"Bearer {TEST_MASTER_KEY}"}
    async with (
        MCPAsyncClient(headers=headers) as client,
        streamable_http_client(f"{base_url}/mcp", http_client=client) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        generic_names = [
            "get_node_profile",
            "find_nodes_by_asn",
            "search_nodes_by_geolocation",
            "get_link_endpoints",
            "find_links_for_node",
            "get_transit_interfaces",
            "lookup_router_hostnames",
        ]
        by_name = {tool.name: tool for tool in listed.tools}
        assert len(by_name) == 43
        assert set(generic_names) <= by_name.keys()
        for name in generic_names:
            tool = by_name[name]
            schema = tool.model_dump(mode="json", by_alias=True)["inputSchema"]
            assert schema["additionalProperties"] is False

        cases = [
            ("get_node_profile", {"node_id": "N1967"}, 2),
            ("get_node_profile", {"node_id": "N1967", "include": "geolocation"}, 1),
            ("get_node_profile", {"node_id": "N1967", "include": "interfaces"}, 1),
            ("get_node_profile", {"node_id": "N1967", "include": "links"}, 2),
            ("find_nodes_by_asn", {"asn": 64500}, 1_205),
            (
                "search_nodes_by_geolocation",
                {"country": "US", "longitude_min": -119, "longitude_max": -117},
                2,
            ),
            ("get_link_endpoints", {"link_id": "L1"}, 2),
            ("find_links_for_node", {"node_id": "N1967"}, 2),
            ("get_transit_interfaces", {"node_id": "N1967"}, 1),
            ("get_transit_interfaces", {"link_id": "L1"}, 2),
            ("lookup_router_hostnames", {"ip": "192.0.2.1"}, 2),
            ("lookup_router_hostnames", {"hostname_exact": "router-one.example"}, 1),
            ("lookup_router_hostnames", {"hostname_prefix": "edge%"}, 1),
        ]
        for name, arguments, expected_count in cases:
            metadata = _structured(await session.call_tool(name, arguments))
            assert metadata["row_count"] == expected_count
            rows = _read_csv(metadata, output_dir)
            if name == "get_node_profile" and arguments.get("include") == "geolocation":
                assert rows[1][3] == r"\N"

        invalid = await session.call_tool("get_transit_interfaces", {})
        dumped = invalid.model_dump(mode="json", by_alias=True)
        assert dumped["isError"] is True
        assert dumped["content"][0]["text"].endswith("INVALID_ARGUMENT")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO caida_itdk.itdk_node_as VALUES ('DENIED', 1, 'denied')",
        "CREATE TABLE denied_create (value integer)",
        "ALTER TABLE caida_itdk.itdk_node_as ADD COLUMN denied integer",
    ],
)
def test_application_role_cannot_insert_create_or_alter(provisioned_database: str, statement: str) -> None:
    with psycopg.connect(provisioned_database) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)
        connection.rollback()


def test_application_role_and_session_are_read_only(provisioned_database: str) -> None:
    with psycopg.connect(provisioned_database) as connection:
        row = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == (False, False, False)
    pool_settings_url = provisioned_database + "?options=-c%20default_transaction_read_only%3Don"
    with psycopg.connect(pool_settings_url) as connection:
        assert connection.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute("CREATE TEMP TABLE denied_even_if_temporary (value integer)")
