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
| 1 Foundation | repo, settings, DB models, Alembic, FastAPI health, Next.js shell, Compose | ☐ |
| 2 Simulator | world model, 7 fault scenarios, telemetry + action + sandbox API | ☐ |
| 3 Tools | 17 tools, registry, permissions, retries, timeouts, structured results | ☐ |
| 4 LangGraph | typed state, 25 nodes, 3 loops, bounded iterations, checkpoints | ☐ |
| 5 LLM | gateway, providers, router, cost ledger, simulated reasoner | ☐ |
| 6 RAG | corpus, chunking, embeddings, pgvector, FTS, RRF, reranker, citations | ☐ |
| 7 Validation | validators, sandbox, critical unsafe-scaling test | ☐ |
| 8 Human approval | interrupt/resume, approve/reject/more-investigation API + UI | ☐ |
| 9 Remediation | execution through allow-listed tools against simulator | ☐ |
| 10 Verification | pre/post comparison, automatic rollback on failure | ☐ |
| 11 Observability | OTel traces, Prometheus metrics, Grafana dashboard, cost endpoint | ☐ |
| 12 Evaluation | eval dataset (≥10), harness, metrics, CI threshold gate | ☐ |
| 13 Security | JWT, RBAC, rate limiting, prompt-injection defence, audit redaction | ☐ |
| 14 Deployment | Dockerfiles, Compose, K8s manifests, Azure architecture | ☐ |
| 15 Polish | README, ADRs, diagrams, demo script | ☐ |

Each milestone follows: implement → run → test → fix → verify → commit.

## 4. Phase log

(Filled in as phases complete, with the commands run and their results.)
