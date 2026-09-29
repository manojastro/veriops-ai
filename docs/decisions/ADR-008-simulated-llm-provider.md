# ADR-008: Deterministic simulated LLM provider as the default

**Status:** Accepted

## Context
The platform must run end to end in CI and on a laptop without API keys, and the demo must reliably show an
AI mistake being caught.

## Decision
`LLM_PROVIDER=simulated` routes every task to transparent heuristics that consume the *same structured
context and schemas* as real providers. It deliberately reproduces two real failure modes: symptom-driven
"scale it out" reasoning, and obeying instructions embedded in documents when the sanitiser is disabled.
Token usage is estimated and priced as the routed model for cost reporting (flagged as estimated).

## Consequences
+ Reproducible demos, evaluations and CI; safety claims are tested against a model that *does* make mistakes.
− Evaluation metrics with the simulated provider measure the pipeline and the safety net, not model quality.
  Real providers (OpenAI, Azure OpenAI, Anthropic, OpenAI-compatible) are one environment variable away.
