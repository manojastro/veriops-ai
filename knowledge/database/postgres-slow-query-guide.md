---
id: db-slow-query-guide
title: PostgreSQL slow query troubleshooting guide
document_type: database_guide
team: data-platform
version: 2
---
# PostgreSQL slow query troubleshooting

## Find the query
Use pg_stat_statements (`get_slow_queries`) and compare mean execution time against the 7-day
baseline. A regression factor above 3x on a hot query is significant.

## Common causes
- Predicates that wrap an indexed column in a function, e.g. `lower(email) = lower($1)`, cannot use
  a plain B-tree index and fall back to a sequential scan.
- New `ORDER BY` clauses on unindexed columns force sorts.
- Implicit casts between parameter and column types.

## Fixes
Prefer rolling back the application change. An expression index (`CREATE INDEX CONCURRENTLY ... ON
customers (lower(email))`) also fixes the plan but is a production schema change and needs DBA
approval. Never run destructive statements during an incident.
