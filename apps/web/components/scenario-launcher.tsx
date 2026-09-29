"use client";

import { Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Button, Card, CardBody, CardHeader } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { useAuth, usePoll } from "@/lib/hooks";
import type { Incident, Scenario } from "@/lib/types";

export function ScenarioLauncher({ compact = false }: { compact?: boolean }) {
  const { can } = useAuth();
  const router = useRouter();
  const { data } = usePoll<Scenario[]>("/api/scenarios");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = can("incidents:write");
  const investigate = can("incidents:investigate");

  async function trigger(id: string) {
    setBusy(id);
    setError(null);
    try {
      const incident = await api<Incident>("/api/incidents", {
        method: "POST",
        json: { scenario: id, auto_investigate: investigate },
      });
      router.push(`/incidents/${incident.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  const scenarios = (data ?? []).slice(0, compact ? 5 : undefined);
  return (
    <Card>
      <CardHeader
        title="Trigger an incident"
        description="Injects a fault into the production simulator and opens an incident"
      />
      <CardBody className="space-y-2">
        {!allowed && <p className="text-xs text-slate-500">Your role cannot create incidents.</p>}
        {error && <p className="text-xs text-rose-700">{error}</p>}
        {scenarios.map((s) => (
          <div key={s.id} className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="truncate">{s.title}</span>
                {s.id === "checkout_db_regression" && <Badge tone="info">flagship</Badge>}
              </div>
              <div className="mt-0.5 flex gap-2 text-xs text-slate-500">
                <span>{s.service}</span>
                <span>·</span>
                <span>{s.environment}</span>
                <span>·</span>
                <span>{s.severity}</span>
              </div>
            </div>
            <Button size="sm" variant="outline" disabled={!allowed || busy !== null} onClick={() => trigger(s.id)}>
              <Play className="h-3.5 w-3.5" />
              {busy === s.id ? "Starting…" : "Trigger"}
            </Button>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}
