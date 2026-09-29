"use client";

import { IncidentTable } from "@/components/incident-table";
import { PageHeader } from "@/components/shell";
import { usePoll } from "@/lib/hooks";
import type { Incident } from "@/lib/types";

export default function InvestigationsPage() {
  const investigating = usePoll<Incident[]>("/api/incidents?status=INVESTIGATING&limit=50", 2000).data;
  const open = usePoll<Incident[]>("/api/incidents?status=OPEN&limit=50", 4000).data;
  return (
    <>
      <PageHeader title="Active investigations" description="LangGraph runs in progress and incidents not yet investigated" />
      <h2 className="mb-2 text-sm font-semibold text-slate-700">Investigating</h2>
      <IncidentTable incidents={investigating} empty="No investigations running." />
      <h2 className="mb-2 mt-6 text-sm font-semibold text-slate-700">Open (not started)</h2>
      <IncidentTable incidents={open} empty="No open incidents." />
    </>
  );
}
