import { API_URL } from "@/lib/config";
import type { SystemHealth } from "@/types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<{ status: number; data: T }> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Cannot reach the OpsPilot API at ${API_URL}`, null);
  }

  const data: unknown = await response.json().catch(() => null);
  return { status: response.status, data: data as T };
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const { status, data } = await request<SystemHealth>("/api/system/health");
  // 503 still carries a SystemHealth body describing which dependency is down.
  if ((status === 200 || status === 503) && data) {
    return data;
  }
  throw new ApiError(`System health check failed (HTTP ${status})`, status, data);
}
