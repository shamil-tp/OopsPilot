"use client";

import { useEffect, useState } from "react";

import { ApiError, getSystemHealth } from "@/lib/api";
import type { SystemHealth } from "@/types/api";

const POLL_INTERVAL_MS = 10_000;

export interface SystemHealthState {
  health: SystemHealth | null;
  error: string | null;
  loading: boolean;
}

export function useSystemHealth(): SystemHealthState {
  const [state, setState] = useState<SystemHealthState>({
    health: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const health = await getSystemHealth();
        if (!cancelled) setState({ health, error: null, loading: false });
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Unexpected error";
        if (!cancelled) setState({ health: null, error: message, loading: false });
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return state;
}
