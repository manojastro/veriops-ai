---
id: svc-user
title: user-service - service overview
document_type: service_doc
service: user-service
team: identity
version: 5
---
# user-service

Tier-1. Profiles, sessions and preferences. In-memory session store per pod (memory limit 768Mi).
Database: users-db. SLO: p95 < 250 ms, error rate < 0.5%.
