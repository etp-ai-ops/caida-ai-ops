# Revised MCP server implementation agent log

This file records every implementation sub-agent created for the approved
[revised implementation plan](2026-08-19-mcp-server-revised.md). Each agent is
started with a fresh context window (`fork_turns: none`) and must independently
read `AGENT.md`, the revised plan, and the current repository state before
editing. Agents are assigned sequentially because later phases depend on the
interfaces and files produced by earlier phases.

## Phase 1 — Bootstrap the service

**Status:** Completed

**Task name:** `phase_1_bootstrap`

**Prompt:**

> Implement Phase 1, "Bootstrap the service," from
> `docs/plans/2026-08-19-mcp-server-revised.md` in the current repository.
> You have a fresh context window. Before editing, read `AGENT.md`, the entire
> revised plan, the relevant documentation, and inspect the current worktree.
> Follow all repository rules, especially never reading `.env`, never executing
> programs or scripts authored during this session, and updating documentation.
> Build the Python/Flask service bootstrap, official Python MCP SDK dependency,
> project manifest and source layout, `.gitignore`, `.env.example`, Dockerfile,
> docker-compose shared output volume configuration, configuration parsing with
> startup validation, structured secret-redacting logging, and health endpoints.
> Keep scope to Phase 1 and avoid implementing the database repositories, MCP
> tools, or Phase 4 tests beyond minimal bootstrap tests if needed. Use
> `apply_patch` for edits. You may run only safe static inspections or existing
> project commands consistent with `AGENT.md`; do not execute newly authored
> code. At completion, report every changed file, key choices, static checks run,
> and any integration assumptions or risks for the next phase. Do not commit.

**Handoff:** Added the Python/Flask package, validated runtime settings,
secret-redacting JSON logging, health/readiness routes, non-root image,
loopback-published Compose service, shared output volume, and supporting
documentation. Database connectivity and MCP transport remain intentionally
deferred.

## Phase 2 — Establish the read-only data boundary

**Status:** Completed

**Task name:** `phase_2_data_boundary`

**Prompt:**

> Implement Phase 2, "Establish the read-only data boundary," from
> `docs/plans/2026-08-19-mcp-server-revised.md` in the current repository.
> You have a fresh context window. Before editing, read `AGENT.md`, the entire
> revised plan, the agent log, all relevant docs, and inspect the completed
> Phase 1 worktree. Follow every repository rule: never read `.env`, never
> execute programs or scripts authored during this session, update docs, use
> `apply_patch`, and do not commit. Add an appropriate psycopg 3 pool dependency
> and implement a conservative PostgreSQL connection pool that establishes
> `default_transaction_read_only=on`, statement and lock timeouts, and an
> application name. Implement fixed, parameterized SELECT-only repository
> functions for every approved query family/tool in the revised plan. No caller
> may provide SQL, relation names, projections, joins, ordering, or arbitrary
> filters. Preserve the schema facts and stable orderings from the plan. Add a
> streaming CSV result writer in `OUTPUT_DIR` with collision-resistant safe
> filenames, deterministic column metadata/type serialization, atomic or
> cleanup-safe failure behavior, and no artificial row limit or full-result
> memory buffering. Keep scope to Phase 2: expose clean interfaces for Phase 3,
> but do not register MCP tools or implement request authentication. Update the
> architecture, data, contracts, operations, decisions, and plan status where
> the Phase 2 behavior makes those docs stale. Perform safe static checks only,
> consistent with `AGENT.md`. At completion, report every changed file, exact
> query APIs and projections, safety invariants, checks performed, and risks or
> assumptions for Phase 3.

**Handoff:** Added the psycopg 3 read-only pool, database-aware readiness,
fixed-query repositories for all seven APIs, named-cursor CSV streaming with
typed serialization and atomic publication, and supporting documentation.
Phase 3 retains authentication, strict MCP schemas, generic error mapping, and
metadata serialization.

## Phase 3 — Implement the MCP boundary

**Status:** Completed

**Task name:** `phase_3_mcp_boundary`

**Prompt:**

