# ADR-004: Human approval via graph interrupts

**Status:** Accepted

## Context
Some changes (production rollbacks, configuration changes, schema changes) should not be autonomous even when
validated. Approvals may take a long time and must survive restarts.

## Decision
The policy engine decides from the deterministic risk level: LOW may auto-execute (except production
HIGH_RISK_WRITE tools), MEDIUM needs approval, HIGH needs approval + justification + a fresh sandbox run,
CRITICAL/destructive is blocked. Approval is a LangGraph `interrupt()` in `await_approval`; side effects of
requesting approval live in a separate node so they run exactly once. The decision is re-validated inside the
graph (role and justification), not only in the API.

## Consequences
+ Durable, auditable pauses; approve / reject / request-more-investigation map to graph routes.
+ Defence in depth against API bypass.
− Approval timeouts need a sweeper (modelled, not yet implemented).
