"use client";

import Link from "next/link";

import { ApprovalPanel } from "@/components/incident/approval-panel";
import { PageHeader } from "@/components/shell";
import { Empty } from "@/components/ui/primitives";
import { usePoll } from "@/lib/hooks";
import type { Incident } from "@/lib/types";

function PendingApproval({ incidentId, onDecided }: { incidentId: string; onDecided: () => void }) {
  const { data } = usePoll<Incident>(`/api/incidents/${incidentId}`);
  const request = data?.state?.approval_request;
  if (!data || !request) return null;
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-sm">
        <Link href={`/incidents/${incidentId}`} className="font-medium hover:underline">
          {incidentId} · {data.title}
        </Link>
        <span className="text-xs text-slate-500">{data.service} · {data.environment}</span>
      </div>
      <ApprovalPanel incidentId={incidentId} request={request} onDecided={onDecided} />
    </div>
  );
}

export default function ApprovalsPage() {
  const { data, refresh } = usePoll<Incident[]>("/api/incidents?status=AWAITING_APPROVAL&limit=50", 3000);
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Validated remediations paused by policy until a human approver decides"
      />
      <div className="space-y-5">
        {(data ?? []).map((i) => (
          <PendingApproval key={i.id} incidentId={i.id} onDecided={() => void refresh()} />
        ))}
        {!data?.length && <Empty>No remediations are waiting for approval.</Empty>}
      </div>
    </>
  );
}
