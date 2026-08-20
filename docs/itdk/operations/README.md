# Operations

## Configuration

Set `MCP_MASTER_KEY` to at least 32 visible ASCII characters. Configure exactly one database
mode: `DATABASE_URL`, or the complete `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and
`DB_PASSWORD` set. Optional settings are `MCP_HOST`, `MCP_PORT`, `DB_POOL_MAX`,
`QUERY_TIMEOUT_MS`, `OUTPUT_DIR`, and `LOG_LEVEL`.

The database login should have only `CONNECT`, schema `USAGE`, and `SELECT` on the required
`caida_itdk` tables. The application also enforces read-only transactions and timeouts.

## Run

```bash
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

Register an MCP v2 Streamable HTTP client at `http://127.0.0.1:8000/mcp` and send
`Authorization: Bearer <MCP_MASTER_KEY>` on every transport request. The runnable examples
are in [`examples/itdk_client`](../../../examples/itdk_client/README.md).

The named `caida-ai-ops-outputs` volume is mounted at `/app/outputs`. Back it up before
removing the deployment if generated CSVs or Ark result archives must be retained.

## Live Ark

Demo mode (`MATTHEWPP_DEMO=1`) never contacts Ark. Live mode additionally requires CAIDA Ark
authorization, the scamper Python package visible to the runtime, a mounted mux socket, and
the runtime user in the socket's group. Set `MATTHEWPP_MUX` if the socket is not
`/run/ark/mux`. Validate with the opt-in suite only from an authorized host:

```bash
MATTHEWPP_DEMO=0 pytest -m live
```

## Verification and incidents

`/healthz` proves the process is serving; `/readyz` checks database connectivity and output
readiness. Neither endpoint exposes dependency details. Logs are structured and redact every
configured secret literal. For database incidents, check role grants, network reachability,
pool exhaustion, and statement timeout configuration without logging the DSN.
