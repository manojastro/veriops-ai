"use client";

import { useState } from "react";

import { IncidentTable } from "@/components/incident-table";
import { ScenarioLauncher } from "@/components/scenario-launcher";
import { PageHeader } from "@/components/shell";
import { usePoll } from "@/lib/hooks";
import type { Incident } from "@/lib/types";

const FILTERS = ["", "INVESTIGATING", "AWAITING_APPROVAL", "RESOLVED", "ROLLED_BACK", "ESCALATED", "REMEDIATION_REJECTED"];

export default function IncidentsPage() {
  const [status, setStatus] = useState("");
  const { data } = usePoll<Incident[]>(`/api/incidents?limit=100${status ? `&status=${status}` : ""}`, 4000);
  return (
    <>
      <PageHeader title="Incidents" description="All incidents with their investigation stage and outcome" />
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f || "all"}
            onClick={() => setStatus(f)}
            className={`rounded-md border px-2.5 py-1 text-xs ${status === f ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}
          >
            {f ? f.replace(/_/g, " ").toLowerCase() : "all"}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-4">
        <div className="2xl:col-span-3">
          <IncidentTable incidents={data} empty="No incidents match this filter." />
        </div>
        <ScenarioLauncher />
      </div>
    </>
  );
}
