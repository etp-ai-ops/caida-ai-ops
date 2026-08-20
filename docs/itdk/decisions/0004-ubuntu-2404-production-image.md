# 0004 — Ubuntu 24.04 production image

**Status:** Accepted  
**Date:** 2026-08-19

## Context

The production container must use Ubuntu 24.04 while remaining small,
non-root, compatible with Python 3.12, and able to write generated CSV files to
the shared output volume. Ubuntu treats its system Python environment as
externally managed, so application dependencies must not be installed into it.

## Decision

Use `ubuntu:24.04` for both stages. A build stage resolves the project and all
dependencies into a wheelhouse. The final stage installs from that wheelhouse,
so it contains neither the source tree nor the build backend. Install only
`python3`, `python3-venv`, `ca-certificates`, and `passwd` (for runtime
user/group creation), with APT recommendations disabled, and remove APT
metadata in the same layer. Create `/opt/venv` and place it first on `PATH`.

Run Uvicorn as the dedicated fixed UID/GID `10001:10001`. Keep application
files root-owned and grant that user ownership of `/app/outputs`, the only
application-writable path. Continue injecting secrets only when the container
is created and keep the one-worker ASGI command selected in decision 0003.
Disable Uvicorn's access log so MCP session identifiers in message URLs are not
recorded. Publish a standard-library `/healthz` image healthcheck, declare
`SIGTERM` as the stop signal, and exec the one-worker Uvicorn process so it owns
signal handling and ASGI lifespan shutdown.

## Consequences

- Python package installation complies with Ubuntu's externally managed Python
  policy without altering the system interpreter.
- A stable numeric identity gives new named volumes deterministic ownership.
- Completed outputs are group-readable mode `0640`; the output directory is
  group-accessible mode `0770`. Consumer containers join GID 10001 and mount it
  read-only.
- Existing volumes created by an image using a different numeric identity may
  require an operator-controlled ownership migration or recreation.
- Dependency compatibility ranges and `ubuntu:24.04` are not immutable locks.
  Builds are not byte-for-byte repeatable; promotion must record or review the
  resolved dependencies and base-image identity.
