# caida-ai-ops

`caida-ai-ops` is one installable Python project for CAIDA network operations. It combines
active Ark measurement, AS Rank analysis, assignment-oriented ITDK analyses, and the safe
generic ITDK query service that previously lived in separate `arc-automate` and `itdk-mcp`
repositories.

## Capabilities

- **Ark:** discover vantage points; run bounded ping, traceroute, and DNS measurements;
  compare paths; and diagnose multi-vantage-point anomalies.
- **AS Rank:** inspect customer cones, tier distributions, organizations, and countries.
- **ITDK analyses:** run the original geo-adjacency, footprint, peering, and pairwise-link
  analyses with a consistent JSON envelope.
- **Generic ITDK:** run seven strictly validated, parameterized, read-only repository tools
  that stream large results to atomic CSV files.

The stdio server exposes all 43 tools in one MCP process. The authenticated HTTP service
continues to expose the seven generic ITDK tools for server deployments.

## Install and run

Python 3.12 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Exercise Ark, AS Rank, and assignment ITDK tools without external access.
MATTHEWPP_DEMO=1 caida-ai-ops
```

Generic ITDK tools load their database connection lazily. Configure `DATABASE_URL`, or all
five of `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and `DB_PASSWORD`, before calling one
of those tools. They write results beneath `OUTPUT_DIR` (default `/app/outputs`).

For the authenticated HTTP service, also set a visible-ASCII `MCP_MASTER_KEY` of at least 32
characters and run:

```bash
caida-itdk-service
```

It serves health endpoints at `/healthz` and `/readyz` and MCP Streamable HTTP at `/mcp`.
See [ITDK operations](docs/itdk/operations/README.md) for production configuration.

## Development

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest -m 'not integration'
```

The Docker-backed integration suite is separate:

```bash
ITDK_INTEGRATION_REQUIRED=1 python -m pytest -m integration
```

Architecture and contracts are documented under [`docs/itdk`](docs/itdk/README) and the
Ark design material is under [`docs/ark`](docs/ark/MEASUREMENT_SPEC.md). Demo notebooks and
ITDK client examples live in [`examples`](examples).
