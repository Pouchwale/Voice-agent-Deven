import { isCallActive } from "./format";
import type { AgentState, Campaign, CallListItem, LiveEvent } from "./types";
import { notifyUnauthorized } from "./auth";
import { ReconnectingSocket, type SocketStatus } from "./ws";

export interface LiveBubble {
  key: string;
  role: "agent" | "user" | "system";
  text: string;
  final: boolean;
  offset_ms: number;
  interrupted?: boolean;
}

export interface LiveCall {
  call: CallListItem;
  state: AgentState | null;
  transcript: LiveBubble[];
  ended: boolean;
  endedAt: number | null;
}

export interface LiveSnapshot {
  status: SocketStatus;
  /** Has the socket delivered at least one snapshot since the page loaded? */
  synced: boolean;
  active: LiveCall[];
  recent: LiveCall[];
}

export interface Levels {
  user: number;
  agent: number;
  at: number;
}

const MAX_RECENT = 12;
const MAX_BUBBLES = 200;

let bubbleSeq = 0;

/**
 * Upserts a streaming transcript event into a bubble list:
 * the newest non-final bubble of the same role (near the end) is replaced until a final arrives.
 */
export function upsertBubble(
  list: LiveBubble[],
  ev: { role: "agent" | "user" | "system"; text: string; final: boolean; offset_ms: number; interrupted?: boolean },
): LiveBubble[] {
  const next = list.slice();
  for (let i = next.length - 1; i >= Math.max(0, next.length - 4); i--) {
    const b = next[i];
    if (b.role === ev.role && !b.final) {
      next[i] = { ...b, text: ev.text, final: ev.final, interrupted: ev.interrupted ?? b.interrupted };
      return next;
    }
  }
  // An "interrupted" flag may arrive for an already-final agent turn.
  if (ev.interrupted && ev.role === "agent") {
    for (let i = next.length - 1; i >= Math.max(0, next.length - 3); i--) {
      if (next[i].role === "agent" && next[i].text === ev.text) {
        next[i] = { ...next[i], interrupted: true };
        return next;
      }
    }
  }
  next.push({
    key: `b${++bubbleSeq}`,
    role: ev.role,
    text: ev.text,
    final: ev.final,
    offset_ms: ev.offset_ms,
    interrupted: ev.interrupted,
  });
  return next.length > MAX_BUBBLES ? next.slice(-MAX_BUBBLES) : next;
}

type Listener = () => void;

function isCall(c: unknown): c is CallListItem {
  return !!c && typeof c === "object" && typeof (c as CallListItem).uuid === "string" && typeof (c as CallListItem).status === "string";
}

/** Holds the /ws/live feed. Levels are kept outside React state (read via rAF) for performance. */
export class LiveStore {
  private calls = new Map<string, LiveCall>();
  private levels = new Map<string, Levels>();
  private listeners = new Set<Listener>();
  private campaignListeners = new Set<(c: Campaign) => void>();
  private callListeners = new Set<(c: CallListItem) => void>();
  private socket: ReconnectingSocket | null = null;
  private status: SocketStatus = "closed";
  private synced = false;
  private snapshot: LiveSnapshot = { status: "closed", synced: false, active: [], recent: [] };
  private emitScheduled = false;

  static readonly EMPTY: LiveSnapshot = { status: "closed", synced: false, active: [], recent: [] };

  connect() {
    if (this.socket) return;
    this.socket = new ReconnectingSocket({
      path: "/ws/live",
      pingInterval: 20_000,
      onStatus: (s) => {
        this.status = s;
        this.emit();
      },
      onJson: (data) => this.handle(data as LiveEvent),
      // 1008 = not signed in (remote use): stop reconnecting and send the user to /login.
      onAuthFailure: () => notifyUnauthorized("Please sign in to continue."),
    });
    this.socket.connect();
  }

  disconnect() {
    this.socket?.close();
    this.socket = null;
  }

  reconnectNow() {
    this.socket?.reconnectNow();
  }

  subscribe = (fn: Listener) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getSnapshot = () => this.snapshot;
  getServerSnapshot = () => LiveStore.EMPTY;

  onCampaign(fn: (c: Campaign) => void) {
    this.campaignListeners.add(fn);
    return () => {
      this.campaignListeners.delete(fn);
    };
  }

