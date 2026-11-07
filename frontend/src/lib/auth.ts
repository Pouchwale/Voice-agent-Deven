/**
 * Auth helpers shared by the API client, the live socket and the AuthProvider.
 * Kept dependency-free (no imports from api.ts) to avoid import cycles.
 */

export interface AuthStatus {
  authenticated: boolean;
  local: boolean;
  password_set: boolean;
}

export const LOGIN_PATH = "/login";

/** WebSocket close code the backend uses for "not authenticated". */
export const WS_POLICY_VIOLATION = 1008;

export const REMOTE_DISABLED_MESSAGE = "Remote access is disabled. Set DASHBOARD_PASSWORD in backend/.env";

type UnauthorizedHandler = (detail: string) => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** Registered by <AuthProvider>; called for any 401 outside /api/auth/* and for /ws/live closing with 1008. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

export function notifyUnauthorized(detail = ""): void {
  unauthorizedHandler?.(detail);
}

export function isLoginPath(pathname: string): boolean {
  return pathname === LOGIN_PATH || pathname.startsWith(`${LOGIN_PATH}/`);
}

/**
 * Validates a `?next=` value: only same-origin, relative paths are allowed (no open redirect).
 * Anything else - absolute URLs, protocol-relative `//host`, backslash tricks, control characters,
 * or the login page itself - falls back to "/".
 */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw || typeof window === "undefined") return "/";
  if (/[\u0000-\u001F\u007F]/.test(raw)) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return "/";
    if (isLoginPath(url.pathname)) return "/";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}

/** Login URL that returns to the current page afterwards. */
export function loginUrlForCurrentPage(): string {
  if (typeof window === "undefined") return LOGIN_PATH;
  const next = `${window.location.pathname}${window.location.search}`;
  return next && next !== "/" ? `${LOGIN_PATH}?next=${encodeURIComponent(next)}` : LOGIN_PATH;
}
