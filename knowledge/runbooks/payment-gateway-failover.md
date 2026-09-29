---
id: rb-payment-failover
title: Runbook - Payment gateway degradation and failover
document_type: runbook
service: payment-service
team: payments
severity: SEV1
version: 5
---
# Payment gateway degradation and failover

## Detection
Authorisation errors rise, traces show `acme-pay-gateway POST /v2/authorize` spans timing out, and
`get_dependency_health` reports the gateway unhealthy while payment-service CPU and memory are normal.

## Failover
payment-service supports a secondary gateway. Failover is a runtime configuration change:
set payment.gateway.provider=secondary
The change takes effect within 30 seconds without a restart. Restarting, scaling or rolling back
payment-service does not help during a gateway outage.

## Failback
When the primary gateway status page reports recovery for 30 minutes, set the provider back to
`primary` during business hours.
