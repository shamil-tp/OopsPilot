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
  listCicdEvents,
  listEvents,
} from "@/lib/api";
import { WS_URL } from "@/lib/config";
import { TERMINAL } from "@/lib/format";
import type {
  AgentEvent,
  CicdEvent,
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
  /** The service's GitHub CI/CD events from 2 h before detection onwards. */
  cicd: CicdEvent[];
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
  cicd: [],
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
  "cicd_event_recorded",
  "deployment_detected",
  "error",
]);
// Same window as the investigation's CI/CD evidence (backend app/agents/evidence.py): the 2 h
// before detection, so an old incident never shows later deployments.
const CICD_LOOKBACK_MS = 2 * 60 * 60 * 1000;
const POLL_MS = 3000;
const RECONNECT_MS = 5000;

/** Pure read of every stored resource of the incident (no state updates). */
async function fetchIncidentData(incidentId: number): Promise<IncidentData> {
  const [incident, events] = await Promise.all([getIncident(incidentId), listEvents(incidentId)]);
  // Only ask for a phase's stored result once the timeline shows that phase ran, so an incident
  // that is early in its lifecycle does not produce expected 404/409 responses (and browser
  // console errors) on every refresh.
  const agents = new Set(events.map((e) => e.agent));
  const types = new Set(events.map((e) => e.event_type));
  const when = <T,>(ran: boolean, load: () => Promise<T | null>) => (ran ? load() : Promise.resolve(null));
  const [investigation, analysis, remediation, execution, verification, report, cicd] = await Promise.all([
    when(agents.has("investigation"), () => getInvestigation(incidentId)),
    when(agents.has("root_cause"), () => getAnalysis(incidentId)),
    when(agents.has("remediation"), () => getRemediation(incidentId)),
    when(types.has("remediation_started"), () => getExecution(incidentId)),
    when(agents.has("verification"), () => getVerification(incidentId)),
    when(types.has("verification_completed"), () => getReport(incidentId)),
    listCicdEvents({
      service: incident.service_name,
      since: new Date(Date.parse(incident.created_at) - CICD_LOOKBACK_MS).toISOString(),
      until: incident.created_at,
      limit: 10,
    }),
  ]);
  return { incident, events, investigation, analysis, remediation, execution, verification, report, cicd };
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
