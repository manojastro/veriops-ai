---
id: rb-pool-exhaustion
title: Runbook - Database connection pool exhaustion
document_type: runbook
team: platform-sre
severity: SEV2
version: 2
---
# Database connection pool exhaustion

## Symptoms
`QueuePool limit ... reached`, `PoolTimeout`, requests waiting to acquire a connection, pool
utilisation at 100%.

## Distinguish the two common causes
- **Slow queries hold connections longer.** Query latency (pg_stat_statements mean time) is well
  above baseline; database CPU is high; sequential scans appear in plans. Fix the query or roll
  back the change that introduced it.
- **Connection leak.** Queries are fast but connections are never returned: pool is full while the
  database is mostly idle. Usually introduced by a code change on an error path. Roll back; a
  rolling restart only buys time.

## What not to do
Do not raise pool sizes or add replicas as a first response. Both increase the total number of
connections against a database that is already the bottleneck and can exhaust `max_connections`
for every service sharing that database.
