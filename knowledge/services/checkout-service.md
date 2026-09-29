---
id: svc-checkout
title: checkout-service - service overview
document_type: service_doc
service: checkout-service
team: commerce-platform
version: 7
---
# checkout-service

Tier-0, revenue-critical. Orchestrates cart checkout: prices the cart, reserves inventory, creates
the order in orders-db and calls payment-service for authorisation.

- Runtime: Python 3.12, SQLAlchemy, 3 replicas (HPA 3-12)
- Database: orders-db (PostgreSQL 16), pool size 100 across replicas, max_connections 250
- Dependencies: orders-db, payment-service, inventory-service, redis-cache
- SLO: p95 < 500 ms, error rate < 1%
- On-call: commerce-platform
