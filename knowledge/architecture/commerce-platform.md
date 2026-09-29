---
id: arch-commerce
title: Commerce platform architecture notes
document_type: architecture
team: architecture
version: 2
---
# Commerce platform architecture

Requests enter through the API gateway. checkout-service calls inventory-service (reserve stock) and
payment-service (authorise). Each service owns its PostgreSQL database; checkout-service's orders-db
also serves reporting jobs, which hold about 24 connections. Shared redis-cache fronts stock reads.

Failure propagation: a slow orders-db saturates checkout-service's connection pool first; payment and
inventory remain healthy. A payment gateway outage surfaces as checkout errors too, but payment-service
traces will show gateway timeouts.
