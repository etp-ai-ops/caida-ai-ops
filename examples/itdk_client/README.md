# Python MCP client examples

These examples connect to the running ITDK MCP server over authenticated
HTTP+SSE. They cover discovery, every tool and selector mode, validation
errors, concurrent calls, and CSV previewing.

## Quickstart

The project targets Python 3.12. Do not invoke these examples with a system
Python 3.10 installation: packages installed in one Python environment are not
available to another, which causes `ModuleNotFoundError: No module named
'mcp'`.

From the repository root, create and activate an isolated client environment,
then install the example dependencies:

```console
cd examples/itdk_client
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Export the same master key used by the server. The URL defaults to the local
Compose endpoint shown here:

```console
export MCP_MASTER_KEY='replace-with-the-running-server-key'
export ITDK_MCP_URL='http://127.0.0.1:8000/mcp'
```

Check the unauthenticated operational endpoints first:

```console
python 00_check_health.py
```

Then discover tools and make a small request:

```console
python 01_list_tools.py
python 02_get_node_profile_asn.py N1967
```

If you prefer not to activate the environment, invoke its interpreter
directly:

```console
.venv/bin/python 01_list_tools.py
```

Every data script prints JSON metadata containing `file_path`, `row_count`,
and ordered column definitions. IDs and other defaults are illustrative; use
values that exist in your ITDK database.

## Preview returned CSV files

The returned path is inside the server container, normally
`/app/outputs/...`. A local process cannot automatically read a named Docker
volume.

### See CSV output directly on macOS

For local development, replace the named volume with a bind-mounted repository
folder. From the repository root, create that folder:

```console
mkdir -p outputs
```

If you want to preserve files already generated in the running container, copy
them before recreating it:

```console
docker cp "$(docker compose ps -q caida-ai-ops)":/app/outputs/. ./outputs/
```

In `docker-compose.yml`, replace:

```yaml
volumes:
  - itdk-outputs:/app/outputs
```

with:

```yaml
volumes:
  - ./outputs:/app/outputs
```

Recreate the service. `docker compose down` does not delete the old named
volume unless `--volumes` is supplied:

```console
docker compose down
docker compose up --build --detach
curl --fail http://127.0.0.1:8000/readyz
```

Return to the examples directory and map server paths to the visible host
folder:

```console
cd examples/itdk_client
export ITDK_OUTPUT_DIR="$(cd ../.. && pwd)/outputs"
python 02_get_node_profile_asn.py N1967 --preview 10
```

Generated CSVs are now also visible in Finder or the terminal:

```console
ls -lh ../../outputs
open ../../outputs
```

### Preview from another container

If the client can see the named output volume from another container, set its
local mount point:

```console
export ITDK_OUTPUT_DIR='/app/outputs'
.venv/bin/python 06_find_nodes_by_asn.py 64500 --preview 10
```

`ITDK_SERVER_OUTPUT_DIR` defaults to `/app/outputs` and controls the server-side
prefix translated to `ITDK_OUTPUT_DIR`. Without a visible output mount, the
scripts still verify the MCP request and print metadata, then explain why a
preview was skipped.

For a separate agent container, mount the named volume read-only at the same
path and join group 10001:

```yaml
services:
  client:
    image: your-python-client-image
    group_add:
      - "10001"
    environment:
      ITDK_MCP_URL: http://caida-ai-ops:8000/mcp
      ITDK_OUTPUT_DIR: /app/outputs
    volumes:
      - itdk-outputs:/app/outputs:ro

volumes:
  itdk-outputs:
    external: true
    name: caida-ai-ops-outputs
```

Provide `MCP_MASTER_KEY` to that client through your normal secret-injection
mechanism rather than committing it to Compose.

## Examples

| Script | Demonstrates |
| --- | --- |
| `00_check_health.py` | Liveness and readiness endpoints. |
| `01_list_tools.py` | MCP initialization and tool discovery. |
| `02_get_node_profile_asn.py` | Node profile ASN family and default include. |
| `03_get_node_profile_geolocation.py` | Node profile geolocation family. |
| `04_get_node_profile_interfaces.py` | Node profile interfaces family. |
| `05_get_node_profile_links.py` | Node profile links family. |
| `06_find_nodes_by_asn.py` | All nodes assigned to an ASN. |
| `07_search_geolocation_country.py` | Country-only geolocation search. |
| `08_search_geolocation_bbox.py` | Country plus longitude/latitude bounds. |
| `09_get_link_endpoints.py` | Exact link endpoint lookup. |
| `10_find_links_for_node.py` | All link rows for a node. |
| `11_get_transit_by_node.py` | Transit view selected by node. |
| `12_get_transit_by_link.py` | Transit view selected by link. |
| `13_lookup_hostname_by_ip.py` | Hostnames selected by IPv4 or IPv6. |
| `14_lookup_hostname_exact.py` | Exact hostname lookup. |
| `15_lookup_hostname_prefix.py` | Literal hostname prefix lookup. |
| `16_run_smoke_suite.py` | One call to every tool family in one session. |
| `17_concurrent_asn_requests.py` | Several concurrent tool calls. |
| `18_dump_tool_schemas.py` | Complete advertised input/output schemas. |
| `19_invalid_request_examples.py` | Expected `INVALID_ARGUMENT` responses. |

Use `--help` on any data script for its arguments:

```console
.venv/bin/python 08_search_geolocation_bbox.py --help
```

## Common errors

- `401 unauthorized`: the key is absent/wrong, or the client is not applying
  it to both SSE requests. These examples apply the same header to both legs.
- `503` from `/readyz`: PostgreSQL or the output directory is unavailable.
- `INTERNAL_ERROR`: inspect sanitized server logs for database, timeout, pool,
  or output-volume problems.
- Zero rows: the request worked, but the illustrative identifier is not in your
  database.
- Preview skipped: mount the output volume and set `ITDK_OUTPUT_DIR`.
