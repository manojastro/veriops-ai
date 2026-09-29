# ADR-003: Deterministic validation is authoritative

**Status:** Accepted

## Context
LLMs produce confident, plausible, wrong remediations. Asking a second LLM to "check" the first shares its
failure modes. The flagship failure — scaling a service whose database is the bottleneck — is detectable from
numbers the system already has.

## Decision
Every remediation passes a pure-Python validator for its action class (`validators/rules.py`) plus a sandbox
simulation. Validators fail closed on missing evidence, report every check individually, can refute the
hypothesis behind a plan, and never call an LLM. Destructive statements are rejected before reaching even the
sandbox. A CI-blocking test asserts the unsafe scaling proposal is rejected.

## Consequences
+ Safety does not depend on model quality; behaviour is explainable check-by-check.
+ Rejections become evidence that steers the next hypothesis.
− Validators encode domain knowledge and must be maintained alongside runbooks; unknown action types are
  rejected (by design).
