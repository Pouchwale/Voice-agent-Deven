import { notifyUnauthorized, type AuthStatus } from "./auth";
import { apiUrl } from "./config";
import type {
  AgentSettings,
  Campaign,
  CampaignCreate,
  CampaignCsvCreate,
  CampaignDetail,
  CampaignFromCsv,
  CampaignResultFilter,
  CsvPreview,
  CallDetail,
  CallListItem,
  CallListParams,
  DashboardStats,
  ImportResult,
  InboundSetup,
  InboundSummary,
  Lead,
  LeadTimeline,
  LeadCreate,
  LeadListParams,
  LeadPatch,
  LeadWithCalls,
  Page,
  Analytics,
  AnalyticsParams,
  BoardResponse,
  PreviewVoiceBody,
  SystemInfo,
  TestProvidersResult,
  Voice,
} from "./types";

/** Error thrown for non-2xx responses and network failures. `status` 0 = network/offline. */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

/** Builds "?a=1&b=2", skipping empty values. Returns "" when nothing is set. */
export function qs(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** FastAPI errors look like {detail: "..."} or {detail: [{msg, loc}]}. */
function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          if (d && typeof d === "object" && "msg" in d) {
            const loc = "loc" in d && Array.isArray(d.loc) ? `${d.loc.slice(1).join(".")}: ` : "";
            return `${loc}${String((d as { msg: unknown }).msg)}`;
          }
          return String(d);
        })
        .join("; ");
    }
  }
  return fallback;
}

function statusFallback(status: number): string {
  if (status === 401) return "Please sign in to continue.";
  if (status === 403) return "This request was blocked (origin not allowed).";
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (status === 404) return "Not found.";
  if (status === 409) return "That already exists.";
  if (status === 422) return "Some fields are invalid.";
  if (status >= 500) return "The server hit an error. Please try again.";
  return `Request failed (${status}).`;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  /** "json" (default), "blob" or "text" */
  as?: "json" | "blob" | "text";
  /** Give up after this many ms (0 = never). A hung backend must surface as an error, not an endless skeleton. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const isAbort = (err: unknown) => err instanceof DOMException && err.name === "AbortError";

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", query, body, signal, as = "json", timeoutMs = DEFAULT_TIMEOUT_MS } = opts;
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (as === "json") headers.Accept = "application/json";

  // One controller for the caller's signal and the timeout; it also covers reading the body.
  const ctrl = new AbortController();
  let timedOut = false;
  const timer =
    timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          ctrl.abort();
        }, timeoutMs)
      : null;
  const relay = () => ctrl.abort();
  if (signal?.aborted) ctrl.abort();
  else signal?.addEventListener("abort", relay, { once: true });
  const timeoutError = () => new ApiError(0, "The backend took too long to respond. Check that it is running and try again.");

  try {
    let res: Response;
    try {
      // credentials: the backend session cookie (remote use) must travel with every request.
      res = await fetch(apiUrl(path) + qs(query), { method, headers, body: payload, signal: ctrl.signal, credentials: "include" });
    } catch (err) {
      if (timedOut) throw timeoutError();
      if (isAbort(err)) throw err;
      throw new ApiError(0, "Can't reach the backend. Check that it is running and try again.");
    }

    if (!res.ok) {
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        /* non-JSON error body */
      }
      const detail = extractDetail(parsed, statusFallback(res.status));
      // Session missing/expired: let the AuthProvider send the user to /login. /api/auth/* handles its own 401s
      // (e.g. a wrong password on the login form must not trigger a redirect).
      if (res.status === 401 && !path.startsWith("/api/auth/")) notifyUnauthorized(detail);
      throw new ApiError(res.status, detail);
    }

    try {
      if (as === "blob") return (await res.blob()) as T;
      if (as === "text") return (await res.text()) as T;
      if (res.status === 204) return undefined as T;
      return (await res.json()) as T;
    } catch (err) {
      if (timedOut) throw timeoutError();
      if (isAbort(err)) throw err;
      // Empty / truncated / non-JSON 200 body (proxy error page, backend crash mid-response).
      throw new ApiError(res.status, "The backend sent an unreadable response. Please try again.");
    }
  } finally {
    if (timer) clearTimeout(timer);
    signal?.removeEventListener("abort", relay);
  }
}

/** SWR fetcher: the key is the API path (already including its query string). The health probe fails fast. */
export const fetcher = <T,>(path: string): Promise<T> =>
  request<T>(path, path === keys.health ? { timeoutMs: 8000 } : undefined);

