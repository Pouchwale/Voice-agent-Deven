"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createAudioContext, pcm16ToFloat32, PcmPlayer } from "@/lib/audio";
import { wsUrl } from "@/lib/config";
import { upsertBubble, type LiveBubble } from "@/lib/live-store";
import type { AgentState, BrowserCallClientMessage, BrowserCallServerMessage, CallDirection, EndReason } from "@/lib/types";

export type CallPhase = "idle" | "mic" | "connecting" | "live" | "ending" | "ended" | "error";

export interface StartOptions {
  lead_id?: number;
  name?: string;
  company?: string;
  /** "inbound" rehearses a customer calling us: inbound greeting, purpose and memory. */
  direction?: CallDirection;
}

const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  video: false,
};

function micErrorMessage(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError" || err.name === "SecurityError")
      return "Microphone permission was blocked. Allow mic access for this site in your browser, then try again.";
    if (err.name === "NotFoundError") return "No microphone found. Plug one in (headphones with a mic work best) and try again.";
    if (err.name === "NotReadableError") return "Your microphone is busy in another app. Close it there and try again.";
  }
  if (typeof window !== "undefined" && !window.isSecureContext)
    return "The microphone needs a secure page (https or localhost).";
  return err instanceof Error ? err.message : "Couldn't start the microphone.";
}

/**
 * Talk to the agent from the browser mic over WS /ws/browser-call.
 * Mic -> AudioWorklet (16 kHz PCM16, ~40 ms frames) -> binary WS frames.
 * Agent PCM16 @ output_sample_rate -> PcmPlayer (sequential scheduling, `clear` stops it instantly).
 */
