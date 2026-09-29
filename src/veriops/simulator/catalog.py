"""Static catalog of simulated services: healthy baselines, SLOs, ownership and dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceProfile:
    name: str
    team: str
    criticality: str  # critical | high | medium
    description: str
    database: str
    replicas: int
    hpa_min: int
    hpa_max: int
    healthy: dict[str, float]
    slo_p95_latency_ms: float
    slo_error_rate_pct: float
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    root_operation: str = "GET /"
    db_statement: str = "SELECT 1"


SERVICES: dict[str, ServiceProfile] = {
    "checkout-service": ServiceProfile(
        name="checkout-service",
        team="commerce-platform",
        criticality="critical",
        description="Orchestrates cart checkout, order creation and payment authorisation.",
        database="orders-db",
        replicas=3,
        hpa_min=3,
        hpa_max=12,
        healthy={
            "p95_latency_ms": 310,
            "error_rate_pct": 0.4,
            "cpu_pct": 37,
            "memory_pct": 52,
            "db_pool_utilization_pct": 38,
            "rps": 420,
        },
        slo_p95_latency_ms=500,
        slo_error_rate_pct=1.0,
        dependencies=("orders-db", "payment-service", "inventory-service", "redis-cache"),
        root_operation="POST /api/checkout",
        db_statement="SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.customer_id = $1",
    ),
    "payment-service": ServiceProfile(
        name="payment-service",
        team="payments",
        criticality="critical",
        description="Authorises and captures payments through the external payment gateway.",
        database="payments-db",
        replicas=3,
        hpa_min=3,
        hpa_max=10,
        healthy={
            "p95_latency_ms": 220,
            "error_rate_pct": 0.2,
            "cpu_pct": 30,
            "memory_pct": 45,
            "db_pool_utilization_pct": 25,
            "rps": 180,
        },
        slo_p95_latency_ms=400,
        slo_error_rate_pct=0.5,
        dependencies=("payments-db", "acme-pay-gateway"),
        root_operation="POST /api/payments/authorize",
        db_statement="INSERT INTO payment_attempts (order_id, amount, status) VALUES ($1, $2, $3)",
    ),
    "inventory-service": ServiceProfile(
        name="inventory-service",
        team="fulfilment",
        criticality="high",
        description="Tracks stock levels and reservations.",
        database="inventory-db",
        replicas=2,
        hpa_min=2,
        hpa_max=8,
        healthy={
            "p95_latency_ms": 140,
            "error_rate_pct": 0.1,
            "cpu_pct": 28,
            "memory_pct": 48,
            "db_pool_utilization_pct": 30,
            "rps": 350,
        },
        slo_p95_latency_ms=300,
        slo_error_rate_pct=0.5,
        dependencies=("inventory-db", "redis-cache"),
        root_operation="POST /api/inventory/reserve",
        db_statement="UPDATE stock SET reserved = reserved + $1 WHERE sku = $2",
    ),
    "user-service": ServiceProfile(
        name="user-service",
        team="identity",
        criticality="high",
        description="User profiles, sessions and preferences.",
        database="users-db",
        replicas=3,
        hpa_min=3,
        hpa_max=12,
        healthy={
            "p95_latency_ms": 95,
            "error_rate_pct": 0.1,
            "cpu_pct": 34,
            "memory_pct": 40,
            "db_pool_utilization_pct": 20,
            "rps": 600,
        },
        slo_p95_latency_ms=250,
        slo_error_rate_pct=0.5,
        dependencies=("users-db", "redis-cache"),
        root_operation="GET /api/users/profile",
        db_statement="SELECT * FROM users WHERE id = $1",
    ),
}

DB_MAX_CONNECTIONS = 250
POOL_MAX_CONNECTIONS = 100


def get_profile(service: str) -> ServiceProfile:
    try:
        return SERVICES[service]
    except KeyError as exc:
        raise KeyError(f"unknown service {service!r}") from exc