> Implement Phase 3, "Implement the MCP boundary," from
> `docs/plans/2026-08-19-mcp-server-revised.md` in the current repository.
> You have a fresh context window. First read `AGENT.md`, the full revised plan,
> the agent log and current docs, and inspect all Phase 1/2 source interfaces.
> Follow every repository rule: never read `.env`, never execute programs or
> scripts authored during this session, update docs, use `apply_patch`, and do
> not commit. Integrate the official Python MCP SDK into the Flask service with
> a standards-compliant SSE MCP connection/message boundary. Do not hand-roll a
> lookalike JSON-RPC protocol or rely on private SDK internals. If the official
> SDK's public SSE transport is ASGI-only and cannot truthfully be hosted inside
> Flask/WSGI, use the smallest robust public-API adapter/composition necessary,
> preserve Flask for the application/operational endpoints, and document the
> exact architecture decision and runtime server change. Protect every MCP
> transport route with strict constant-time `Authorization: Bearer` validation;
> health endpoints may remain unauthenticated and disclose no secrets. Register
> exactly the seven deterministic tools from the revised plan, with strict
> typed/JSON-schema inputs, cross-field validation (exactly-one selectors,
> coordinate ranges and min/max ordering, country/ASN/identifier/prefix
> bounds), and no extra SQL-like controls or row limits. Wire them only to the
> Phase 2 repositories. Serialize every `CsvResult` to the exact concise JSON
> metadata envelope (`file_path`, `row_count`, ordered `columns` objects).
> Convert invalid inputs and internal/database/file errors into safe stable MCP
> errors while logging only sanitized error codes/context. Own pool lifecycle
> correctly for the actual runtime. Keep scope to Phase 3; leave comprehensive
> tests and CI to later phases. Update architecture, contracts, operations,
> decisions, data docs, and the plan status where necessary. Perform safe static
> checks consistent with `AGENT.md`. At completion report changed files, SDK
> APIs/transport composition used, exact schemas and validation rules, auth and
> lifecycle behavior, checks, and Phase 4 risks.

**Handoff:** Added the official SDK low-level server and SSE transport beneath
an authenticated Starlette ASGI mount, preserved Flask operational routes via
the public WSGI adapter, changed the runtime to single-worker Uvicorn, registered
the seven strict fixed tools, and added safe error/metadata handling. Phase 4
retains comprehensive schema/auth/unit tests and live SDK/PostgreSQL integration.

## Additional container task — Ubuntu 24.04 base image

**Status:** Completed

**Task name:** `ubuntu_2404_container`

**Prompt:**

> Change the project's production Dockerfile to use `ubuntu:24.04` as its base
> image. You have a fresh context window. Before editing, read `AGENT.md`, the
> entire revised implementation plan, the agent log, current Docker/Compose and
> operations documentation, `pyproject.toml`, and the current application
> entrypoints. Phase 3 is concurrently moving the runtime to the official MCP
> SDK's ASGI SSE transport and Uvicorn, so preserve and accommodate the latest
> ASGI/Uvicorn command and dependencies present in the worktree; do not restore
> an obsolete Gunicorn/WSGI command. Follow all repository rules: do not read
> `.env`, do not execute newly authored code, use `apply_patch`, update relevant
> docs/decision records, and do not commit. Build a minimal practical Ubuntu
> 24.04 Python 3.12 image: install only required OS packages without recommended
> extras, clean apt metadata in the same layer, create an isolated virtual
> environment suitable for Ubuntu's externally-managed Python policy, install
> the project, retain deterministic Python environment settings, create and own
> `OUTPUT_DIR`, and run as a dedicated non-root user. Preserve loopback/internal
> networking, healthcheck compatibility, runtime-only secret injection, and the
> shared output volume. Statically review the final Dockerfile and Compose
> expansion if available, but do not build the image or execute session-authored
> application code. Report changed files, final image/runtime approach, checks,
> and any remaining build risks.

**Handoff:** Rebased the production image on Ubuntu 24.04, installed the minimal
Python 3.12/venv/CA-certificate/account-tooling package set without APT
recommendations, and installed the project into `/opt/venv`. The image runs the
one-worker ASGI Uvicorn command without access logging as fixed UID/GID
`10001:10001`, with
only `/app/outputs` owned for runtime writes. Compose expansion passed with
loopback publication, runtime-only configuration, healthcheck, and shared
volume intact. The image was intentionally not built.

