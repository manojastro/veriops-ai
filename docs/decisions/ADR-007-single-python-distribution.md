# ADR-007: One Python distribution with bounded-context subpackages

**Status:** Accepted

## Context
The brief suggested `packages/agents`, `packages/graph`, … as separate packages.

## Decision
Keep one `veriops` distribution under `src/` with subpackages per bounded context (`domain`, `tools`, `llm`,
`agents`, `rag`, `validators`, `policy`, `graph`, `api`, …). Dependency direction is by convention: `domain` is
imported by everything and imports nothing internal; `graph` orchestrates agents/tools/validators/policy; `api`
depends on `graph` and `db`.

## Consequences
+ No version skew or multi-package install friction; one lint/type/test configuration.
+ The same boundaries as separate packages, cheaper to refactor.
− Boundaries are not enforced by the packaging system (an import-linter contract would be a cheap addition).
