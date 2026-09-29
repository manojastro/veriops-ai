// Types mirroring the VeriOps API (see /openapi.json).

export type Role = "viewer" | "engineer" | "approver" | "administrator";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  permissions: string[];
}

export interface Alert {
  service: string;
  environment: string;
  title: string;
  p95_latency_ms: number;
  error_rate_pct: number;
  cpu_pct: number;
  memory_pct: number;
  db_pool_utilization_pct: number;
  fired_at: string;
}

export interface Hypothesis {
  id: string;
  title: string;
  category: string;
  description: string;
  probability: number;
  evidence_for: string[];
  evidence_against: string[];
  required_tests: string[];
  suspect_deployment_id: string | null;
  status: "PROPOSED" | "SUPPORTED" | "REFUTED" | "INCONCLUSIVE";
  rank: number;
}

export interface SignalCheck {
  name: string;
  observed: string;
  expectation: string;
  holds: boolean;
  evidence_ref: string | null;
}

export interface HypothesisTest {
  hypothesis_id: string;
  verdict: string;
  llm_confidence: number;
  evidence_coverage: number;
  calibrated_confidence: number;
  reasoning: string;
  signals: SignalCheck[];
  evidence_refs: string[];
  missing_evidence: string[];
  iteration: number;
  tested_at: string;
}

export interface RemediationAction {
  action_type: string;
  service: string;
  environment: string;
  replicas: number | null;
  deployment_id: string | null;
  config_key: string | null;
  config_value: string | null;
  sql: string | null;
}

export interface RemediationPlan {
  id: string;
  hypothesis_id: string;
  action: RemediationAction;
  rationale: string;
  expected_impact: string;
  rollback_strategy: string;
  evidence_refs: string[];
  citations: string[];
  attempt: number;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
  observed: Record<string, unknown>;
}

export interface SandboxTest {
  name: string;
  passed: boolean;
  threshold: string;
  observed: string;
}

export interface SandboxResult {
  action: string;
  before: Record<string, number>;
  after: Record<string, number>;
  tests: SandboxTest[];
  passed: boolean;
}

export interface ValidationResult {
  id: string;
  plan_id: string;
  validator: string;
  status: "ACCEPTED" | "REJECTED";
  failed_checks: ValidationCheck[];
  passed_checks: ValidationCheck[];
  sandbox: SandboxResult | null;
  refutes_hypothesis: boolean;
  summary: string;
  validated_at: string;
}

export interface RiskFactor {
  name: string;
  weight: number;
  value: number;
  contribution: number;
  explanation: string;
}

export interface RiskAssessment {
  score: number;
  level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  factors: RiskFactor[];
  policy_decision: string;
  policy_reasons: string[];
  required_approvals: number;
}

export interface ApprovalRequest {
  incident_id: string;
  plan_id: string;
  proposed_remediation: string;
  action: RemediationAction;
  expected_impact: string;
  rollback_strategy: string;
  root_cause: string | null;
  evidence: string[];
  validation: { status: string; passed: number; failed: number };
  risk: { score: number; level: string; policy: string };
  requires_comment: boolean;
}

export interface VerificationResult {
  passed: boolean;
  before: Record<string, number>;
  after: Record<string, number>;
  checks: ValidationCheck[];
}

export interface DocumentCitation {
  document_id: string;
  chunk_id: string;
  title: string;
  document_type: string;
  snippet: string;
  vector_score: number;
  keyword_score: number;
  fused_score: number;
  rerank_score: number;
  flagged_injection: boolean;
}

export interface GraphState {
  hypotheses?: Hypothesis[];
  tested_hypotheses?: HypothesisTest[];
  selected_hypothesis?: Hypothesis | null;
  proposed_remediation?: RemediationPlan | null;
  risk?: RiskAssessment | null;
  approval?: { status: string; actor: string; role: string; comment: string; decided_at: string } | null;
  approval_status?: string;
  execution_result?: { success: boolean; detail: string; action: string } | null;
  verification_result?: VerificationResult | null;
  rollback_result?: { success: boolean; action: string; detail: string } | null;
  root_cause_confidence?: number;
  investigation_iterations?: number;
  remediation_attempts?: number;
  retrieved_runbooks?: DocumentCitation[];
  similar_incidents?: DocumentCitation[];
  slo?: { p95_latency_ms: number; error_rate_pct: number };
  pre_remediation_metrics?: Record<string, number>;
  service_criticality?: string;
  awaiting_approval?: boolean;
  approval_request?: ApprovalRequest | null;
  next_nodes?: string[];
}

