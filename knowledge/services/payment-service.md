---
id: svc-payment
title: payment-service - service overview
document_type: service_doc
service: payment-service
team: payments
version: 4
---
# payment-service

Tier-0. Authorises and captures payments via the external acme-pay gateway (primary) with a
secondary gateway available for failover. Database: payments-db. SLO: p95 < 400 ms, error rate < 0.5%.
