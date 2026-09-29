"use client";

import { useEffect, useRef, useState } from "react";

import { api, streamUrl } from "@/lib/api";
import type { TimelineEvent } from "@/lib/types";

/**
 * Live timeline: Server-Sent Events with automatic fallback to polling (e.g. when a proxy buffers SSE).
 * Events are de-duplicated by sequence number.
 */
export function useLiveTimeline(incidentId: string, active: boolean, refreshKey?: string): TimelineEvent[] {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const lastSeq = useRef(0);
  const pollRef = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    let poller: number | null = null;

    const merge = (incoming: TimelineEvent[]) => {
      const fresh = incoming.filter((e) => e.seq > lastSeq.current);
      if (!fresh.length) return;
      lastSeq.current = Math.max(...fresh.map((e) => e.seq));
      setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.seq - b.seq));
    };

    const poll = async () => {
      try {
        merge(await api<TimelineEvent[]>(`/api/incidents/${incidentId}/timeline?after_seq=${lastSeq.current}`));
      } catch {
        /* transient */
      }
    };

    pollRef.current = poll;
    void poll().then(() => {
      if (closed || !active) return;
      if (typeof EventSource !== "undefined") {
        source = new EventSource(streamUrl(incidentId, lastSeq.current));
        source.addEventListener("timeline", (msg) => merge([JSON.parse((msg as MessageEvent).data)]));
        source.addEventListener("end", () => source?.close());
        source.onerror = () => {
          source?.close();
          source = null;
          if (!poller && !closed) poller = window.setInterval(() => void poll(), 1500);
        };
      }
      // Safety net: also poll in case SSE is buffered by an intermediary (e.g. a compressing proxy).
      poller = window.setInterval(() => void poll(), 1500);
    });

    return () => {
      closed = true;
      source?.close();
      if (poller) window.clearInterval(poller);
    };
  }, [incidentId, active]);

  // Pull immediately whenever the incident record changes (status / current node / updated_at).
  useEffect(() => {
    if (refreshKey) void pollRef.current();
  }, [refreshKey]);

  return events;
}
