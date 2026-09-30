// NEXT_PUBLIC_* values are inlined at build time (see Next.js environment variable docs).
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const WS_URL = API_URL.replace(/^http/, "ws");
