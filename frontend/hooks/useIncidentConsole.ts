"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  getAnalysis,
  getExecution,
  getIncident,
  getInvestigation,
  getRemediation,
  getReport,
  getVerification,
  listEvents,
} from "@/lib/api";
import { WS_URL } from "@/lib/config";
import { TERMINAL } from "@/lib/format";
import type {
  AgentEvent,
  ExecutionRun,
  Incident,
  IncidentReport,
  InvestigationRun,
  RemediationRun,
  RootCauseRun,
  StreamMessage,
  VerificationRun,
} from "@/types/api";

export type LiveMode = "connecting" | "live" | "polling";

export interface IncidentData {
  incident: Incident | null;
  events: AgentEvent[];
  investigation: InvestigationRun | null;
  analysis: RootCauseRun | null;
  remediation: RemediationRun | null;
  execution: ExecutionRun | null;
  verification: VerificationRun | null;
  report: IncidentReport | null;
}

const EMPTY: IncidentData = {
  incident: null,
  events: [],
  investigation: null,
  analysis: null,
  remediation: null,
  execution: null,
  verification: null,
  report: null,
};

// Events after which a phase result may have changed: re-read the REST resources.
const REFRESH_ON = new Set([
  "investigation_completed",
  "root_cause_identified",
  "remediation_recommended",
  "approval_received",
  "incident_escalated",
  "remediation_completed",
  "verification_completed",
  "incident_resolved",
  "report_generated",
  "error",
]);
const POLL_MS = 3000;
const RECONNECT_MS = 5000;

/** Pure read of every stored resource of the incident (no state updates). */
async function fetchIncidentData(incidentId: number): Promise<IncidentData> {
  const [incident, events, investigation, analysis, remediation, execution, verification, report] =
    await Promise.all([
      getIncident(incidentId),
      listEvents(incidentId),
      getInvestigation(incidentId),
      getAnalysis(incidentId),
      getRemediation(incidentId),
      getExecution(incidentId),
      getVerification(incidentId),
      getReport(incidentId),
    ]);
  return { incident, events, investigation, analysis, remediation, execution, verification, report };
}

function mergeEvents(current: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const seen = new Set(current.map((e) => e.id));
  const fresh = incoming.filter((e) => !seen.has(e.id));
  return fresh.length === 0 ? current : [...current, ...fresh].sort((a, b) => a.id - b.id);
}

/**
 * Incident page data: REST for the stored state, WebSocket (`/ws/incidents/{id}`) for live events.
 * If the socket is unavailable it falls back to REST polling and keeps retrying the socket.
 * The backend stays the source of truth: this hook only reads.
 */
export function useIncidentConsole(incidentId: number) {
  const [data, setData] = useState<IncidentData>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<LiveMode>("connecting");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const apply = useCallback((result: IncidentData | Error) => {
    if (result instanceof Error) {
      setError(result instanceof ApiError ? result.message : "Unexpected error while loading the incident");
    } else {
      setData((prev) => ({ ...result, events: mergeEvents(prev.events, result.events) }));
      setError(null);
    }
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    apply(await fetchIncidentData(incidentId).catch((err: unknown) => (err instanceof Error ? err : new Error(String(err)))));
  }, [incidentId, apply]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void refresh(), 300);
  }, [refresh]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let lastId = 0;
    let closed = false;

    const startPolling = () => {
      setMode("polling");
      if (!pollTimer) pollTimer = setInterval(() => void refresh(), POLL_MS);
    };
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const connect = () => {
      if (closed) return;
      setMode("connecting");
      socket = new WebSocket(`${WS_URL}/ws/incidents/${incidentId}?after=${lastId}`);
      socket.onopen = () => {
        stopPolling();
        setMode("live");
      };
      socket.onmessage = (message: MessageEvent<string>) => {
        let parsed: StreamMessage;
        try {
          parsed = JSON.parse(message.data) as StreamMessage;
        } catch {
          return;
        }
        if (parsed.type === "incident_status" && "incident_status" in parsed) {
          const status = parsed.incident_status;
          setData((prev) => (prev.incident ? { ...prev, incident: { ...prev.incident, status } } : prev));
          return;
        }
        if (parsed.type === "error" && !("id" in parsed)) return; // stream hiccup; it retries itself
        if ("id" in parsed) {
          const { type: _type, ...event } = parsed;
          void _type;
          lastId = Math.max(lastId, event.id);
          setData((prev) => ({ ...prev, events: mergeEvents(prev.events, [event]) }));
          if (REFRESH_ON.has(event.event_type)) scheduleRefresh();
        }
      };
      socket.onclose = () => {
        if (closed) return;
        startPolling();
        reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };
      socket.onerror = () => socket?.close();
    };

    // Initial load, then go live. State is only set after the awaited fetch resolves.
    fetchIncidentData(incidentId)
      .catch((err: unknown) => (err instanceof Error ? err : new Error(String(err))))
      .then((result) => {
        if (closed) return;
        apply(result);
        connect();
      });

    return () => {
      closed = true;
      socket?.close();
      stopPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [incidentId, refresh, scheduleRefresh, apply]);

  const terminal = data.incident ? TERMINAL.includes(data.incident.status) : false;
  return { data, error, loading, mode, terminal, refresh };
}
