# ADR-005: Allow-listed, permission-classed tools

**Status:** Accepted

## Context
Agents act through tools. A prompt injection or a model error must not be able to invoke an arbitrary or
destructive capability.

## Decision
All tools are registered in one `ToolRegistry` with an input schema and a permission class: `READ_ONLY`,
`LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `BLOCKED`. Unknown tools are rejected; writes need an explicit grant from
the execution node; HIGH_RISK_WRITE needs a recorded approval id; BLOCKED tools (drop table, delete database,
destroy infrastructure, rotate credentials, disable security controls, remove backups) are registered only so
requests are recognised, audited and refused. Writes are never auto-retried. Tool descriptors follow the MCP
`tools/list` shape so an MCP server could expose the same registry.

## Consequences
+ A single choke point for authorisation, validation, retries, timeouts, caching and auditing.
+ Real integrations replace simulator backends without touching agents.
− New capabilities require code review (intended).
