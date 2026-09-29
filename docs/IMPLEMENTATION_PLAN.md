# VeriOps AI — Implementation Plan

> Status legend: ☐ not started · ◐ in progress · ☑ verified (with evidence in the phase log below)

## 0. Starting point

The repository contained only a placeholder `README.md`. Everything is built from scratch.

Environment used for development and verification:

| Tool | Version |
|---|---|
| Python | 3.12 (uv-managed venv) |
| Node | 22 |
| Docker / Compose | 29 / v5 |
| PostgreSQL | 16 + pgvector (`pgvector/pgvector:pg16`) |

## 1. Target repository architecture

```
veriops-ai/
├── src/veriops/                 # Python backend — one distribution, bounded-context subpackages
│   ├── shared/                  # settings, ids, clock, errors, redaction, logging
│   ├── domain/                  # Pydantic domain models (evidence, hypothesis, remediation, risk…)
│   ├── db/                      # SQLAlchemy models, engine/session, repositories
│   ├── simulator/               # production world model, fault scenarios, telemetry, simulator API
│   ├── tools/                   # MCP-style tool registry, permissions, telemetry backends
│   ├── llm/                     # provider abstraction, model router, cost ledger, simulated provider
│   ├── agents/                  # coordinator, evidence, root-cause, remediation, report agents
│   ├── rag/                     # ingestion, chunking, embeddings, pgvector/in-memory stores, hybrid search
│   ├── validators/              # deterministic remediation validators + sandbox validation
│   ├── policy/                  # policy engine (action classes) + deterministic risk engine
│   ├── graph/                   # LangGraph state, nodes, routing, builder, runner
│   ├── observability/           # OpenTelemetry tracing + Prometheus metrics
│   ├── security/                # JWT auth, RBAC, rate limiting, prompt-injection sanitizer
│   ├── evaluation/              # evaluation harness + metrics
│   ├── api/                     # FastAPI application and routers
│   └── worker/                  # background investigation worker (Redis queue)
├── migrations/                  # Alembic migrations
├── knowledge/                   # operational knowledge corpus (runbooks, past incidents, SLOs…)
├── evals/incidents.json         # evaluation dataset (≥10 scenarios)
├── apps/web/                    # Next.js + TypeScript + Tailwind operations console
├── infra/
│   ├── docker/                  # Dockerfiles (api, worker, simulator, web)
│   ├── kubernetes/              # manifests (read-only RBAC by default)
│   ├── terraform/azure/         # optional Azure reference architecture
│   ├── otel/ prometheus/ grafana/
├── docs/{architecture,decisions,security,demos}/
├── scripts/                     # demo_incident.py, seed scripts
├── tests/{unit,integration,e2e,evals}/
├── docker-compose.yml  Makefile  .env.example  README.md
```

**Deviation from the suggested layout (documented in ADR-007):** the prompt suggested
`packages/agents`, `packages/graph`, … as separate packages. Separate pip distributions in a
single repo add versioning and install friction without adding isolation. We keep the same
bounded contexts as *subpackages* of one `veriops` distribution, and enforce direction of
dependencies by convention (`domain` ← everything; `graph` → agents/tools/validators/policy;
`api` → graph/db). The Next.js app lives in `apps/web`.

## 2. Key design decisions (safe defaults)

