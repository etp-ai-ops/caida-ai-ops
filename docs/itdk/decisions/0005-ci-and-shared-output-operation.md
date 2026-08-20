# 0005 — CI gates and shared-output operation

**Status:** Accepted  
**Date:** 2026-08-19

## Context

Phase 5 must turn the implemented service into an operable package without
publishing artifacts or exposing credentials. The MCP response contains a
container-local path, so independent non-root agent containers need a precise,
least-privilege way to consume the generated file.

## Decision

Run three GitHub Actions gates on Ubuntu 24.04 with repository contents read
permission only: quality/unit, real Docker/PostgreSQL integration, and a
production image build. Integration and image jobs depend on the quality job.
Use maintained major action pins, synthetic fixture-only credentials, require
Docker in CI, and never push an image or mutate an external service.

Prepare `/app/outputs` as `10001:10001` mode `0770` and publish completed CSVs
as mode `0640`. An agent container joins GID 10001, mounts the same named volume
read-only at `/app/outputs`, and can therefore use the returned path literally.
Do not expose an HTTP file-download route in this phase.

## Consequences

- Local integration runs may skip when Docker is genuinely unavailable; CI
  sets `ITDK_INTEGRATION_REQUIRED=1`, converting that condition into failure.
- Formatting, lint, typing, unit behavior, real protocol/database behavior,
  and image construction fail independently and are visible as separate gates.
- A consumer using another mount path must deliberately translate the trusted
  `/app/outputs/` prefix. A consumer without GID 10001 cannot read the files.
- Output retention, volume capacity, and deletion remain operator-owned.
