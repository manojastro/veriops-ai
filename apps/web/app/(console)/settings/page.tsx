"use client";

import { PageHeader } from "@/components/shell";
import { Badge, Card, CardBody, CardHeader, Table, Td, Th } from "@/components/ui/primitives";
import { usePoll } from "@/lib/hooks";

interface SettingsView {
  environment: string;
  llm_provider: string;
  embedding_provider: string;
  execution_mode: string;
  model_routing: Array<{ task: string; tier: string; model: string }>;
  limits: Record<string, number>;
  risk_weights: Record<string, number>;
  risk_levels: Record<string, string>;
  policy: Record<string, string | string[]>;
  roles: Record<string, string[]>;
  tools: Array<{ name: string; description: string; annotations: { permission: string } }>;
}

const PERMISSION_TONE: Record<string, "neutral" | "warning" | "danger"> = {
  READ_ONLY: "neutral",
  LOW_RISK_WRITE: "warning",
  HIGH_RISK_WRITE: "warning",
  BLOCKED: "danger",
};

export default function SettingsPage() {
  const s = usePoll<SettingsView>("/api/settings").data;
  if (!s) return null;
  return (
    <>
      <PageHeader title="Settings & policies" description="Effective configuration. Secrets are never returned by the API." />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Runtime" />
          <CardBody className="grid grid-cols-2 gap-2 text-sm">
            <div>Environment</div><div className="font-medium">{s.environment}</div>
            <div>LLM provider</div><div className="font-medium">{s.llm_provider}</div>
            <div>Embeddings</div><div className="font-medium">{s.embedding_provider}</div>
            <div>Execution mode</div><div className="font-medium">{s.execution_mode}</div>
            {Object.entries(s.limits).flatMap(([k, v]) => [
              <div key={`${k}-k`} className="text-slate-600">{k.replace(/_/g, " ")}</div>,
              <div key={`${k}-v`} className="tabular font-medium">{v}</div>,
            ])}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Model routing" description="Cheap models for simple tasks, strong models for root-cause reasoning" />
          <Table>
            <thead><tr><Th>Task</Th><Th>Tier</Th><Th>Model</Th></tr></thead>
            <tbody className="text-sm">
              {s.model_routing.map((r) => (
                <tr key={r.task}><Td className="font-mono text-xs">{r.task}</Td><Td>{r.tier}</Td><Td className="font-mono text-xs">{r.model}</Td></tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader title="Policy engine" />
          <CardBody className="space-y-1.5 text-sm">
            {Object.entries(s.policy).map(([k, v]) => (
              <div key={k} className="grid grid-cols-3 gap-2">
                <div className="font-medium">{k.replace(/_/g, " ")}</div>
                <div className="col-span-2 text-slate-600">{Array.isArray(v) ? v.join(", ") : v}</div>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Risk model" description={Object.entries(s.risk_levels).map(([k, v]) => `${k} ${v}`).join(" · ")} />
          <CardBody className="space-y-1 text-sm">
            {Object.entries(s.risk_weights).map(([k, v]) => (
              <div key={k} className="flex justify-between"><span className="font-mono text-xs">{k}</span><span className="tabular">{v}</span></div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Roles (RBAC)" />
          <CardBody className="space-y-2 text-sm">
            {Object.entries(s.roles).map(([role, perms]) => (
              <div key={role}>
                <div className="font-medium">{role}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">{perms.map((p) => <Badge key={p}>{p}</Badge>)}</div>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Tool allow-list" description="MCP-style tools with permission classes" />
          <Table>
            <thead><tr><Th>Tool</Th><Th>Permission</Th></tr></thead>
            <tbody className="text-xs">
              {s.tools.map((t) => (
                <tr key={t.name}>
                  <Td><div className="font-mono">{t.name}</div><div className="text-slate-500">{t.description}</div></Td>
                  <Td><Badge tone={PERMISSION_TONE[t.annotations.permission]}>{t.annotations.permission}</Badge></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      </div>
    </>
  );
}
