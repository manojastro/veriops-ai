"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, CardBody, CardHeader, Empty, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn, fmtTime, fmtUsd } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import type { AuditEvent, EvidenceItem, IncidentCost, TimelineEvent, ToolCall } from "@/lib/types";

const DOT: Record<TimelineEvent["status"], string> = {
  info: "bg-slate-400",
  success: "bg-emerald-600",
  warning: "bg-amber-500",
  danger: "bg-rose-600",
};

export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (!events.length) return <Empty>No events yet.</Empty>;
  return (
    <ol className="relative space-y-2 border-l border-slate-200 pl-4">
      {events.map((e) => (
        <li key={e.id} className="relative">
          <span className={cn("absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white", DOT[e.status])} />
          <div className="flex items-baseline gap-2 text-sm">
            <span className="tabular w-16 shrink-0 text-xs text-slate-500">{fmtTime(e.ts)}</span>
            <span className={cn("font-medium", e.status === "danger" && "text-rose-800", e.status === "success" && "text-emerald-800")}>{e.title}</span>
          </div>
          {e.detail && <div className="ml-[72px] mt-0.5 whitespace-pre-wrap text-xs text-slate-600">{e.detail}</div>}
        </li>
      ))}
    </ol>
  );
}

export function Evidence({ items, tools }: { items: EvidenceItem[]; tools: ToolCall[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-3">
        <CardHeader title="Evidence" description="Every conclusion references these ids" />
        <Table>
          <thead>
            <tr>
              <Th>Id</Th>
              <Th>Kind</Th>
              <Th>Summary</Th>
              <Th>Source</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id}>
                <Td className="font-mono text-xs">{e.id}</Td>
                <Td><Badge>{e.kind}</Badge></Td>
                <Td className="text-xs">{e.summary}</Td>
                <Td className="text-xs text-slate-500">{e.source_tool}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <Card className="xl:col-span-2">
        <CardHeader title="Tool calls" description="All calls pass the allow-list and permission checks" />
        <Table>
          <thead>
            <tr>
              <Th>Tool</Th>
              <Th>Permission</Th>
              <Th className="text-right">ms</Th>
              <Th>Result</Th>
            </tr>
          </thead>
          <tbody className="text-xs">
            {tools.map((t) => (
              <tr key={t.id}>
                <Td className="font-mono">{t.tool}</Td>
                <Td><Badge tone={t.permission === "READ_ONLY" ? "neutral" : "warning"}>{t.permission}</Badge></Td>
                <Td className="tabular text-right">{t.latency_ms.toFixed(0)}</Td>
                <Td>
                  {t.success ? <Badge tone="success">{t.cached ? "cached" : "ok"}</Badge> : <Badge tone="danger" >{t.error?.slice(0, 40) ?? "failed"}</Badge>}
                  {t.attempts > 1 && <span className="ml-1 text-slate-500">{t.attempts} attempts</span>}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}

export function Audit({ incidentId }: { incidentId: string }) {
  const { data } = usePoll<AuditEvent[]>(`/api/incidents/${incidentId}/audit`, 4000);
  if (!data?.length) return <Empty>No audit events.</Empty>;
  return (
    <Card>
      <CardHeader title="Audit log" description="Append-only (UPDATE/DELETE blocked in the database); sensitive fields redacted" />
      <Table>
        <thead>
          <tr>
            <Th>Time</Th>
            <Th>Actor</Th>
            <Th>Action</Th>
            <Th>Tool</Th>
            <Th>Decision</Th>
            <Th>Reason</Th>
            <Th>Approval / execution</Th>
          </tr>
        </thead>
        <tbody className="text-xs">
          {data.map((a) => (
            <tr key={a.id}>
              <Td className="tabular whitespace-nowrap text-slate-500">{fmtTime(a.ts)}</Td>
              <Td>
                {a.actor}
                <div className="text-slate-400">{a.actor_type}</div>
              </Td>
              <Td className="font-mono">{a.action}</Td>
              <Td className="font-mono">{a.tool ?? ""}</Td>
              <Td>{a.decision && <Badge tone={/REJECT|DENIED|FAIL/.test(a.decision) ? "danger" : "neutral"}>{a.decision}</Badge>}</Td>
              <Td className="max-w-md text-slate-600">{a.reason?.slice(0, 220)}</Td>
              <Td className="max-w-[12rem] truncate font-mono text-slate-500">{a.approval ?? a.execution ?? ""}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

export function Cost({ incidentId }: { incidentId: string }) {
  const { data } = usePoll<IncidentCost>(`/api/incidents/${incidentId}/cost`, 5000);
  if (!data) return null;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader title="AI cost" description={data.estimated ? "Estimated: simulated provider priced as the routed model" : data.providers.join(", ")} />
        <CardBody className="tabular space-y-1 text-sm">
          <div className="flex justify-between"><span>Total</span><span className="font-semibold">{fmtUsd(data.total_cost_usd)}</span></div>
          <div className="flex justify-between"><span>LLM calls</span><span>{data.calls} ({data.cached_calls} cached, {data.failed_calls} failed)</span></div>
          <div className="flex justify-between"><span>Prompt tokens</span><span>{data.prompt_tokens.toLocaleString()}</span></div>
          <div className="flex justify-between"><span>Completion tokens</span><span>{data.completion_tokens.toLocaleString()}</span></div>
        </CardBody>
      </Card>
      {(["by_agent", "by_model"] as const).map((key) => (
        <Card key={key}>
          <CardHeader title={key === "by_agent" ? "Cost by agent" : "Cost by model (routing)"} />
          <CardBody className="tabular space-y-1 text-sm">
            {Object.entries(data[key]).map(([name, v]) => (
              <div key={name} className="flex justify-between gap-2">
                <span className="truncate">{name}</span>
                <span className="text-slate-600">{v.calls} · {(v.prompt_tokens + v.completion_tokens).toLocaleString()} tok · {fmtUsd(v.cost_usd)}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

export function Report({ incidentId, available }: { incidentId: string; available: boolean }) {
  const { data } = usePoll<string>(available ? `/api/incidents/${incidentId}/report.md` : null);
  const [busy, setBusy] = useState(false);
  if (!available) return <Empty>The post-incident report is generated when the investigation completes.</Empty>;

  async function download(kind: "md" | "json") {
    setBusy(true);
    try {
      const body = kind === "md" ? await api<string>(`/api/incidents/${incidentId}/report.md`) : JSON.stringify(await api(`/api/incidents/${incidentId}/report`), null, 2);
      const url = URL.createObjectURL(new Blob([body], { type: kind === "md" ? "text/markdown" : "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `${incidentId}-postmortem.${kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Post-incident report"
        action={
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => download("md")}><Download className="h-3.5 w-3.5" /> Markdown</Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => download("json")}><Download className="h-3.5 w-3.5" /> JSON</Button>
          </div>
        }
      />
      <CardBody>
        <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-4 font-mono text-xs leading-relaxed text-slate-800">{data ?? "Loading…"}</pre>
      </CardBody>
    </Card>
  );
}
