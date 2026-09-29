# VeriOps AI — Threat model and controls

VeriOps can change production. The design goal is that **no single failure** — a wrong model, a poisoned
document, a compromised browser session, a buggy tool — can cause an unsafe production change.

## Assets
Production availability; database integrity; credentials; the audit trail; incident data (may contain PII in
logs); LLM spend.

## Threats and controls

| # | Threat | Controls | Evidence |
|---|---|---|---|
| T1 | LLM proposes a harmful fix (e.g. scaling a DB-bound service) | Deterministic validators (fail closed) + sandbox; risk engine; policy engine; approval | `test_scaling_is_rejected_when_db_pool_is_exhausted`, eval gates |
| T2 | Prompt injection via retrieved documents | Sanitiser redacts instruction-like lines and flags documents; `<untrusted_data>` prompt blocks; system prompt forbids following data; enum-constrained action schema; validators/policy downstream | `test_poisoned_document_is_flagged_and_neutralised`, eval-015 (sanitiser off → DROP proposed twice, rejected twice, nothing executed) |
| T3 | Destructive actions (DROP TABLE, delete DB, destroy infra, rotate credentials, disable security, remove backups) | Action classes exist only to be recognised; `DestructiveActionValidator` always rejects; policy `NEVER_AUTONOMOUS`; tools registered as `BLOCKED`; SQL tool accepts only `CREATE INDEX CONCURRENTLY`; simulator DB guard | `test_destructive_*`, `test_blocked_tools_never_execute` |
| T4 | Tool misuse / unknown tools | Allow-list; Pydantic input schemas; permission classes; HIGH_RISK_WRITE requires approval id; writes not retried | `tests/unit/test_tools.py` |
| T5 | Unauthorised approval | JWT + RBAC on routes; graph re-checks approver role and justification; approvals recorded with actor/role | `test_viewer_role_cannot_approve_even_if_api_is_bypassed`, integration RBAC tests |
| T6 | Audit tampering | Append-only trigger on `audit_events`; every tool/LLM/human decision recorded | `test_audit_log_is_append_only` |
| T7 | Secret leakage (logs, audit, prompts) | Recursive redaction of keys and value patterns (bearer tokens, `sk-…`, DSNs with passwords, AWS keys, private keys) before persistence; secrets only from env / Key Vault; `/api/settings` never returns secrets | `test_redaction` |
| T8 | Credential stuffing / API abuse | bcrypt hashes; login limiter (10/min); per-user read/write limits; generic 401s | `test_login_is_rate_limited` |
| T9 | Runaway agent loops / cost | Iteration and attempt budgets, recursion limit, bounded LLM/tool retries, response cache, cost ledger and dashboards | graph tests, `/api/cost` |
| T10 | Insecure deployment defaults | Production refuses dev JWT secret and demo users; non-root containers; restricted pod security; NetworkPolicies; read-only Kubernetes RBAC by default | `test_production_refuses_insecure_defaults`, `infra/kubernetes` |
| T11 | Checkpoint deserialisation attacks | LangGraph serializer restricted to an explicit allow-list of VeriOps classes | `graph/state.py::checkpoint_serializer` |
| T12 | Failed remediation makes things worse | Post-execution verification against SLO; automatic undo (restore revision / scale back / config revert) | `test_failed_verification_triggers_automatic_rollback`, eval-014 |

## Residual risks
- In-memory rate limiting is per process; enforce at ingress for multi-replica deployments.
- Regex sanitisation is a heuristic; it is one layer, not the control. The binding controls are T1/T3/T4.
- `localStorage` JWT in the console is acceptable for a demo, not for production (use httpOnly cookies / SSO).
- Real infrastructure integrations would need their own least-privilege credentials per tool.
