# ADR-001: LangGraph for the investigation workflow

**Status:** Accepted

## Context
An investigation is a state machine with loops (gather more evidence, revise a rejected remediation, roll back
a failed one), a pause for human approval that can last minutes to hours, and parallel evidence collection. It
must be resumable after a process restart and inspectable step by step.

## Decision
Model the workflow as a LangGraph `StateGraph` with a typed `InvestigationState` (TypedDict of Pydantic models,
reducers for fan-out fields), explicit conditional edges driven by deterministic gate nodes, `interrupt()` for
approvals and `PostgresSaver` checkpoints.

## Consequences
+ Loops, fan-out/fan-in and human-in-the-loop are first-class; each node is a small testable function.
+ Durable pause/resume across API and worker processes (thread id = incident id).
+ The graph shape is the documentation (see `graph/builder.py`).
− A framework dependency with fast-moving APIs; mitigated by keeping business logic in agents/validators and
  only orchestration in nodes. The checkpoint serializer uses an explicit class allow-list.