/** Human message for any thrown value. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

// ---------------------------------------------------------------------------
// SWR keys (paths). Keeping them here means mutate() calls stay consistent.
// ---------------------------------------------------------------------------

export const keys = {
  health: "/api/health",
  authStatus: "/api/auth/status",
  system: "/api/settings/system",
  stats: (days: number) => `/api/dashboard/stats${qs({ days })}`,
  leads: (p: LeadListParams) =>
    `/api/leads${qs({
      search: p.search,
      status: p.status,
      interest: p.interest,
      stage: p.stage,
      page: p.page ?? 1,
      page_size: p.page_size ?? 50,
      sort: p.sort ?? "-created_at",
    })}`,
  lead: (id: number) => `/api/leads/${id}`,
  calls: (p: CallListParams) =>
    `/api/calls${qs({
      search: p.search,
      status: p.status,
      interest: p.interest,
      channel: p.channel,
      direction: p.direction,
      caller_type: p.caller_type,
      outcome: p.outcome,
      follow_up: p.follow_up,
      from: p.from,
      to: p.to,
      lead_id: p.lead_id,
      page: p.page ?? 1,
      page_size: p.page_size ?? 50,
    })}`,
  activeCalls: "/api/calls/active",
  leadTimeline: (id: number) => `/api/leads/${id}/timeline`,
  inboundSummary: (p: { from?: string; to?: string } = {}) => `/api/inbound/summary${qs({ from: p.from, to: p.to })}`,
  inboundSetup: "/api/inbound/setup",
  call: (uuid: string) => `/api/calls/${encodeURIComponent(uuid)}`,
  campaigns: "/api/campaigns",
  campaign: (id: number) => `/api/campaigns/${id}`,
  campaignItems: (id: number, p: { result?: CampaignResultFilter | ""; page?: number; page_size?: number } = {}) =>
    `/api/campaigns/${id}${qs({ result: p.result, page: p.page ?? 1, page_size: p.page_size ?? 50 })}`,
  settings: "/api/settings",
  voices: "/api/settings/voices",
  board: (search: string, limit = 50) => `/api/leads/board${qs({ search, limit })}`,
  analytics: (p: AnalyticsParams) =>
    `/api/analytics${qs({ from: p.from, to: p.to, campaign_id: p.campaign_id, include_tests: p.include_tests ? "true" : undefined })}`,
} as const;

// ---------------------------------------------------------------------------
// Typed endpoint helpers
// ---------------------------------------------------------------------------

export const api = {
  health: () => request<{ status: string }>(keys.health),

  auth: {
    status: () => request<AuthStatus>(keys.authStatus, { timeoutMs: 10_000 }),
    login: (password: string) => request<{ ok: true }>("/api/auth/login", { method: "POST", body: { password }, timeoutMs: 15_000 }),
    logout: () => request<{ ok: true }>("/api/auth/logout", { method: "POST", timeoutMs: 10_000 }),
  },
  system: () => request<SystemInfo>(keys.system),
  stats: (days = 14) => request<DashboardStats>(keys.stats(days)),

  leads: {
    list: (p: LeadListParams) => request<Page<Lead>>(keys.leads(p)),
    get: (id: number) => request<LeadWithCalls>(keys.lead(id)),
    create: (body: LeadCreate) => request<Lead>("/api/leads", { method: "POST", body }),
    update: (id: number, body: LeadPatch) => request<Lead>(keys.lead(id), { method: "PATCH", body }),
    remove: (id: number) => request<{ ok: true }>(keys.lead(id), { method: "DELETE" }),
    bulkDelete: (ids: number[]) =>
      request<{ deleted: number }>("/api/leads/bulk-delete", { method: "POST", body: { ids }, timeoutMs: 60_000 }),
    import: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<ImportResult>("/api/leads/import", { method: "POST", body: form, timeoutMs: 120_000 });
    },
    exportUrl: (q: { interest?: string; status?: string } = {}) => apiUrl(`/api/leads/export${qs(q)}`),
    board: (search = "", limit = 50) => request<BoardResponse>(keys.board(search, limit)),
    templateUrl: () => apiUrl("/api/leads/template"),
    timeline: (id: number) => request<LeadTimeline>(keys.leadTimeline(id)),
  },

  inbound: {
    summary: (p: { from?: string; to?: string } = {}) => request<InboundSummary>(keys.inboundSummary(p)),
    /** Reads the Vobiz application / number status (live Vobiz check, cached ~60 s server-side; check=false skips it). */
    setup: (check = true) => request<InboundSetup>(`${keys.inboundSetup}${check ? "" : "?check=false"}`, { timeoutMs: 30_000 }),
    /**
     * Creates/updates our Vobiz application (answer + hangup URLs) and attaches our number to it.
     * 409 when the number is currently routed somewhere else, unless force=true (owner confirmed the takeover).
     */
    connect: (force = false) =>
      request<InboundSetup>(`${keys.inboundSetup}${force ? "?force=true" : ""}`, { method: "POST", timeoutMs: 45_000 }),
    /** Detaches our number from our application (re-attaches the previous application when we know it). */
    disconnect: () => request<InboundSetup>(keys.inboundSetup, { method: "DELETE", timeoutMs: 45_000 }),
  },

  calls: {
    start: (lead_id: number) => request<CallListItem>("/api/calls", { method: "POST", body: { lead_id } }),
    list: (p: CallListParams) => request<Page<CallListItem>>(keys.calls(p)),
    active: () => request<CallListItem[]>(keys.activeCalls),
    get: (uuid: string) => request<CallDetail>(keys.call(uuid)),
    hangup: (uuid: string) => request<{ ok: true }>(`${keys.call(uuid)}/hangup`, { method: "POST" }),
    reanalyze: (uuid: string) => request<CallDetail>(`${keys.call(uuid)}/reanalyze`, { method: "POST", timeoutMs: 90_000 }),
    remove: (uuid: string) => request<{ ok: true }>(keys.call(uuid), { method: "DELETE" }),
    recordingUrl: (relative: string) => apiUrl(relative),
  },

  analytics: (p: AnalyticsParams) => request<Analytics>(keys.analytics(p)),

  /** Report download URLs (plain links; the backend sends Content-Disposition: attachment). */
  reports: {
    summaryPdf: (p: AnalyticsParams & { include_transcripts?: boolean }) =>
      apiUrl(
        `/api/reports/summary.pdf${qs({
          from: p.from,
          to: p.to,
          campaign_id: p.campaign_id,
          include_transcripts: p.include_transcripts ? "true" : "false",
          include_tests: p.include_tests ? "true" : undefined,
        })}`,
      ),
    callsCsv: (p: AnalyticsParams & { include_transcripts?: boolean }) =>
      apiUrl(
        `/api/reports/calls.csv${qs({
          from: p.from,
          to: p.to,
          campaign_id: p.campaign_id,
          include_transcripts: p.include_transcripts ? "true" : "false",
          include_tests: p.include_tests ? "true" : undefined,
        })}`,
      ),
    leadsCsv: (p: { from?: string; to?: string; stage?: string }) => apiUrl(`/api/reports/leads.csv${qs(p)}`),
  },

  campaigns: {
    list: () => request<Campaign[]>(keys.campaigns),
    get: (id: number) => request<CampaignDetail>(keys.campaign(id)),
    create: (body: CampaignCreate) => request<Campaign>("/api/campaigns", { method: "POST", body, timeoutMs: 60_000 }),
    /** Read-only check of a CSV: every number with its verdict. Nothing is saved. */
    previewCsv: (file: File, signal?: AbortSignal) => {
      const form = new FormData();
      form.append("file", file);
      return request<CsvPreview>("/api/campaigns/csv/preview", { method: "POST", body: form, signal, timeoutMs: 120_000 });
    },
    /** Creates the campaign (and any new leads) from the CSV in one step. */
    createFromCsv: (file: File, settings: CampaignCsvCreate) => {
      const form = new FormData();
      form.append("file", file);
      for (const [k, v] of Object.entries(settings)) if (v !== undefined) form.append(k, String(v));
      return request<CampaignFromCsv>("/api/campaigns/csv", { method: "POST", body: form, timeoutMs: 120_000 });
    },
    templateUrl: () => apiUrl("/api/campaigns/template"),
    start: (id: number) => request<Campaign>(`${keys.campaign(id)}/start`, { method: "POST" }),
    pause: (id: number) => request<Campaign>(`${keys.campaign(id)}/pause`, { method: "POST" }),
    remove: (id: number) => request<{ ok: true }>(keys.campaign(id), { method: "DELETE" }),
  },

  settings: {
    get: () => request<AgentSettings>(keys.settings),
    update: (body: Partial<AgentSettings>) => request<AgentSettings>(keys.settings, { method: "PUT", body }),
    reset: () => request<AgentSettings>("/api/settings/reset", { method: "POST" }),
    voices: () => request<Voice[]>(keys.voices),
    previewVoice: (body: PreviewVoiceBody, signal?: AbortSignal) =>
      request<Blob>("/api/settings/preview-voice", { method: "POST", body, as: "blob", signal, timeoutMs: 60_000 }),
    testProviders: () => request<TestProvidersResult>("/api/settings/test-providers", { method: "POST", timeoutMs: 60_000 }),
  },
};