| Decision | Choice | Why |
|---|---|---|
| LLM access | `LLMGateway` with providers `simulated`, `openai`, `azure_openai`, `anthropic`, `openai_compatible` | Runs offline and in CI; real providers are opt-in by env vars |
| Default provider | `simulated` — deterministic reasoner over structured context | No keys required; reproducible demo & evals. It deliberately reproduces a common LLM failure mode (symptom-driven "just scale it") so the deterministic safety net is demonstrable |
| Structured output | Every LLM call is `structured(task, context, schema)` returning a Pydantic model | No free-text parsing |
| Graph persistence | LangGraph `PostgresSaver` (in-memory saver in unit tests) | Durable pause/resume for approvals |
| Graph state | TypedDict with Pydantic models and reducers; checkpoint serializer allow-list | Typed and safe to deserialize |
| Embeddings | Deterministic feature-hashing embedder by default; OpenAI/Azure embeddings optional | Works offline; clearly documented quality trade-off |
| Hybrid retrieval | pgvector cosine + Postgres `ts_rank_cd` full text → Reciprocal Rank Fusion → deterministic reranker | Not vector-only; scores exposed |
| Validation | Pure-Python validators + sandbox simulation; LLM never approves its own plan | Core safety principle |
| Risk | Deterministic weighted factor model 0–100 | LLM cannot invent the final score |
| Execution | Tool permission classes `READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `BLOCKED` checked in the tool registry *and* in graph nodes | Defence in depth |
| Telemetry source | Simulator service over HTTP (same interface as future real integrations); in-process backend for tests | Real integrations can replace it |
| Background work | Redis-backed worker in Compose; in-process runner fallback | API stays responsive |
| Live updates | Server-Sent Events backed by persisted `incident_events` | Survives restarts, works across processes |
| Auth | JWT + RBAC (viewer / engineer / approver / administrator) | Secure defaults; seeded demo users for local only |

## 3. Milestones

| Phase | Scope | Status |
|---|---|---|
| 1 Foundation | repo, settings, DB models, Alembic, FastAPI health, Next.js shell, Compose | ☑ |
| 2 Simulator | world model, 12 fault scenarios, telemetry + action + sandbox API | ☑ |
| 3 Tools | 29 tools, registry, permissions, retries, timeouts, structured results | ☑ |
| 4 LangGraph | typed state, 26 nodes, 3 loops, bounded iterations, checkpoints | ☑ |
| 5 LLM | gateway, providers, router, cost ledger, simulated reasoner | ☑ (real providers implemented, not exercised: no keys) |
| 6 RAG | corpus, chunking, embeddings, pgvector, FTS, RRF, reranker, citations | ☑ |
| 7 Validation | validators, sandbox, critical unsafe-scaling test | ☑ |
| 8 Human approval | interrupt/resume, approve/reject/more-investigation API + UI | ☑ |
| 9 Remediation | execution through allow-listed tools against simulator | ☑ |
| 10 Verification | pre/post comparison, automatic rollback on failure | ☑ |
| 11 Observability | OTel traces, Prometheus metrics, Grafana dashboard, cost endpoint | ☑ (Langfuse exporter not wired) |
| 12 Evaluation | eval dataset (15), harness, metrics, CI threshold gate | ☑ |
| 13 Security | JWT, RBAC, rate limiting, prompt-injection defence, audit redaction | ☑ |
| 14 Deployment | Dockerfiles, Compose, K8s manifests, Azure Terraform | ☑ (K8s/Terraform schema validation runs in CI only) |
| 15 Polish | README, ADRs, diagrams, demo script, screenshots | ☑ |

Each milestone followed: implement → run → test → fix → verify → commit.

## 4. Phase log (evidence)

### Foundation, simulator, tools (commit `c02c275`)
- `alembic upgrade head` → `downgrade base` → `upgrade head`: success; 23 tables.
- `UPDATE audit_events …` → rejected by trigger (`audit_events is append-only`).
- Simulator flagship: p95 4200 ms / errors 11.8 % / CPU 37 % / memory 52 % / pool 100 %; sandbox rollback →
  310 ms / 0.4 %; sandbox scale 3→8 → 6650 ms / 23.6 %, DB 291/250 connections.
- Tool registry smoke: HIGH_RISK_WRITE without approval refused; `DROP TABLE` refused by schema and by BLOCKED tool.

### Graph, LLM, RAG, validation (commit `c02c275`)
- All 12 scenarios resolved in-process with the correct root-cause category; staging scenario auto-executed at LOW risk.
- Hybrid search returns INC-2025-114 and the checkout runbook for the flagship query; poisoned document flagged
  with 5 finding types and neutralised.

### API, persistence, security, evaluation (commit `99f4550`)
- Live HTTP run on PostgreSQL: create → investigate → AWAITING_APPROVAL (31 events) → approve (alice) → RESOLVED.
- RBAC: engineer approve → 403; viewer create → 403; no token → 401; bad password → 401.
- `veriops-eval`: 15/15 cases pass; gates pass (accuracy 1.0, unsafe rejection 9/9, 0 unsafe executed).
- pytest: 122 passed (unit + evals); 7 passed (integration).

### Frontend, packaging, CI (commit `9a9034a`)
- `tsc --noEmit` clean; Vitest 6 passed; `next build` succeeded (11 routes).
- `docker compose up`: 10 services running; API and web healthy; Prometheus targets `veriops-api` and
  `veriops-worker` up; Jaeger receives traces from both; Grafana dashboard provisioned.
- Playwright (Chromium) against Compose in worker mode: flagship demo and viewer RBAC test — 2 passed.
- `scripts/demo_incident.py --approve` against Compose: RESOLVED, 8 LLM calls, $0.022 estimated.
- mypy: no issues in 98 files; ruff clean; bandit: no findings; pip-audit and npm audit: 0 vulnerabilities.

### Hardening
- Rate limiter split into read/write budgets after measuring that the live console's polling (~150 req/min)
  exceeded the single 120/min budget; login limiter covered by an integration test (8 integration tests pass).
- Measured API p95 10–24 ms; graph first node ~0.19 s after `POST /api/incidents`; full investigation to the
  approval gate ~0.7 s (simulated provider).

## 5. Known gaps (see README → Limitations)
Real telemetry backends; real-LLM evaluation run; approval-timeout sweeper; Langfuse exporter; Redis-backed rate
limiting; SSO; automatic ingestion of new post-mortems into the knowledge base.
