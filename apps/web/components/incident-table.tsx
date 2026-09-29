"use client";

import Link from "next/link";

import { Card, Empty, StatusBadge, Table, Td, Th } from "@/components/ui/primitives";
import { fmtDateTime, fmtMs, fmtPct, titleCase } from "@/lib/format";
import type { Incident } from "@/lib/types";

export function IncidentTable({ incidents, empty }: { incidents: Incident[] | null; empty: string }) {
  return (
    <Card>
      <Table>
        <thead>
          <tr>
            <Th>Incident</Th>
            <Th>Service</Th>
            <Th>Severity</Th>
            <Th>Status</Th>
            <Th>Stage</Th>
            <Th className="text-right">p95 at alert</Th>
            <Th className="text-right">Errors at alert</Th>
            <Th>Root cause</Th>
            <Th>Risk</Th>
            <Th>Opened</Th>
          </tr>
        </thead>
        <tbody>
          {(incidents ?? []).map((i) => (
            <tr key={i.id} className="hover:bg-slate-50">
              <Td>
                <Link href={`/incidents/${i.id}`} className="font-medium hover:underline">
                  {i.id}
                </Link>
                <div className="max-w-xs truncate text-xs text-slate-500">{i.title}</div>
              </Td>
              <Td>
                {i.service}
                <div className="text-xs text-slate-500">{i.environment}</div>
              </Td>
              <Td>{i.severity}</Td>
              <Td>
                <StatusBadge status={i.status} />
              </Td>
              <Td className="text-xs text-slate-600">{i.current_node ? titleCase(i.current_node) : "—"}</Td>
              <Td className="tabular text-right">{fmtMs(i.alert.p95_latency_ms)}</Td>
              <Td className="tabular text-right">{fmtPct(i.alert.error_rate_pct)}</Td>
              <Td className="max-w-xs text-xs">{i.root_cause ?? "—"}</Td>
              <Td>{i.risk_level ? <StatusBadge status={i.risk_level} /> : "—"}</Td>
              <Td className="text-xs text-slate-500">{fmtDateTime(i.created_at)}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!incidents?.length && (
        <div className="p-4">
          <Empty>{empty}</Empty>
        </div>
      )}
    </Card>
  );
}