export function useBrowserCall() {
  const [phase, setPhase] = useState<CallPhase>("idle");
  const [agentState, setAgentState] = useState<AgentState | null>(null);
  const [uuid, setUuid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [endReason, setEndReason] = useState<EndReason | "connection_lost" | null>(null);
  const [bubbles, setBubbles] = useState<LiveBubble[]>([]);
  const [muted, setMuted] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const outRateRef = useRef(24000);
  const readyRef = useRef(false);
  const micLevelRef = useRef(0);
  const mutedRef = useRef(false);
  const phaseRef = useRef<CallPhase>("idle");
  const forceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * Bumped by every start / cancel / reset / unmount. `start` awaits the mic prompt and the worklet load;
   * if the attempt changed meanwhile (hang-up, panel closed, second start) it must not go on to open a socket.
   */
  const attemptRef = useRef(0);

  const setPhaseBoth = useCallback((p: CallPhase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);

  /** Stop mic + audio graph + socket. */
  const teardown = useCallback(() => {
    readyRef.current = false;
    if (forceTimer.current) clearTimeout(forceTimer.current);
    forceTimer.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      try {
        if (ws.readyState === WebSocket.OPEN) ws.close(1000, "client done");
        else if (ws.readyState === WebSocket.CONNECTING) ws.close();
      } catch {
        /* ignore */
      }
    }
    if (nodeRef.current) {
      nodeRef.current.port.onmessage = null;
      try {
        nodeRef.current.disconnect();
      } catch {
        /* ignore */
      }
    }
    nodeRef.current = null;
    try {
      sourceRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    sourceRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    playerRef.current?.dispose();
    playerRef.current = null;
    const ctx = ctxRef.current;
    ctxRef.current = null;
    if (ctx && ctx.state !== "closed") void ctx.close().catch(() => {});
    micLevelRef.current = 0;
  }, []);

  const finish = useCallback(
    (reason: EndReason | "connection_lost" | null) => {
      setEndReason(reason);
      setAgentState("ended");
      setPhaseBoth("ended");
      teardown();
    },
    [setPhaseBoth, teardown],
  );

  const handleJson = useCallback(
    (msg: BrowserCallServerMessage) => {
      switch (msg.type) {
        case "ready":
          outRateRef.current = msg.output_sample_rate || 24000;
          readyRef.current = true;
          if (ctxRef.current?.state === "suspended") void ctxRef.current.resume().catch(() => {});
          setUuid(msg.uuid);
          setStartedAt(Date.now());
          setPhaseBoth("live");
          break;
        case "transcript":
          setBubbles((list) => upsertBubble(list, msg));
          break;
        case "state":
          setAgentState(msg.state);
          break;
        case "clear":
          playerRef.current?.clear();
          break;
        case "ended":
          finish(msg.reason ?? null);
          break;
        case "error":
          setError(msg.detail || "The agent reported an error.");
          if (!readyRef.current) {
            setPhaseBoth("error");
            teardown();
          }
          break;
      }
    },
    [finish, setPhaseBoth, teardown],
  );

  const start = useCallback(
    async (opts: StartOptions) => {
      const p0 = phaseRef.current;
      if (p0 === "mic" || p0 === "connecting" || p0 === "live" || p0 === "ending") return;
      teardown();
      const attempt = ++attemptRef.current;
      const stale = () => attempt !== attemptRef.current;
      setError(null);
      setEndReason(null);
      setBubbles([]);
      setUuid(null);
      setAgentState(null);
      setStartedAt(null);
      setMuted(false);
      mutedRef.current = false;
      setPhaseBoth("mic");

      // 1. microphone
      let stream: MediaStream;
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser can't access the microphone.");
        stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
      } catch (err) {
        if (stale()) return;
        setError(micErrorMessage(err));
        setPhaseBoth("error");
        return;
      }
      if (stale()) {
        // Hung up / closed while the permission prompt was open: release the mic right away.
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;

      // 2. audio graph
      try {
        if (typeof AudioWorkletNode === "undefined") {
          throw new Error("this browser doesn't support AudioWorklet. Use a recent Chrome, Edge, Firefox or Safari.");
        }
        const ctx = createAudioContext({ latencyHint: "interactive" });
        ctxRef.current = ctx;
        // Don't block on resume(): some browsers keep it pending without a fresh user gesture.
        if (ctx.state === "suspended") void ctx.resume().catch(() => {});
        await ctx.audioWorklet.addModule("/pcm-capture-worklet.js");
        if (stale()) return; // teardown() already released the stream and closed this context
        const source = ctx.createMediaStreamSource(stream);
        const node = new AudioWorkletNode(ctx, "pcm-capture", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
          processorOptions: { targetSampleRate: 16000, frameMs: 40 },
        });
        // keep the worklet pulled by the graph without playing the mic back
        const sink = ctx.createGain();
        sink.gain.value = 0;
        source.connect(node);
        node.connect(sink);
        sink.connect(ctx.destination);
        node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
          micLevelRef.current = e.data.level;
          const ws = wsRef.current;
          if (readyRef.current && ws && ws.readyState === WebSocket.OPEN) ws.send(e.data.pcm);
        };
        sourceRef.current = source;
        nodeRef.current = node;
        playerRef.current = new PcmPlayer(ctx, { leadTime: 0.05 });
      } catch (err) {
        if (stale()) return;
        setError(err instanceof Error ? `Audio setup failed: ${err.message}` : "Audio setup failed.");
        setPhaseBoth("error");
        teardown();
        return;
      }

      // 3. socket
      setPhaseBoth("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl("/ws/browser-call"));
      } catch {
        setError("Couldn't open a connection to the agent.");
        setPhaseBoth("error");
        teardown();
        return;
      }
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => {
        const payload: Extract<BrowserCallClientMessage, { type: "start" }> = { type: "start" };
        if (opts.lead_id) payload.lead_id = opts.lead_id;
        if (opts.name?.trim()) payload.name = opts.name.trim();
        if (opts.company?.trim()) payload.company = opts.company.trim();
        // Only sent for inbound, so the outbound start message stays exactly as before.
        if (opts.direction === "inbound") payload.direction = "inbound";
        ws.send(JSON.stringify(payload));
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (typeof ev.data === "string") {
          try {
            handleJson(JSON.parse(ev.data) as BrowserCallServerMessage);
          } catch {
            /* ignore malformed */
          }
        } else if (ev.data instanceof ArrayBuffer && playerRef.current) {
          playerRef.current.enqueue(pcm16ToFloat32(ev.data), outRateRef.current);
        }
      };
      ws.onerror = () => {
        if (!readyRef.current) setError("Couldn't connect to the agent. Is the backend running?");
      };
      ws.onclose = (ev: CloseEvent) => {
        const p = phaseRef.current;
        if (p === "live" || p === "ending") finish(p === "ending" ? "manual_hangup" : "connection_lost");
        else if (p === "connecting") {
          setError((e) => e ?? (ev.reason || "The agent closed the connection before the call started."));
          setPhaseBoth("error");
          teardown();
        }
      };
    },
    [finish, handleJson, setPhaseBoth, teardown],
  );

  const hangup = useCallback(() => {
    const ws = wsRef.current;
    const p = phaseRef.current;
    if (p === "connecting" || p === "mic") {
      attemptRef.current += 1; // cancels a start() still waiting on the mic prompt / worklet
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "hangup" }));
        } catch {
          /* ignore */
        }
      }
      setPhaseBoth("idle");
      teardown();
      return;
    }
    if (p !== "live") return; // already ending / ended: a second click must not stack force timers
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      finish("manual_hangup");
      return;
    }
    ws.send(JSON.stringify({ type: "hangup" }));
    playerRef.current?.clear();
    setPhaseBoth("ending");
    // don't wait forever for the server's "ended"
    if (forceTimer.current) clearTimeout(forceTimer.current);
    forceTimer.current = setTimeout(() => {
      if (phaseRef.current === "ending") finish("manual_hangup");
    }, 4000);
  }, [finish, setPhaseBoth, teardown]);

  const toggleMute = useCallback(() => {
    // Side effect outside the state updater (updaters may run twice in StrictMode).
    const next = !mutedRef.current;
    mutedRef.current = next;
    nodeRef.current?.port.postMessage({ type: "mute", muted: next });
    setMuted(next);
  }, []);

  const reset = useCallback(() => {
    attemptRef.current += 1;
    teardown();
    setPhaseBoth("idle");
    setError(null);
    setEndReason(null);
    setBubbles([]);
    setUuid(null);
    setAgentState(null);
    setStartedAt(null);
  }, [setPhaseBoth, teardown]);

  /** Combined level for the orb: agent playback level, or the mic level while the customer talks. */
  const getLevel = useCallback(() => {
    const agent = playerRef.current?.level() ?? 0;
    const mic = Math.min(1, micLevelRef.current * 5);
    return Math.max(agent, mic);
  }, []);

  const isAgentAudible = useCallback(() => playerRef.current?.playing ?? false, []);

  // On unmount: politely hang up, then tear everything down (and cancel a start() still in progress).
  useEffect(
    () => () => {
      attemptRef.current += 1;
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "hangup" }));
        } catch {
          /* ignore */
        }
      }
      teardown();
    },
    [teardown],
  );

  return {
    phase,
    agentState,
    uuid,
    error,
    endReason,
    bubbles,
    muted,
    startedAt,
    start,
    hangup,
    toggleMute,
    reset,
    getLevel,
    isAgentAudible,
  };
}
