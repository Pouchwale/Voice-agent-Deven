"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createAudioContext, pcm16ToFloat32, PcmPlayer } from "@/lib/audio";
import { wsUrl } from "@/lib/config";

export type ListenStatus = "connecting" | "live" | "error";

interface Session {
  uuid: string;
  ws: WebSocket;
  ctx: AudioContext;
  master: GainNode;
  customer: PcmPlayer;
  agent: PcmPlayer;
  sampleRate: number;
}

/**
 * Listen in to one live call over WS /ws/listen/{uuid}.
 * Binary frames: byte 0 = track (0 customer, 1 agent), bytes 1.. = PCM16 LE mono.
 * Each track is scheduled sequentially on its own playhead; {"type":"clear"} drops queued agent audio.
 * Only one session at a time - starting a new one stops the previous.
 */
export function useListenIn() {
  const session = useRef<Session | null>(null);
  const [uuid, setUuid] = useState<string | null>(null);
  const [status, setStatus] = useState<ListenStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);

  const teardown = useCallback((s: Session | null) => {
    if (!s) return;
    s.ws.onopen = null;
    s.ws.onmessage = null;
    s.ws.onerror = null;
    s.ws.onclose = null;
    try {
      s.ws.close();
    } catch {
      /* ignore */
    }
    s.customer.dispose();
    s.agent.dispose();
    try {
      s.master.disconnect();
    } catch {
      /* ignore */
    }
    void s.ctx.close().catch(() => {});
  }, []);

  const stop = useCallback(() => {
    teardown(session.current);
    session.current = null;
    setUuid(null);
    setError(null);
  }, [teardown]);

  /** Must be called from a click handler so the AudioContext may start. */
  const start = useCallback(
    (callUuid: string) => {
      teardown(session.current);
      session.current = null;
      setError(null);
      setStatus("connecting");
      setUuid(callUuid);

      let ctx: AudioContext;
      try {
        ctx = createAudioContext();
      } catch (err) {
        setStatus("error");
        setError(err instanceof Error ? err.message : "Audio is not available in this browser.");
        return;
      }
      void ctx.resume().catch(() => {});
      const master = ctx.createGain();
      master.gain.value = muted ? 0 : 1;
      master.connect(ctx.destination);
      // Customer audio is real time: give it a slightly larger jitter buffer. Agent audio arrives in bursts.
      const customer = new PcmPlayer(ctx, { destination: master, leadTime: 0.12 });
      const agent = new PcmPlayer(ctx, { destination: master, leadTime: 0.06 });

      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl(`/ws/listen/${encodeURIComponent(callUuid)}`));
      } catch {
        customer.dispose();
        agent.dispose();
        void ctx.close().catch(() => {});
        setStatus("error");
        setError("Couldn't open the listen-in connection.");
        return;
      }
      ws.binaryType = "arraybuffer";
      const s: Session = { uuid: callUuid, ws, ctx, master, customer, agent, sampleRate: 8000 };
      session.current = s;

      ws.onopen = () => {
        if (session.current === s) setStatus("live");
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (session.current !== s) return;
        if (typeof ev.data === "string") {
          let msg: unknown;
          try {
            msg = JSON.parse(ev.data);
          } catch {
            return;
          }
          if (!msg || typeof msg !== "object") return;
          const m = msg as { type?: string; sample_rate?: number };
          if (m.type === "format" && typeof m.sample_rate === "number" && m.sample_rate > 0) {
            s.sampleRate = m.sample_rate;
          } else if (m.type === "clear") {
            s.agent.clear();
          }
          return;
        }
        if (!(ev.data instanceof ArrayBuffer) || ev.data.byteLength < 3) return;
        const track = new Uint8Array(ev.data, 0, 1)[0];
        const samples = pcm16ToFloat32(ev.data, 1);
        (track === 1 ? s.agent : s.customer).enqueue(samples, s.sampleRate);
      };
      ws.onerror = () => {
        if (session.current !== s) return;
        setStatus("error");
        setError("Listen-in connection failed.");
      };
      ws.onclose = () => {
        if (session.current !== s) return;
        // Call ended or server closed: release audio, keep a short note if it never connected.
        teardown(s);
        session.current = null;
        setUuid(null);
      };
    },
    [teardown, muted],
  );

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m;
      const s = session.current;
      if (s) s.master.gain.setTargetAtTime(next ? 0 : 1, s.ctx.currentTime, 0.02);
      return next;
    });
  }, []);

  // Release everything on unmount.
  useEffect(
    () => () => {
      teardown(session.current);
      session.current = null;
    },
    [teardown],
  );

  return { uuid, status, error, muted, start, stop, toggleMute };
}
