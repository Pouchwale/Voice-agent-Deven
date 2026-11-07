"use client";

import { Download, Pause, Play, RotateCcw, TriangleAlert } from "lucide-react";
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";

export interface AudioPlayerHandle {
  /** Seek to a position (ms) and start playing. */
  seekTo: (ms: number, play?: boolean) => void;
}

const SPEEDS = [1, 1.5, 2] as const;

/** Brand-styled recording player: play/pause, seek, speed, download. */
export function AudioPlayer({
  src,
  fallbackDuration,
  onTime,
  ref,
}: {
  src: string;
  fallbackDuration?: number | null;
  onTime?: (ms: number, playing: boolean) => void;
  ref?: Ref<AudioPlayerHandle>;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const onTimeRef = useRef(onTime);
  useEffect(() => {
    onTimeRef.current = onTime;
  }, [onTime]);

  useImperativeHandle(
    ref,
    () => ({
      seekTo: (ms, play = true) => {
        const a = audioRef.current;
        if (!a) return;
        a.currentTime = Math.max(0, ms / 1000);
        setTime(a.currentTime);
        if (play) void a.play().catch(() => {});
      },
    }),
    [],
  );

  useEffect(() => {
    const a = audioRef.current;
    if (a) a.playbackRate = speed;
  }, [speed]);

  const total = Number.isFinite(duration) && duration > 0 ? duration : (fallbackDuration ?? 0);
  const pct = total > 0 ? Math.min(100, (time / total) * 100) : 0;

  function toggle() {
    const a = audioRef.current;
    if (!a) return;
    // AbortError = play() interrupted by a quick pause/seek, not a broken recording.
    if (a.paused) void a.play().catch((err: unknown) => !(err instanceof DOMException && err.name === "AbortError") && setError(true));
    else a.pause();
  }

  return (
    <div className="rounded-lg border border-line bg-canvas p-3">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onLoadedMetadata={(e) => {
          setDuration(e.currentTarget.duration);
          setLoading(false);
        }}
        onCanPlay={() => setLoading(false)}
        onTimeUpdate={(e) => {
          setTime(e.currentTarget.currentTime);
          onTimeRef.current?.(e.currentTarget.currentTime * 1000, !e.currentTarget.paused);
        }}
        onPlay={() => setPlaying(true)}
        onPause={() => {
          setPlaying(false);
          onTimeRef.current?.((audioRef.current?.currentTime ?? 0) * 1000, false);
        }}
        onEnded={() => setPlaying(false)}
        onError={() => {
          setError(true);
          setLoading(false);
        }}
      />
      {error ? (
        <div className="flex flex-wrap items-center gap-3 text-sm" role="alert">
          <TriangleAlert className="size-4 shrink-0 text-fg" aria-hidden />
          <span className="flex-1">The recording couldn&apos;t be loaded.</span>
          <button
            type="button"
            className="btn !h-8 border-line bg-white !px-3 text-[13px] hover:bg-canvas"
            onClick={() => {
              setError(false);
              setLoading(true);
              audioRef.current?.load();
            }}
          >
            <RotateCcw className="size-3.5" aria-hidden /> Retry
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={toggle}
            disabled={loading && !duration}
            aria-label={playing ? "Pause recording" : "Play recording"}
            className="btn !h-10 !w-10 shrink-0 !rounded-full !p-0 bg-primary text-white hover:bg-primary-hover [&_svg]:size-4 [&_svg]:fill-current"
          >
            {playing ? <Pause aria-hidden /> : <Play aria-hidden className="translate-x-px" />}
          </button>
          <div className="flex min-w-[180px] flex-1 flex-col gap-1">
            <input
              type="range"
              className="range"
              min={0}
              max={total || 0}
              step={0.1}
              value={Math.min(time, total || 0)}
              aria-label="Seek"
              aria-valuetext={`${formatDuration(time)} of ${formatDuration(total)}`}
              onChange={(e) => {
                const a = audioRef.current;
                const v = Number(e.target.value);
                if (a) a.currentTime = v;
                setTime(v);
                onTimeRef.current?.(v * 1000, !(a?.paused ?? true));
              }}
              style={{ ["--fill" as string]: `${pct}%` }}
            />
            <div className="tabular flex justify-between text-xs text-muted">
              <span>{formatDuration(time)}</span>
              <span>{loading && !total ? "Loading…" : formatDuration(total)}</span>
            </div>
          </div>
          <div className="flex items-center gap-0.5 rounded-md border border-line bg-white p-0.5" role="radiogroup" aria-label="Playback speed">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={speed === s}
                onClick={() => setSpeed(s)}
                className={cn(
                  "tabular rounded px-2 py-0.5 text-xs font-medium",
                  speed === s ? "bg-primary-tint text-primary" : "text-muted hover:bg-canvas",
                )}
              >
                {s}x
              </button>
            ))}
          </div>
          {/* `download` is ignored cross-origin (API on another port), so open it in a new tab
              instead of navigating this page away to a bare WAV. */}
          <a
            href={src}
            download
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex size-8 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg [&_svg]:size-4"
            aria-label="Download recording"
            title="Download recording (WAV)"
          >
            <Download aria-hidden />
          </a>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">Stereo recording: customer on the left channel, agent on the right.</p>
    </div>
  );
}