export interface Incident {
  id: string;
  title: string;
  service: string;
  environment: string;
  severity: string;
  status: string;
  scenario: string | null;
  alert: Alert;
  root_cause: string | null;
  root_cause_category: string | null;
  current_node: string | null;
  risk_level: string | null;
  risk_score: number | null;
  approval_status: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  has_report?: boolean;
  counts?: Record<string, number>;
  state?: GraphState;
}

export interface TimelineEvent {
  seq: number;
  id: string;
  ts: string;
  type: string;
  title: string;
  detail: string;
  node: string | null;
  status: "info" | "success" | "warning" | "danger";
  data: Record<string, unknown>;
}

export interface EvidenceItem {
  id: string;
  kind: string;
  source_tool: string | null;
  summary: string;
  facts: Record<string, unknown>;
  collected_at: string;
}

export interface MetricsSnapshot {
  phase: string;
  p95_latency_ms: number;
  error_rate_pct: number;
  cpu_pct: number;
  memory_pct: number;
  db_pool_utilization_pct: number;
  series: Array<Record<string, number | string>>;
  captured_at: string;
}

export interface ToolCall {
  id: string;
  tool: string;
  permission: string;
  arguments: Record<string, unknown>;
  success: boolean;
  attempts: number;
  latency_ms: number;
  cached: boolean;
  error: string | null;
  created_at: string;
}

export interface AuditEvent {
  seq: number;
  id: string;
  ts: string;
  actor: string;
  actor_type: string;
  agent: string | null;
  tool: string | null;
  action: string;
  decision: string | null;
  reason: string | null;
  approval: string | null;
  execution: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface CostBreakdown {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface IncidentCost {
  calls: number;
  failed_calls: number;
  cached_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  by_agent: Record<string, CostBreakdown>;
  by_model: Record<string, CostBreakdown>;
  providers: string[];
  estimated: boolean;
}

export interface Scenario {
  id: string;
  title: string;
  service: string;
  environment: string;
  severity: string;
  description: string;
  fault: string;
}

export interface ServiceOverview {
  name: string;
  team: string;
  criticality: string;
  environment: string;
  replicas: number;
  slo_p95_latency_ms: number;
  slo_error_rate_pct: number;
  metrics: Record<string, number>;
  health: { healthy: boolean; checks: Record<string, boolean> };
}

export interface Kpis {
  incidents_total: number;
  incidents_investigated: number;
  incidents_resolved: number;
  incidents_awaiting_approval: number;
  mttd_seconds: number | null;
  mean_time_to_root_cause_seconds: number | null;
  mttr_seconds: number | null;
  autonomous_investigation_success_rate: number | null;
  root_cause_accuracy: number | null;
  unsafe_action_rejection_rate: number | null;
  remediations_proposed: number;
  remediations_rejected_by_validation: number;
  approval_rate: number | null;
  rollback_rate: number | null;
  total_ai_cost_usd: number;
  mean_ai_cost_per_incident_usd: number | null;
  ai_cost_per_resolved_incident_usd: number | null;
  mean_investigation_steps: number | null;
  latest_evaluation_id: string | null;
}

export interface EvaluationCaseResult {
  incident_id: string;
  scenario: string;
  passed: boolean;
  final_status: string;
  expected_final_status: string;
  root_cause_category: string | null;
  root_cause_correct: boolean;
  remediation_action: string | null;
  proposed_actions: string[];
  unsafe_proposed: string[];
  unsafe_rejected: string[];
  evidence_precision: number;
  evidence_recall: number;
  graph_steps: number;
  llm_cost_usd: number;
  failures: string[];
}

export interface EvaluationRun {
  id: string;
  status: string;
  provider: string;
  scenario_count: number;
  metrics: Record<string, unknown>;
  passed_thresholds: boolean;
  started_at: string;
  finished_at: string | null;
  results?: EvaluationCaseResult[];
}
