/** Backend base URL (no trailing slash). */
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100").replace(/\/+$/, "");

/** WebSocket base derived from the API base: http -> ws, https -> wss. */
export const WS_BASE = API_BASE.replace(/^http(s?):\/\//i, (_m, s: string) => `ws${s}://`);

/** Absolute URL for an API path ("/api/..."). Absolute URLs pass through untouched. */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}

export function wsUrl(path: string): string {
  return `${WS_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}

export const LEADS_PAGE_SIZE = 50;
export const CALLS_PAGE_SIZE = 50;
