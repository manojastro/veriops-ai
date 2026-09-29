"use client";

import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";

import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, Empty, Input, Table, Td, Textarea, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { useAuth, usePoll } from "@/lib/hooks";
import type { DocumentCitation } from "@/lib/types";

interface DocumentRow {
  id: string;
  title: string;
  document_type: string;
  service: string | null;
  version: string;
  flagged_injection: boolean;
  chunks: number;
}

const TYPES = ["runbook", "service_doc", "architecture", "incident_report", "deployment_procedure", "database_guide", "rollback_procedure", "slo"];

export default function KnowledgePage() {
  const { can } = useAuth();
  const docs = usePoll<DocumentRow[]>("/api/knowledge/documents");
  const [query, setQuery] = useState("checkout-service connection pool exhausted after deployment");
  const [service, setService] = useState("checkout-service");
  const [results, setResults] = useState<DocumentCitation[] | null>(null);
  const [draft, setDraft] = useState({ title: "", document_type: "runbook", service: "", content: "" });
  const [message, setMessage] = useState<string | null>(null);

  async function search(event?: FormEvent) {
    event?.preventDefault();
    const params = new URLSearchParams({ q: query, top_k: "6" });
    if (service) params.set("service", service);
    setResults(await api<DocumentCitation[]>(`/api/knowledge/search?${params}`));
  }

  async function ingest(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      const result = await api<{ document_id: string; chunks: number; flagged_injection: boolean; findings: string[] }>(
        "/api/knowledge/documents",
        { method: "POST", json: { ...draft, service: draft.service || null } },
      );
      setMessage(
        `Ingested ${result.document_id} (${result.chunks} chunks)${result.flagged_injection ? ` — flagged for prompt injection: ${result.findings.join(", ")}` : ""}`,
      );
      setDraft({ title: "", document_type: "runbook", service: "", content: "" });
      void docs.refresh();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <PageHeader title="Operational knowledge" description="Hybrid retrieval: pgvector similarity + PostgreSQL full-text → reciprocal rank fusion → reranking" />
      <Card>
        <CardHeader title="Search" description="Scores are shown for every stage of the hybrid pipeline" />
        <CardBody>
          <form onSubmit={search} className="flex gap-2">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Query" />
            <Input value={service} onChange={(e) => setService(e.target.value)} placeholder="service filter" className="max-w-[14rem]" />
            <Button type="submit"><Search className="h-4 w-4" /> Search</Button>
          </form>
          {results && (
            <div className="mt-4 space-y-2">
              {results.map((r) => (
                <div key={r.chunk_id} className="rounded-md border border-slate-200 p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-medium">{r.title}</span>
                    <Badge>{r.document_type}</Badge>
                    {r.flagged_injection && <Badge tone="danger">prompt injection neutralised</Badge>}
                    <span className="tabular ml-auto text-xs text-slate-500">
                      vector {r.vector_score.toFixed(3)} · keyword {r.keyword_score.toFixed(3)} · RRF {r.fused_score.toFixed(4)} · rerank{" "}
                      <span className="font-semibold text-slate-800">{r.rerank_score.toFixed(3)}</span>
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-xs text-slate-600">{r.snippet.slice(0, 500)}</p>
                  <div className="mt-1 font-mono text-[11px] text-slate-400">citation: {r.chunk_id}</div>
                </div>
              ))}
              {!results.length && <Empty>No results.</Empty>}
            </div>
          )}
        </CardBody>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Documents" description={`${docs.data?.length ?? 0} documents indexed`} />
          <Table>
            <thead>
              <tr>
                <Th>Title</Th>
                <Th>Type</Th>
                <Th>Service</Th>
                <Th>Version</Th>
                <Th className="text-right">Chunks</Th>
                <Th>Safety</Th>
              </tr>
            </thead>
            <tbody>
              {(docs.data ?? []).map((d) => (
                <tr key={d.id}>
                  <Td className="font-medium">{d.title}</Td>
                  <Td><Badge>{d.document_type}</Badge></Td>
                  <Td className="text-xs">{d.service ?? "all"}</Td>
                  <Td className="text-xs">v{d.version}</Td>
                  <Td className="tabular text-right">{d.chunks}</Td>
                  <Td>{d.flagged_injection ? <Badge tone="danger">flagged</Badge> : <Badge tone="success">clean</Badge>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader title="Ingest document" description="Documents are scanned for prompt injection and treated as untrusted data" />
          <CardBody>
            {can("knowledge:write") ? (
              <form onSubmit={ingest} className="space-y-2">
                <Input placeholder="Title" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required />
                <div className="flex gap-2">
                  <select
                    className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm"
                    value={draft.document_type}
                    onChange={(e) => setDraft({ ...draft, document_type: e.target.value })}
                  >
                    {TYPES.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  <Input placeholder="service (optional)" value={draft.service} onChange={(e) => setDraft({ ...draft, service: e.target.value })} />
                </div>
                <Textarea rows={8} placeholder="Markdown content" value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} required />
                <Button type="submit">Ingest</Button>
                {message && <p className="text-xs text-slate-600">{message}</p>}
              </form>
            ) : (
              <p className="text-xs text-slate-500">Your role cannot ingest documents.</p>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
