"use client";

import { Play, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage } from "@/lib/api";
import type { AgentSettings, PreviewVoiceBody } from "@/lib/types";
import { LANGUAGES } from "./model";

export type PreviewState = "idle" | "loading" | "playing";
type VoiceOptions = Omit<PreviewVoiceBody, "text">;

/** The preview endpoint accepts at most this much text. */
const PREVIEW_MAX_CHARS = 600;

/** Stop function of whichever preview is playing anywhere on the page: a new one stops it, so voices never overlap. */
let stopPlaying: (() => void) | null = null;

/**
 * The UNSAVED voice settings of the form, so a preview plays exactly what was just picked. Values the preview
 * endpoint would reject are left out (it then uses the saved ones) instead of failing the whole preview.
 */
export function voiceOf(form: Pick<AgentSettings, "voice" | "tts_pace" | "tts_temperature" | "language_code">): VoiceOptions {
  const within = (v: number, lo: number, hi: number) => (Number.isFinite(v) && v >= lo && v <= hi ? v : undefined);
  return {
    voice: form.voice || undefined,
    pace: within(form.tts_pace, 0.5, 2),
    temperature: within(form.tts_temperature, 0.01, 1),
    language_code: LANGUAGES.some((l) => l.value === form.language_code) ? form.language_code : undefined,
  };
}

/** Long lines are cut at a word boundary to what the preview endpoint accepts. */
function previewText(text: string): string {
  const t = text.trim();
  if (t.length <= PREVIEW_MAX_CHARS) return t;
  const cut = t.slice(0, PREVIEW_MAX_CHARS);
  const space = cut.lastIndexOf(" ");
  return space > PREVIEW_MAX_CHARS * 0.6 ? cut.slice(0, space) : cut;
}

/**
 * Plays a line in the agent's voice (TTS preview). One instance can serve several Play buttons: `stateFor(id)`
 * says which line the loading / playing state belongs to.
 */
export function usePreviewVoice() {
  const toast = useToast();
  const [state, setState] = useState<PreviewState>("idle");
  const [activeId, setActiveId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Named so it can recognise itself in `stopPlaying` (useCallback keeps the first-render function).
  const stop = useCallback(function stopPreview() {
    abortRef.current?.abort();
    abortRef.current = null;
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    if (stopPlaying === stopPreview) stopPlaying = null;
    setState("idle");
    setActiveId(null);
  }, []);

  useEffect(() => stop, [stop]);

  const play = useCallback(
    async (id: string, text: string, voice: VoiceOptions) => {
      stopPlaying?.();
      stop();
      stopPlaying = stop;
      setActiveId(id);
      setState("loading");
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        // Stop / unmount aborts the TTS request too, not just the playback.
        const blob = await api.settings.previewVoice({ ...voice, text: previewText(text) }, ctrl.signal);
        if (ctrl.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => stop();
        await audio.play();
        if (ctrl.signal.aborted) return;
        setState("playing");
      } catch (err) {
        if (ctrl.signal.aborted) return;
        stop();
        toast({ tone: "error", title: "Couldn't preview the voice", description: errorMessage(err) });
      }
    },
    [stop, toast],
  );

  const stateFor = useCallback((id: string): PreviewState => (activeId === id ? state : "idle"), [activeId, state]);

  return { state, activeId, stateFor, play, stop };
}

/** Small Play / Stop button for one line. */
export function PlayButton({
  state,
  onPlay,
  onStop,
  disabled,
  label = "Play",
  what,
}: {
  state: PreviewState;
  onPlay: () => void;
  onStop: () => void;
  disabled?: boolean;
  label?: string;
  /** Screen-reader name of the line, e.g. "greeting for new callers". */
  what: string;
}) {
  return (
    <Button
      size="sm"
      variant={state === "playing" ? "primary" : "secondary"}
      icon={state === "playing" ? <Square /> : <Play />}
      loading={state === "loading"}
      disabled={disabled && state === "idle"}
      onClick={() => (state === "idle" ? onPlay() : onStop())}
      aria-label={state === "idle" ? `${label}: ${what}` : `Stop playing the ${what}`}
    >
      {state === "playing" ? "Stop" : state === "loading" ? "Generating…" : label}
    </Button>
  );
}
