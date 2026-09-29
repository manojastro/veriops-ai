---
id: rb-memory-leak
title: Runbook - Memory leaks and OOMKilled pods
document_type: runbook
team: platform-sre
severity: SEV2
version: 2
---
# Memory leaks and OOMKilled pods

## Symptoms
Memory climbs steadily, pods are OOMKilled and restart, GC pauses grow, errors spike around restarts.

## Response
1. If the growth started after a recent deployment, roll that deployment back.
2. If there is no recent deployment (slow leak), perform a rolling restart as mitigation and open a
   defect for the owning team. A restart is a LOW-risk, temporary mitigation.
3. Scaling out does not fix a leak; every new pod leaks at the same rate.
