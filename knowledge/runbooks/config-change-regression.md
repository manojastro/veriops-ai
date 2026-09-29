---
id: rb-config-regression
title: Runbook - Runtime configuration regressions
document_type: runbook
team: platform-sre
severity: SEV2
version: 1
---
# Runtime configuration regressions

Runtime configuration (feature flags, cache TTLs, timeouts) changes without a deployment. When a
service degrades with no recent deployment, check `get_config_history` first.

## Response
Revert the offending key to its previous value. Common example: `cache.ttl_seconds` set to 0
disables caching and multiplies database load. Configuration changes to production are HIGH-risk
and require approval.
