"use client";

import { CheckCircle2, RotateCcw, Search, XCircle } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, CardBody, CardHeader, StatusBadge, Textarea } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks";
import type { ApprovalRequest } from "@/lib/types";

type Decision = "approve" | "reject" | "request-more-investigation";

export function ApprovalPanel({
  incidentId,
  request,
  onDecided,
}: {
  incidentId: string;
  request: ApprovalRequest;
  onDecided?: () => void;
}) {
  const { can } = useAuth();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = can("incidents:approve");

  async function decide(decision: Decision) {
    if (decision === "approve" && request.requires_comment && !comment.trim()) {
      setError("HIGH-risk approvals require a justification.");
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      await api(`/api/incidents/${incidentId}/${decision}`, { method: "POST", json: { comment } });
      onDecided?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="border-amber-300 ring-1 ring-amber-200">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Human approval required <StatusBadge status={request.risk.level} />
          </span>
        }
        description={`Policy: ${request.risk.policy.replace(/_/g, " ").toLowerCase()} · risk score ${request.risk.score.toFixed(0)}/100`}
      />
      <CardBody className="space-y-3 text-sm">
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Proposed remediation</div>
          <div className="mt-0.5 font-medium">{request.proposed_remediation}</div>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Root cause</div>
            <div className="mt-0.5">{request.root_cause ?? "—"}</div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Expected impact</div>
            <div className="mt-0.5">{request.expected_impact}</div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Validation</div>
            <div className="mt-0.5 flex items-center gap-2">
              <StatusBadge status={request.validation.status} />
              <span className="text-slate-600">
                {request.validation.passed} checks passed, {request.validation.failed} failed
              </span>
            </div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Rollback strategy</div>
            <div className="mt-0.5">{request.rollback_strategy}</div>
          </div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Evidence</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {request.evidence.map((e) => (
              <Badge key={e}>{e}</Badge>
            ))}
          </div>
        </div>
        {allowed ? (
          <>
            <Textarea
              rows={2}
              placeholder={request.requires_comment ? "Justification (required for HIGH risk)" : "Comment (optional)"}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            {error && <div className="text-xs text-rose-700">{error}</div>}
            <div className="flex flex-wrap gap-2">
              <Button variant="success" disabled={busy !== null} onClick={() => decide("approve")}>
                <CheckCircle2 className="h-4 w-4" /> {busy === "approve" ? "Approving…" : "Approve & execute"}
              </Button>
              <Button variant="danger" disabled={busy !== null} onClick={() => decide("reject")}>
                <XCircle className="h-4 w-4" /> Reject
              </Button>
              <Button variant="outline" disabled={busy !== null} onClick={() => decide("request-more-investigation")}>
                <Search className="h-4 w-4" /> Request more investigation
              </Button>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 rounded bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <RotateCcw className="h-3.5 w-3.5" /> Waiting for an approver. Your role cannot approve remediations.
          </div>
        )}
      </CardBody>
    </Card>
  );
}