  onCallUpdate(fn: (c: CallListItem) => void) {
    this.callListeners.add(fn);
    return () => {
      this.callListeners.delete(fn);
    };
  }

  getLevels(uuid: string): Levels | undefined {
    return this.levels.get(uuid);
  }

  get(uuid: string): LiveCall | undefined {
    return this.calls.get(uuid);
  }

  private upsertCall(call: CallListItem): LiveCall {
    const existing = this.calls.get(call.uuid);
    const active = isCallActive(call.status);
    const ended = !active || (existing?.state === "ended" && !active);
    const lc: LiveCall = existing
      ? { ...existing, call: { ...existing.call, ...call }, ended, endedAt: ended ? (existing.endedAt ?? Date.now()) : null }
      : { call, state: null, transcript: [], ended, endedAt: ended ? Date.now() : null };
    this.calls.set(call.uuid, lc);
    return lc;
  }

  private handle(ev: LiveEvent) {
    // Malformed frames are dropped instead of throwing inside the socket handler.
    if (!ev || typeof ev !== "object") return;
    switch (ev.type) {
      case "snapshot": {
        this.synced = true;
        const seen = new Set<string>();
        for (const call of Array.isArray(ev.calls) ? ev.calls : []) {
          if (!isCall(call)) continue;
          seen.add(call.uuid);
          this.upsertCall(call);
        }
        // Anything we believed active that the server no longer lists has ended.
        for (const [uuid, lc] of this.calls) {
          if (!seen.has(uuid) && !lc.ended) {
            this.calls.set(uuid, { ...lc, ended: true, endedAt: Date.now() });
          }
        }
        break;
      }
      case "call.update": {
        if (!isCall(ev.call)) return;
        this.upsertCall(ev.call);
        this.callListeners.forEach((fn) => fn(ev.call));
        break;
      }
      case "call.state": {
        const lc = this.calls.get(ev.uuid);
        if (!lc) return;
        const ended = ev.state === "ended" || lc.ended;
        this.calls.set(ev.uuid, { ...lc, state: ev.state, ended, endedAt: ended ? (lc.endedAt ?? Date.now()) : null });
        break;
      }
      case "transcript": {
        const lc = this.calls.get(ev.uuid);
        if (!lc || typeof ev.text !== "string") return;
        this.calls.set(ev.uuid, { ...lc, transcript: upsertBubble(lc.transcript, ev) });
        break;
      }
      case "level": {
        // Only for calls we know about, so stray uuids can't grow the map forever.
        if (!this.calls.has(ev.uuid)) return;
        this.levels.set(ev.uuid, { user: Number(ev.user) || 0, agent: Number(ev.agent) || 0, at: performance.now() });
        return; // no React emit
      }
      case "campaign.update": {
        if (!ev.campaign || typeof ev.campaign !== "object") return;
        this.campaignListeners.forEach((fn) => fn(ev.campaign));
        return;
      }
      default:
        return;
    }
    this.emit();
  }

  /** Coalesce bursts of events into one React update per frame. */
  private emit() {
    if (this.emitScheduled) return;
    this.emitScheduled = true;
    const run = () => {
      this.emitScheduled = false;
      this.rebuild();
      this.listeners.forEach((fn) => fn());
    };
    if (typeof requestAnimationFrame !== "undefined" && typeof document !== "undefined" && !document.hidden) {
      requestAnimationFrame(run);
    } else {
      setTimeout(run, 0);
    }
  }

  private rebuild() {
    const active: LiveCall[] = [];
    const recent: LiveCall[] = [];
    for (const lc of this.calls.values()) (lc.ended ? recent : active).push(lc);
    const startOf = (lc: LiveCall) => lc.call.started_at ?? lc.call.created_at ?? "";
    active.sort((a, b) => startOf(a).localeCompare(startOf(b)));
    recent.sort((a, b) => (b.endedAt ?? 0) - (a.endedAt ?? 0));
    // prune old ended calls
    for (const lc of recent.slice(MAX_RECENT)) {
      this.calls.delete(lc.call.uuid);
      this.levels.delete(lc.call.uuid);
    }
    this.snapshot = { status: this.status, synced: this.synced, active, recent: recent.slice(0, MAX_RECENT) };
  }
}