## Phase 4 — Verify functionality and safety

**Status:** Completed (dynamic execution pending Phase 5 CI/user execution)

**Task name:** `phase_4_verification`

**Prompt:**

> Implement Phase 4, "Verify functionality and safety," from
> `docs/plans/2026-08-19-mcp-server-revised.md` in the current repository.
> You have a fresh context window. Read `AGENT.md`, the entire revised plan and
> agent log, all contracts/architecture/data/operations/decision docs, and all
> Phase 1-3 implementation before editing. Follow every repository rule:
> never read `.env`, never dynamically execute tests or application programs
> authored during this session, use `apply_patch`, update docs, and do not
> commit. Create a comprehensive pytest suite covering configuration startup
> validation, bearer authentication (both SSE legs, malformed/duplicate
> headers, constant-time comparison behavior without timing assertions), log
> redaction, all strict tool schemas and cross-field rules, exact metadata/type
> serialization, stable safe errors, pool lifecycle/read-only settings, fixed
> SQL/query bindings, streaming CSV formatting/escaping/null/numeric handling,
> atomic cleanup, and output accessibility. Add an integration suite using an
> ephemeral PostgreSQL fixture that creates only the supplied `caida_itdk`
> schema/tables/view with representative one-to-many data, provisions the
> application's login as a read-only role, exercises every repository/tool via
> the official MCP client SSE path, validates generated CSV files and unlimited
> multi-row behavior, and proves INSERT/CREATE/ALTER attempts fail. Ensure the
> integration tests are clearly markable/skippable when Docker or required
> infrastructure is absent, but do not silently turn assertion failures into
> skips. Use maintained test dependencies and deterministic fixtures; never use
> real credentials. Because `AGENT.md` forbids executing newly authored code,
> do not run pytest or the application. You may run static parsing/compilation,
> Compose expansion, `git diff --check`, and static coverage inventories that do
> not import or execute the authored tests/app. Document exact user/CI commands
> for unit and integration execution and state explicitly that dynamic results
> remain pending Phase 5 CI/user execution. Report changed files, test matrix,
> fixture/security design, static checks, and any discovered implementation
> defects you fixed within Phase 4.

**Handoff:** Added comprehensive unit coverage for startup configuration,
strict bearer authentication, redaction, all schemas and cross-field rules,
safe tool results/errors, database pool configuration/lifecycle, fixed SQL and
bindings, and streaming/atomic CSV output. Added a separately marked Docker
integration suite that provisions the supplied ITDK relations beneath an
administrator owner, grants a distinct application login read-only access,
starts the real ASGI service, and drives every tool/profile family through the
official MCP SDK SSE client. It validates a 1,205-row unbounded result,
generated-file accessibility/content, both authenticated transport legs, and
denial of `INSERT`, `CREATE`, and `ALTER`. Dynamic test results remain pending
because repository rules prohibit executing session-authored code.

## Phase 5 — Package and operate

**Status:** Completed (static verification only; dynamic CI execution pending)

**Task name:** `phase_5_package_operate`

**Prompt:**

> Implement Phase 5, "Package and operate," from
> `docs/plans/2026-08-19-mcp-server-revised.md` in the current repository.
> You have a fresh context window. Read `AGENT.md`, the entire revised plan and
> agent log, every current docs area/ADR, Dockerfile, Compose file, manifest,
> application entrypoints, and Phase 4 tests before editing. Follow every
> repository rule: never read `.env`, never execute code/tests authored during
> this session, use `apply_patch`, keep docs current, and do not commit. Finish
> production packaging around the user-requested Ubuntu 24.04 non-root image
> and one-worker Uvicorn SSE runtime. Audit Docker/Compose for runtime-only
> secrets, loopback binding, healthcheck correctness, output-volume sharing and
> permissions, graceful stop, minimal build context/layers, and no secret
> leakage. Write complete local startup and shutdown instructions, secure
> read-only PostgreSQL role/grant examples, shared-volume consumption patterns
> (including how an agent/container sees returned `/app/outputs/...` paths),
> health/readiness checks, MCP client endpoint/auth usage, test commands,
> operational limits, cleanup, upgrades, and troubleshooting. Add GitHub Actions
> CI with least-required permissions and pinned major actions that runs Ruff,
> mypy, unit tests, the real integration suite where Docker is available, and a
> production Docker image build; avoid publishing images or external state.
> Ensure CI uses synthetic/non-secret fixture credentials and fails rather than
> masking test failures. Add any small package/config fixes needed for those
> commands to be internally consistent. Do not dynamically run the new CI,
> tests, app, or image; safe static YAML/Compose/Docker inspections and
> `git diff --check` are allowed. Update the revised plan and agent log with an
> honest Phase 5 status. Report every changed file, CI job/gate structure,
> operator workflows, static checks, and remaining verification risks.

