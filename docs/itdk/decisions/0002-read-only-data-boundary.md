# 0002 — Read-only repositories and streaming CSV

**Status:** Accepted  
**Date:** 2026-08-19

## Context

Approved relations contain millions of rows. Results cannot be artificially
limited or buffered in memory, and callers must not control SQL structure.

## Decision

Use a conservative psycopg 3 pool with read-only and timeout startup options.
Keep schema-qualified static SELECTs in family repositories and bind every
caller value. Stream named server-side cursors to temporary CSV files and
atomically rename completed output. Use one profile family per call, defaulting
to ASN, to avoid cartesian multiplication.

## Consequences

A connection and transaction remain occupied throughout query execution and
file writing. A hard process death may leave a hidden temporary file. The MCP
boundary validates tool inputs, maps failures generically, and serializes
`CsvResult`; the operator runbook defines cleanup and retention.
Least-privilege database grants remain an operator responsibility.
