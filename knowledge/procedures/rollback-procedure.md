---
id: proc-rollback
title: Rollback procedure for Kubernetes deployments
document_type: rollback_procedure
team: platform-sre
version: 6
---
# Rollback procedure

## Preconditions
- The deployment being rolled back must be the currently active revision.
- A previous known-good revision must exist and must not contain an irreversible database migration.
- Rollbacks of critical services in production require approval by an on-call approver.

## Steps
1. Run the rollback in the sandbox and confirm latency and error-rate thresholds pass.
2. Roll back the deployment (the platform restores the previous ReplicaSet).
3. Verify health for 10 minutes: p95 latency, error rate, connection pool.
4. If verification fails, restore the rolled-back revision and escalate.
