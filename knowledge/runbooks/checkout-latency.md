---
id: rb-checkout-latency
title: Runbook - checkout-service high latency or error rate
document_type: runbook
service: checkout-service
team: commerce-platform
severity: SEV1
version: 3
---
# checkout-service high latency or error rate

## Triage
1. Check golden signals: p95 latency, 5xx rate, CPU, memory and the orders-db connection pool.
2. Correlate the onset with the deployment timeline (`get_recent_deployments`). Most checkout
   latency incidents in the last year started within 30 minutes of a deployment.
3. Look at the slowest span of a representative trace. If the slowest span is `orders-db`, treat
   the incident as database-bound.

## Scaling guidance
Scaling checkout-service out is only appropriate when pods are CPU-bound (CPU above 70-75%) and the
orders-db connection pool has headroom. Each replica holds roughly 33 pooled connections and
orders-db allows 250 connections in total. **Do not scale checkout-service when the connection pool
is saturated**: more replicas open more connections against the same slow database and make the
incident worse.

## Database-bound latency
If the pool is at or near 100% while CPU is normal, check `get_slow_queries` for a query whose mean
latency regressed versus baseline. If the regression began right after a deployment, roll the
deployment back (see the rollback procedure). A DBA may alternatively add an index, which is a
HIGH-risk production change.