**Handoff:** Reworked the Ubuntu 24.04 image into build/runtime stages with a
runtime-only wheelhouse install, image-native liveness check, explicit
`SIGTERM`, and one-worker non-root Uvicorn runtime. Made completed shared-volume
outputs group-readable for read-only GID 10001 consumers. Added least-privilege
GitHub Actions gates for Ruff, mypy, units, required Docker/PostgreSQL MCP
integration, and a no-push production build. Expanded the operator runbook for
database provisioning, both database configuration modes, startup/shutdown,
health/readiness, MCP auth, shared-volume path semantics, limits, rotation,
cleanup, upgrades, and failures. Static checks completed; the repository rule
prohibited executing the authored CI, tests, app, or image, so all dynamic
results remain pending.

## Additional configuration task — Split PostgreSQL environment variables

**Status:** Completed (dynamic tests pending CI/user execution)

**Task name:** `split_database_env`

**Prompt:**

> Extend the application so operators can configure PostgreSQL with the five
> split environment variables `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`,
> and `DB_PASSWORD`, which the application safely assembles into the equivalent
> `postgresql://<username>:<password>@<host>:<port>/<database>` connection URL.
> You have a fresh context window. Before editing, read `AGENT.md`, the entire
> revised plan and agent log, current configuration implementation/tests,
> `.env.example` (never `.env`), Docker/Compose, CI if present, and operations/
> security docs. Phase 5 is concurrently editing operations/CI/package files;
> coordinate by preserving its latest changes and keep your edits narrowly
> focused. Follow all repository rules: never read `.env`, never execute code or
> tests authored during this session, use `apply_patch`, update docs and tests,
> and do not commit. Preserve `DATABASE_URL` as a supported alternative for
> backward compatibility, but require exactly one complete configuration mode:
> reject partial split settings and reject ambiguous simultaneous URL + split
> settings. Validate `DB_PORT` as 1-65535, require nonblank host/database/user/
> password, correctly bracket IPv6 literals, and percent-encode username,
> password, and database components so reserved characters cannot corrupt the
> URL. Never include the assembled URL, username, password, or raw values in
> validation errors or logs; ensure configured split secrets participate in
> registered redaction. Update Compose runtime environment wiring and
> `.env.example` without values, and document both mutually exclusive modes.
> Add focused unit tests for URL mode, split mode, special characters, IPv4/
> DNS/IPv6, partial/conflicting inputs, invalid ports, and redaction, plus adjust
> existing fixtures/CI synthetic variables as needed. Do not run the authored
> tests or app; safe compile/static checks, Compose expansion with synthetic
> placeholders, and `git diff --check` are allowed. Report changed files, exact
> precedence/validation/encoding behavior, Compose contract, checks, and risks.

**Handoff:** Added mutually exclusive `DATABASE_URL` and complete five-variable
split modes, safe URL assembly with bounded ports, IPv6 brackets and encoded
userinfo/path components, raw split-value log redaction, Compose wiring for both
modes, and focused unit coverage. Dynamic tests remain pending under repository
rules.

## Root integration audit

After all agents completed, the primary agent installed the declared project
and development dependencies into an isolated temporary Python 3.12
environment without starting the application or tests. The audit corrected the
integration fixture to use the maintained `testcontainers.postgres` import,
added the package's standard `py.typed` marker, and narrowly annotated the
official MCP SDK's currently untyped decorator factories. Dependency-backed
Ruff formatting, Ruff lint, and strict mypy then passed. Python compilation,
both Compose database modes, a source wheel build and contents inspection, and
`git diff --check` also passed. Dynamic unit, integration, application, and
image execution remain pending under `AGENT.md`.
