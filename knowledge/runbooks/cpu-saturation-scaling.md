---
id: rb-cpu-saturation
title: Runbook - CPU saturation and horizontal scaling
document_type: runbook
team: platform-sre
severity: SEV2
version: 4
---
# CPU saturation and horizontal scaling

## When scaling is correct
Scale out when CPU is above 75-80% across pods, traffic is above baseline, and downstream
dependencies (especially the database connection pool) have headroom. Target roughly 50% CPU.
Stay within the HPA maximum.

## Checks before scaling
- Database: projected connections = replicas x per-pod pool size must stay below max_connections.
- Dependencies must be healthy - scaling does not help if a dependency is the bottleneck.

## Rollback
Scaling is reversible: scale back to the previous replica count.
