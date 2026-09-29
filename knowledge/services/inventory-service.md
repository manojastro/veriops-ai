---
id: svc-inventory
title: inventory-service - service overview
document_type: service_doc
service: inventory-service
team: fulfilment
version: 3
---
# inventory-service

Tier-1. Stock levels and reservations. Uses redis-cache for stock reads (`cache.ttl_seconds`,
default 300) and inventory-db for writes. SLO: p95 < 300 ms, error rate < 0.5%.
