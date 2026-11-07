import { WS_POLICY_VIOLATION } from "./auth";
import { wsUrl } from "./config";

export type SocketStatus = "connecting" | "open" | "closed";

export interface ReconnectingSocketOptions {
  /** Path under the WS base, e.g. "/ws/live". */
  path: string;
  /** Called for every text frame that parses as JSON. */
  onJson?: (data: unknown) => void;
  /** Called for every binary frame. */
  onBinary?: (data: ArrayBuffer) => void;
  onStatus?: (status: SocketStatus) => void;
  /** Called after each (re)connect - e.g. to re-send a subscribe message. */
  onOpen?: (socket: WebSocket) => void;
  /** Server closed with 1008 (not authenticated). Reconnecting stops; the caller decides what to do. */
  onAuthFailure?: () => void;
  /** Send {"type":"ping"} at this interval (ms). 0 disables. */
  pingInterval?: number;
  /** Max backoff delay in ms. */
  maxDelay?: number;
  /** Stop retrying after this many consecutive failures (Infinity = never). */
  maxRetries?: number;
}

/**
 * Small WebSocket wrapper with exponential backoff + jitter reconnects.
 * Safe to construct during SSR (it only connects when `connect()` is called in the browser).
 */
export class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private retries = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private stopped = true;
  private opts: Required<Omit<ReconnectingSocketOptions, "onJson" | "onBinary" | "onStatus" | "onOpen" | "onAuthFailure">> &
    ReconnectingSocketOptions;
  status: SocketStatus = "closed";

  constructor(opts: ReconnectingSocketOptions) {
    this.opts = { pingInterval: 25_000, maxDelay: 15_000, maxRetries: Infinity, ...opts };
  }

  connect(): void {
    if (typeof window === "undefined") return;
    this.stopped = false;
    this.open();
  }

  /** Close for good (no reconnect). */
  close(): void {
    this.stopped = true;
    this.clearTimers();
    this.detach();
    this.setStatus("closed");
  }

  /** Force an immediate reconnect attempt (resets backoff). */
  reconnectNow(): void {
    if (this.stopped) return;
    this.retries = 0;
    if (this.ws?.readyState === WebSocket.OPEN) return;
    this.clearTimers();
    this.open();
  }

  /** Drop the current socket without triggering a reconnect (its handlers are removed first). */
  private detach() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    ws.onopen = null;
    try {
      ws.close();
    } catch {
      /* ignore */
    }
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data);
      return true;
    }
    return false;
  }

  sendJson(data: unknown): boolean {
    return this.send(JSON.stringify(data));
  }

  private setStatus(status: SocketStatus) {
    if (this.status === status) return;
    this.status = status;
    this.opts.onStatus?.(status);
  }

  private clearTimers() {
    if (this.timer) clearTimeout(this.timer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.timer = null;
    this.pingTimer = null;
  }

  private open() {
    if (this.stopped) return;
    // Never keep two sockets: a manual reconnect while one is still connecting would otherwise
    // leave the old one alive, delivering duplicate events and clobbering `this.ws` when it closes.
    this.detach();
    this.setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl(this.opts.path));
    } catch {
      this.scheduleReconnect();
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.retries = 0;
      this.setStatus("open");
      this.opts.onOpen?.(ws);
      if (this.pingTimer) clearInterval(this.pingTimer);
      if (this.opts.pingInterval > 0) {
        this.pingTimer = setInterval(() => this.sendJson({ type: "ping" }), this.opts.pingInterval);
      }
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws) return;
      if (typeof ev.data === "string") {
        let parsed: unknown;
        try {
          parsed = JSON.parse(ev.data);
        } catch {
          return;
        }
        this.opts.onJson?.(parsed);
      } else if (ev.data instanceof ArrayBuffer) {
        this.opts.onBinary?.(ev.data);
      }
    };
    ws.onerror = () => {
      /* onclose follows; reconnect handled there */
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return;
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = null;
      this.ws = null;
      this.setStatus("closed");
      if (ev.code === WS_POLICY_VIOLATION) {
        // Not authenticated: retrying would only loop. Stop for good and report it.
        this.stopped = true;
        this.clearTimers();
        this.opts.onAuthFailure?.();
        return;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    if (this.retries >= this.opts.maxRetries) return;
    const base = Math.min(this.opts.maxDelay, 1000 * 2 ** this.retries);
    const delay = base / 2 + Math.random() * (base / 2);
    this.retries += 1;
    this.timer = setTimeout(() => this.open(), delay);
  }
}
