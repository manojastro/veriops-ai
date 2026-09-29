---
id: slo-definitions
title: Service level objectives
document_type: slo
team: platform-sre
version: 2
---
# Service level objectives

| Service | p95 latency | Error rate |
|---|---|---|
| checkout-service | 500 ms | 1.0% |
| payment-service | 400 ms | 0.5% |
| inventory-service | 300 ms | 0.5% |
| user-service | 250 ms | 0.5% |

A breach of either objective for 5 minutes pages the owning team. SEV1 is declared when a tier-0
service exceeds 10% errors.
