# Five-minute demo

**Setup (before the call):** `make up`, open <http://localhost:3000>, sign in as `alice` / `veriops-demo`.
Optional second tab: Jaeger (<http://localhost:16686>) and Grafana (<http://localhost:3001>).

## Minute 1 — the dashboard
- KPI row: incidents, awaiting approval, MTTR, time to root cause, **unsafe AI actions blocked**, AI cost/incident.
- Service health table: live golden signals from the production simulator.
- Say: *"Every number here is computed from persisted events — nothing is hard-coded."*

## Minute 2 — start the investigation
- *Trigger an incident* → **checkout-service p95 latency and error rate SLO breach** (flagship).
- The incident page opens. The **Live investigation** checklist ticks through: alert → metrics → logs →
  traces → deployment #842 identified → runbooks + similar incident INC-2025-114 → hypotheses.
- Point at the metric charts: latency and DB pool jump right after the deployment; CPU stays flat.

## Minute 3 — the AI is wrong, and the system knows it
- *Proposed remediations* shows the red card **AI RECOMMENDATION REJECTED — Scale checkout-service to 8 replicas**.
- Walk the failed checks: CPU 37 % < 70 %; DB pool 100 %; projected 291 DB connections > 250; query 13.5× slower
  after #842; the sandbox predicts p95 4.2 s → 6.65 s and errors 11.8 % → 23.6 %.
- *Hypotheses* tab: the LLM gave "under-provisioned" 62 % confidence — the deterministic signal checks beside it
  already disagreed. Say: *"Confidence is displayed separately from evidence; validators, not the model, decide."*
- The green card: **REMEDIATION VALIDATED — roll back #842**, sandbox 4.2 s → 310 ms, 11.8 % → 0.4 %.

## Minute 4 — human approval
- Amber panel: risk **MEDIUM 51/100**, policy *require approval*, rollback strategy, evidence ids.
- Show the risk factor bars (environment, action type, blast radius, confidence, reversibility, coverage).
- Type a comment → **Approve & execute**. (Optional: sign in as `victor` first to show the viewer cannot approve.)

## Minute 5 — outcome and governance
- Status **RESOLVED**; *Service recovered: p95 4200 → 310 ms, errors 11.8 % → 0.4 %*.
- *Timeline* (every step, timestamps), *Audit log* (append-only, human + agent + tool decisions),
  *AI cost* (by agent and by model — routing sends cheap tasks to the small model), *Report* (Markdown/JSON
  post-mortem with rejected hypotheses and rejected remediations).
- *Evaluations* page → **Run evaluation**: 15 cases, root-cause accuracy 1.0, unsafe rejection 9/9, 0 unsafe
  executions — the same gates block merges in CI.

## Backup: terminal-only
```bash
make demo-offline            # full flagship run in-process, coloured timeline in the terminal
make test-critical           # the safety-critical tests
make eval                    # evaluation table + gates
```
